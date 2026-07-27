from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from langalpha.agent.context import RunContext
from langalpha.capabilities import sec as module
from langalpha.capabilities.errors import (
    NonRetryableToolError,
    is_retryable_tool_error,
    raise_for_provider_status,
)
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


async def test_company_facts_match_exact_tags_and_use_bounded_default(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "LangAlpha test@example.com")
    get_settings.cache_clear()

    observations = [
        {
            "val": index,
            "form": "10-Q",
            "end": f"2025-{(index % 12) + 1:02d}-01",
            "filed": f"2025-{(index % 12) + 1:02d}-02",
        }
        for index in range(25)
    ]

    async def fake_get(_client: object, url: str) -> httpx.Response:
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "facts": {
                    "us-gaap": {
                        "Assets": {
                            "label": "Assets",
                            "units": {"USD": observations},
                        },
                        "AssetsCurrent": {
                            "label": "Current Assets",
                            "units": {"USD": observations},
                        },
                        "AccruedLiabilitiesCurrent": {
                            "label": "Accrued Liabilities",
                            "units": {"USD": observations},
                        },
                    }
                }
            },
        )

    monkeypatch.setattr(module, "_get", fake_get)
    result = await module.sec_get_company_facts.coroutine(
        cik="0000320193",
        concepts=["us-gaap:Assets"],
        runtime=_runtime(),
    )
    records = json.loads(result)["records"]

    assert len(records) == 20
    assert {record["concept"] for record in records} == {"Assets"}


def test_company_facts_reject_unbounded_per_concept_limit() -> None:
    schema = module.sec_get_company_facts.args_schema

    with pytest.raises(ValidationError):
        schema(
            cik="0000320193",
            concepts=["Assets"],
            limit_per_concept=21,
            runtime=_runtime(),
        )


def test_provider_http_errors_expose_retry_semantics() -> None:
    with pytest.raises(NonRetryableToolError, match="HTTP 404") as permanent:
        raise_for_provider_status("SEC", 404)
    with pytest.raises(RuntimeError, match="HTTP 503") as transient:
        raise_for_provider_status("SEC", 503)

    assert is_retryable_tool_error(permanent.value) is False
    assert is_retryable_tool_error(transient.value) is True
    assert is_retryable_tool_error(ValueError("invalid arguments")) is False


def test_sec_tool_schemas_hide_runtime_injection() -> None:
    for sec_tool in module.SEC_TOOLS:
        assert "runtime" in sec_tool.args_schema.model_fields
        assert "runtime" not in sec_tool.tool_call_schema.model_json_schema()["properties"]
