from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

from langalpha.agent.context import RunContext
from langalpha.capabilities import finance as module
from langalpha.config import get_settings


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        context=RunContext(
            project_id="project",
            owner_id="owner",
            thread_id="thread",
            turn_id="market-turn",
        )
    )


async def test_market_snapshots_use_massive_without_fallback(monkeypatch) -> None:
    monkeypatch.setenv("MASSIVE_API_KEY", "test")
    get_settings.cache_clear()

    async def fake_get(_client: object, path: str, *, params: dict) -> dict:
        assert path == "/v3/snapshot"
        assert params["ticker.any_of"] == "AAPL,MSFT"
        return {"status": "OK", "results": [{"ticker": "AAPL"}, {"ticker": "MSFT"}]}

    monkeypatch.setattr(module, "_massive_get", fake_get)
    result = await module.market_get_snapshots.coroutine(
        symbols=["AAPL", "MSFT"],
        runtime=_runtime(),
    )
    payload = json.loads(result)

    assert [record["ticker"] for record in payload["records"]] == ["AAPL", "MSFT"]
    assert payload["provider"] == "Massive"
    assert "fallback" not in payload


async def test_market_bars_return_request_contract_and_market_dates(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MASSIVE_API_KEY", "test")
    get_settings.cache_clear()

    timestamp = int(datetime(2025, 1, 2, 5, tzinfo=UTC).timestamp() * 1_000)

    async def fake_get(_client: object, path: str, *, params: dict) -> dict:
        assert path.endswith("/2025-01-02/2025-01-04")
        assert params == {"adjusted": "true", "sort": "asc", "limit": 5000}
        return {
            "status": "OK",
            "results": [
                {
                    "t": timestamp,
                    "o": 240.0,
                    "h": 245.0,
                    "l": 239.0,
                    "c": 243.85,
                    "v": 1_000,
                }
            ],
        }

    monkeypatch.setattr(module, "_massive_get", fake_get)
    result = await module.market_get_bars.coroutine(
        symbol="AAPL",
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 4),
        adjusted=True,
        runtime=_runtime(),
    )
    payload = json.loads(result)

    assert payload["records"][0]["market_date"] == "2025-01-02"
    assert payload["metadata"]["request"]["adjusted"] is True
    assert payload["metadata"]["market_timezone"] == "America/New_York"
    assert payload["metadata"]["returned_count"] == 1
    assert payload["metadata"]["calendar_dates_without_bars"] == [
        "2025-01-03",
        "2025-01-04",
    ]
    assert payload["source"].startswith("https://api.massive.com/v2/aggs/ticker/AAPL/")


def test_market_tool_schemas_hide_runtime_injection() -> None:
    for market_tool in module.FINANCE_TOOLS:
        assert "runtime" in market_tool.args_schema.model_fields
        properties = market_tool.tool_call_schema.model_json_schema()["properties"]
        assert "runtime" not in properties
