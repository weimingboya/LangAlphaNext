from __future__ import annotations

import json
from types import SimpleNamespace

from langalpha.agent.context import RunContext
from langalpha.capabilities import macro as module
from langalpha.config import get_settings


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(context=RunContext("project", "owner", "thread", "fred-turn"))


async def test_fred_search_returns_series_metadata(monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "test")
    get_settings.cache_clear()

    async def fake_get(_client: object, path: str, *, params: dict) -> dict:
        assert path == "series/search"
        assert params["search_text"] == "consumer price index"
        return {"seriess": [{"id": "CPIAUCSL", "title": "Consumer Price Index"}]}

    monkeypatch.setattr(module, "_fred_get", fake_get)
    result = await module.fred_search_series.coroutine(
        query="consumer price index",
        runtime=_runtime(),
    )
    payload = json.loads(result)

    assert payload["records"][0]["id"] == "CPIAUCSL"
    assert payload["provider"] == "Federal Reserve Bank of St. Louis (FRED)"


def test_fred_tool_schemas_hide_runtime_injection() -> None:
    for fred_tool in module.MACRO_TOOLS:
        assert "runtime" in fred_tool.args_schema.model_fields
        assert "runtime" not in fred_tool.tool_call_schema.model_json_schema()["properties"]
