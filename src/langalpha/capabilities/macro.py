from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime
from typing import Any, Literal

import httpx
from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from langalpha.agent.context import RunContext
from langalpha.capabilities.errors import raise_for_provider_status
from langalpha.capabilities.gateway import gateway
from langalpha.config import get_settings

_BASE_URL = "https://api.stlouisfed.org/fred/"
_SERIES_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class PublicRuntimeInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: ToolRuntime[RunContext, object]


class SearchSeriesInput(PublicRuntimeInput):
    query: str = Field(min_length=2, max_length=300)
    max_results: int = Field(default=10, ge=1, le=50)


class ObservationsInput(PublicRuntimeInput):
    series_ids: list[str] = Field(min_length=1, max_length=10)
    start_date: date | None = None
    end_date: date | None = None
    units: Literal["lin", "chg", "ch1", "pch", "pc1", "pca", "cch", "cca", "log"] = "lin"
    frequency: str | None = Field(default=None, min_length=1, max_length=20)
    aggregation_method: Literal["avg", "sum", "eop"] = "avg"
    limit: int = Field(default=5_000, ge=1, le=5_000)

    @field_validator("series_ids")
    @classmethod
    def normalize_series_ids(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().upper() for value in values))
        if any(not _SERIES_ID.fullmatch(value) for value in normalized):
            raise ValueError("series_ids contain unsupported characters")
        return normalized


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_BASE_URL,
        timeout=httpx.Timeout(25, connect=8),
        headers={"User-Agent": "LangAlphaNext/0.1", "Accept": "application/json"},
    )


async def _fred_get(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, Any],
) -> dict[str, Any]:
    safe_params = {
        **params,
        "api_key": get_settings().require_fred_key(),
        "file_type": "json",
    }
    try:
        response = await client.get(path, params=safe_params)
    except httpx.RequestError as exc:
        raise RuntimeError("FRED request failed before receiving a response") from exc
    raise_for_provider_status("FRED", response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("FRED returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("FRED returned an invalid response")
    return payload


def _envelope(records: list[dict[str, Any]], *, source: str) -> str:
    return json.dumps(
        {
            "records": records,
            "provider": "Federal Reserve Bank of St. Louis (FRED)",
            "source": source,
            "retrieved_at": datetime.now(UTC).isoformat(),
        },
        ensure_ascii=False,
    )


@tool(args_schema=SearchSeriesInput)
async def fred_search_series(
    query: str,
    runtime: ToolRuntime[RunContext, object],
    max_results: int = 10,
) -> str:
    """Search FRED series metadata before requesting observations."""
    gateway.admit_runtime("fred.search_series", runtime)
    params = {
        "search_text": query,
        "limit": max_results,
        "order_by": "search_rank",
        "sort_order": "asc",
    }
    async with _client() as client:
        payload = await _fred_get(client, "series/search", params=params)
    records = payload.get("seriess")
    return _envelope(
        records if isinstance(records, list) else [],
        source="https://api.stlouisfed.org/fred/series/search",
    )


async def _series_with_observations(
    client: httpx.AsyncClient,
    series_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    metadata, observations = await asyncio.gather(
        _fred_get(client, "series", params={"series_id": series_id}),
        _fred_get(client, "series/observations", params={"series_id": series_id, **params}),
    )
    series = metadata.get("seriess")
    info = series[0] if isinstance(series, list) and series else {}
    rows = observations.get("observations")
    return {
        "series_id": series_id,
        "title": info.get("title"),
        "frequency": info.get("frequency"),
        "units": info.get("units"),
        "seasonal_adjustment": info.get("seasonal_adjustment"),
        "last_updated": info.get("last_updated"),
        "notes": info.get("notes"),
        "observations": rows if isinstance(rows, list) else [],
    }


@tool(args_schema=ObservationsInput)
async def fred_get_observations(
    series_ids: list[str],
    runtime: ToolRuntime[RunContext, object],
    start_date: date | None = None,
    end_date: date | None = None,
    units: Literal["lin", "chg", "ch1", "pch", "pc1", "pca", "cch", "cca", "log"] = "lin",
    frequency: str | None = None,
    aggregation_method: Literal["avg", "sum", "eop"] = "avg",
    limit: int = 5_000,
) -> str:
    """Get bounded FRED observations and metadata for up to 10 resolved series."""
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    gateway.admit_runtime("fred.observations", runtime)
    params: dict[str, Any] = {
        "units": units,
        "aggregation_method": aggregation_method,
        "limit": limit,
        "sort_order": "asc",
    }
    if start_date:
        params["observation_start"] = start_date.isoformat()
    if end_date:
        params["observation_end"] = end_date.isoformat()
    if frequency:
        params["frequency"] = frequency
    async with _client() as client:
        records = await asyncio.gather(
            *(_series_with_observations(client, series_id, params) for series_id in series_ids)
        )
    return _envelope(
        records,
        source="https://api.stlouisfed.org/fred/series/observations",
    )


MACRO_TOOLS = [fred_search_series, fred_get_observations]
