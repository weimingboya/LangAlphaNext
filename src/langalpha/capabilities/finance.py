from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from langalpha.agent.context import RunContext
from langalpha.capabilities.gateway import gateway

_SYMBOL = re.compile(r"^[A-Z0-9.^=-]{1,20}$")
_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


class MarketQuotesInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: ToolRuntime[RunContext, object]
    symbols: list[str] = Field(
        min_length=1,
        max_length=20,
        description="US or global market symbols such as AAPL, MSFT, or ^GSPC.",
    )

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().upper() for value in values))
        if any(not _SYMBOL.fullmatch(value) for value in normalized):
            raise ValueError("symbols contain unsupported characters")
        return normalized


async def _fetch_quote(symbol: str, client: httpx.AsyncClient) -> dict[str, Any]:
    url = _YAHOO_CHART.format(symbol=quote(symbol, safe=""))
    response = await client.get(
        url,
        params={"range": "5d", "interval": "1d", "events": "div,splits"},
    )
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(str(chart["error"].get("description") or "quote failed"))
    results = chart.get("result") or []
    if not results:
        raise ValueError("quote provider returned no result")
    result = results[0]
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice")
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    change = price - previous if isinstance(price, (int, float)) and previous else None
    percent = change / previous * 100 if change is not None and previous else None
    market_timestamp = meta.get("regularMarketTime")
    market_time = (
        datetime.fromtimestamp(market_timestamp, UTC).isoformat()
        if isinstance(market_timestamp, (int, float))
        else None
    )
    return {
        "symbol": str(meta.get("symbol") or symbol),
        "name": meta.get("longName") or meta.get("shortName"),
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "price": price,
        "previous_close": previous,
        "change": change,
        "change_percent": percent,
        "market_time": market_time,
        "source": url,
    }


@tool(args_schema=MarketQuotesInput)
async def get_market_quotes(
    symbols: list[str],
    runtime: ToolRuntime[RunContext, object],
) -> str:
    """Fetch a small set of delayed/current market quotes in the host runtime.

    Use for at most 20 symbols. For reproducible Python analysis, call
    materialize_dataset with this ToolMessage's source_tool_call_id before
    computing or charting.
    """

    context = runtime.context
    if context is None:
        raise RuntimeError("server-issued RunContext is required")
    gateway.admit("market.quotes", context)
    async with httpx.AsyncClient(
        timeout=12,
        headers={"User-Agent": "LangAlphaNext/0.1 personal-research"},
        follow_redirects=True,
    ) as client:
        results = await asyncio.gather(
            *(_fetch_quote(symbol, client) for symbol in symbols),
            return_exceptions=True,
        )

    records = []
    errors = []
    for symbol, result in zip(symbols, results, strict=True):
        if isinstance(result, BaseException):
            errors.append({"symbol": symbol, "error": type(result).__name__})
        else:
            records.append(result)
    return json.dumps(
        {
            "records": records,
            "errors": errors,
            "provider": "Yahoo Finance chart endpoint",
            "retrieved_at": datetime.now(UTC).isoformat(),
            "notice": "Market data may be delayed; verify before making decisions.",
        },
        ensure_ascii=False,
    )


FINANCE_TOOLS = [get_market_quotes]
