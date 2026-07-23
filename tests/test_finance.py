from __future__ import annotations

import json
from types import SimpleNamespace

from langalpha.agent.context import RunContext
from langalpha.capabilities import finance as module


async def test_market_quotes_returns_structured_provenance(monkeypatch) -> None:
    async def fake_fetch(symbol: str, _client: object) -> dict:
        return {
            "symbol": symbol,
            "price": 123.0,
            "previous_close": 120.0,
            "change": 3.0,
            "change_percent": 2.5,
            "source": f"https://example.test/{symbol}",
        }

    monkeypatch.setattr(module, "_fetch_quote", fake_fetch)
    context = RunContext(
        project_id="project",
        owner_id="owner",
        workspace_id="workspace",
        product_thread_id="thread",
        turn_id="turn",
        product_run_id="finance-run",
    )
    result = await module.get_market_quotes.coroutine(
        symbols=["AAPL", "MSFT"],
        runtime=SimpleNamespace(context=context),
    )
    payload = json.loads(result)

    assert [record["symbol"] for record in payload["records"]] == ["AAPL", "MSFT"]
    assert payload["errors"] == []
    assert payload["provider"] == "Yahoo Finance chart endpoint"


def test_market_quote_schema_preserves_hidden_runtime_injection() -> None:
    assert "runtime" in module.get_market_quotes.args_schema.model_fields
    properties = module.get_market_quotes.tool_call_schema.model_json_schema()["properties"]
    assert "runtime" not in properties
