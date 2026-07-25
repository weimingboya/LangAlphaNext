from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime
from typing import Any, Literal
from urllib.parse import quote

import httpx
from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from langalpha.agent.context import RunContext
from langalpha.capabilities.gateway import gateway
from langalpha.config import get_settings

_BASE_URL = "https://api.massive.com"
_SYMBOL = re.compile(r"^[A-Z0-9.^=-]{1,32}$")


class PublicRuntimeInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: ToolRuntime[RunContext, object]


class ResolveInstrumentInput(PublicRuntimeInput):
    query: str = Field(min_length=1, max_length=120)
    max_results: int = Field(default=8, ge=1, le=25)


class SymbolsInput(PublicRuntimeInput):
    symbols: list[str] = Field(min_length=1, max_length=20)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().upper() for value in values))
        if any(not _SYMBOL.fullmatch(value) for value in normalized):
            raise ValueError("symbols contain unsupported characters")
        return normalized


class BarsInput(PublicRuntimeInput):
    symbol: str
    start_date: date
    end_date: date
    multiplier: int = Field(default=1, ge=1, le=60)
    timespan: Literal["minute", "hour", "day", "week", "month"] = "day"
    adjusted: bool = True
    limit: int = Field(default=5_000, ge=1, le=50_000)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not _SYMBOL.fullmatch(normalized):
            raise ValueError("symbol contains unsupported characters")
        return normalized


class CorporateActionsInput(PublicRuntimeInput):
    symbol: str
    start_date: date | None = None
    end_date: date | None = None
    limit: int = Field(default=100, ge=1, le=1_000)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not _SYMBOL.fullmatch(normalized):
            raise ValueError("symbol contains unsupported characters")
        return normalized


def _envelope(records: list[dict[str, Any]], *, source: str) -> str:
    return json.dumps(
        {
            "records": records,
            "provider": "Massive",
            "source": source,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "notice": "Market data may be delayed; verify before making decisions.",
        },
        ensure_ascii=False,
    )


async def _massive_get(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, Any],
) -> dict[str, Any]:
    response = await client.get(path, params=params)
    if response.status_code >= 400:
        raise RuntimeError(f"Massive request failed with HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Massive returned an invalid response")
    if str(payload.get("status", "")).upper() in {"ERROR", "NOT_AUTHORIZED"}:
        raise RuntimeError("Massive rejected the market data request")
    return payload


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_BASE_URL,
        timeout=httpx.Timeout(25, connect=8),
        headers={
            "Authorization": f"Bearer {get_settings().require_massive_key()}",
            "User-Agent": "LangAlphaNext/0.1",
            "Accept": "application/json",
        },
    )


@tool(args_schema=ResolveInstrumentInput)
async def market_resolve_instrument(
    query: str,
    runtime: ToolRuntime[RunContext, object],
    max_results: int = 8,
) -> str:
    """Resolve a company or symbol to active US stock ticker metadata."""
    gateway.admit_runtime("market.resolve_instrument", runtime)
    params = {
        "search": query,
        "active": "true",
        "market": "stocks",
        "limit": max_results,
        "sort": "ticker",
        "order": "asc",
    }
    async with _client() as client:
        payload = await _massive_get(client, "/v3/reference/tickers", params=params)
    records = payload.get("results")
    return _envelope(records if isinstance(records, list) else [], source="/v3/reference/tickers")


@tool(args_schema=SymbolsInput)
async def market_get_snapshots(
    symbols: list[str],
    runtime: ToolRuntime[RunContext, object],
) -> str:
    """Get current consolidated stock snapshots for up to 20 resolved symbols."""
    gateway.admit_runtime("market.snapshots", runtime)
    params = {
        "ticker.any_of": ",".join(symbols),
        "type": "stocks",
        "limit": len(symbols),
    }
    async with _client() as client:
        payload = await _massive_get(client, "/v3/snapshot", params=params)
    records = payload.get("results")
    return _envelope(records if isinstance(records, list) else [], source="/v3/snapshot")


@tool(args_schema=BarsInput)
async def market_get_bars(
    symbol: str,
    start_date: date,
    end_date: date,
    runtime: ToolRuntime[RunContext, object],
    multiplier: int = 1,
    timespan: Literal["minute", "hour", "day", "week", "month"] = "day",
    adjusted: bool = True,
    limit: int = 5_000,
) -> str:
    """Get reproducible OHLCV aggregate bars for a resolved market symbol."""
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    gateway.admit_runtime("market.bars", runtime)
    path = (
        f"/v2/aggs/ticker/{quote(symbol, safe='')}/range/{multiplier}/{timespan}/"
        f"{start_date.isoformat()}/{end_date.isoformat()}"
    )
    async with _client() as client:
        payload = await _massive_get(
            client,
            path,
            params={"adjusted": str(adjusted).lower(), "sort": "asc", "limit": limit},
        )
    rows = payload.get("results")
    records = []
    for row in rows if isinstance(rows, list) else []:
        timestamp = row.get("t")
        records.append(
            {
                "symbol": symbol,
                "timestamp": (
                    datetime.fromtimestamp(timestamp / 1_000, UTC).isoformat()
                    if isinstance(timestamp, (int, float))
                    else None
                ),
                "open": row.get("o"),
                "high": row.get("h"),
                "low": row.get("l"),
                "close": row.get("c"),
                "volume": row.get("v"),
                "volume_weighted_price": row.get("vw"),
                "transactions": row.get("n"),
            }
        )
    return _envelope(records, source=path)


@tool(args_schema=CorporateActionsInput)
async def market_get_corporate_actions(
    symbol: str,
    runtime: ToolRuntime[RunContext, object],
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 100,
) -> str:
    """Get dividends and stock splits for a resolved market symbol."""
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    gateway.admit_runtime("market.corporate_actions", runtime)
    dividend_params: dict[str, Any] = {
        "ticker": symbol,
        "limit": limit,
        "sort": "ex_dividend_date",
        "order": "asc",
    }
    split_params: dict[str, Any] = {
        "ticker": symbol,
        "limit": limit,
        "sort": "execution_date",
        "order": "asc",
    }
    if start_date:
        dividend_params["ex_dividend_date.gte"] = start_date.isoformat()
        split_params["execution_date.gte"] = start_date.isoformat()
    if end_date:
        dividend_params["ex_dividend_date.lte"] = end_date.isoformat()
        split_params["execution_date.lte"] = end_date.isoformat()
    async with _client() as client:
        dividends, splits = await asyncio.gather(
            _massive_get(client, "/stocks/v1/dividends", params=dividend_params),
            _massive_get(client, "/stocks/v1/splits", params=split_params),
        )
    records = [
        {"action_type": "dividend", **row}
        for row in dividends.get("results", [])
        if isinstance(row, dict)
    ]
    records.extend(
        {"action_type": "split", **row}
        for row in splits.get("results", [])
        if isinstance(row, dict)
    )
    return _envelope(records, source="/stocks/v1/dividends + /stocks/v1/splits")


FINANCE_TOOLS = [
    market_resolve_instrument,
    market_get_snapshots,
    market_get_bars,
    market_get_corporate_actions,
]
