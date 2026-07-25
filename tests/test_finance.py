from __future__ import annotations

import json
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


def test_market_tool_schemas_hide_runtime_injection() -> None:
    for market_tool in module.FINANCE_TOOLS:
        assert "runtime" in market_tool.args_schema.model_fields
        properties = market_tool.tool_call_schema.model_json_schema()["properties"]
        assert "runtime" not in properties
