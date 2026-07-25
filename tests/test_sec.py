from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from langalpha.agent.context import RunContext
from langalpha.capabilities import sec as module
from langalpha.config import get_settings


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(context=RunContext("project", "owner", "thread", "sec-turn"))


async def test_sec_resolves_company_with_primary_source(monkeypatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "LangAlpha test@example.com")
    get_settings.cache_clear()

    async def fake_get(_client: object, url: str) -> httpx.Response:
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
            },
        )

    monkeypatch.setattr(module, "_get", fake_get)
    result = await module.sec_resolve_company.coroutine(
        query="AAPL",
        runtime=_runtime(),
    )
    payload = json.loads(result)

    assert payload["records"][0]["cik"] == "0000320193"
    assert payload["records"][0]["ticker"] == "AAPL"
    assert payload["source"] == "https://www.sec.gov/files/company_tickers.json"


def test_sec_tool_schemas_hide_runtime_injection() -> None:
    for sec_tool in module.SEC_TOOLS:
        assert "runtime" in sec_tool.args_schema.model_fields
        assert "runtime" not in sec_tool.tool_call_schema.model_json_schema()["properties"]
