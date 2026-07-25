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


async def test_sec_resolver_accepts_combined_company_and_ticker_query(
    monkeypatch,
) -> None:
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
        query="Apple Inc. AAPL",
        runtime=_runtime(),
    )

    assert json.loads(result)["records"][0]["cik"] == "0000320193"


async def test_company_facts_include_deterministic_usd_scales(monkeypatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "LangAlpha test@example.com")
    get_settings.cache_clear()

    async def fake_get(_client: object, url: str) -> httpx.Response:
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "facts": {
                    "us-gaap": {
                        "RevenueFromContractWithCustomerExcludingAssessedTax": {
                            "label": "Revenue",
                            "description": "Revenue",
                            "units": {
                                "USD": [
                                    {
                                        "val": 416_161_000_000,
                                        "form": "10-K",
                                        "fy": 2025,
                                        "fp": "FY",
                                        "end": "2025-09-27",
                                        "filed": "2025-10-31",
                                    }
                                ]
                            },
                        }
                    }
                }
            },
        )

    monkeypatch.setattr(module, "_get", fake_get)
    result = await module.sec_get_company_facts.coroutine(
        cik="0000320193",
        concepts=["RevenueFromContractWithCustomerExcludingAssessedTax"],
        forms=["10-K"],
        runtime=_runtime(),
    )
    normalized = json.loads(result)["records"][0]["normalized_value"]

    assert normalized["raw"] == 416_161_000_000
    assert normalized["usd_billions"] == 416.161
    assert normalized["usd_hundred_millions"] == 4161.61


def test_sec_tool_schemas_hide_runtime_injection() -> None:
    for sec_tool in module.SEC_TOOLS:
        assert "runtime" in sec_tool.args_schema.model_fields
        assert "runtime" not in sec_tool.tool_call_schema.model_json_schema()["properties"]
