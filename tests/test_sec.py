from __future__ import annotations

import json
from hashlib import sha256
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from langalpha.agent.context import RunContext
from langalpha.capabilities import sec as module
from langalpha.capabilities.errors import (
    NonRetryableToolError,
    is_retryable_model_error,
    is_retryable_tool_error,
    raise_for_provider_status,
)
from langalpha.config import get_settings


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(context=RunContext("project", "owner", "thread", "sec-turn"))


@pytest.fixture(autouse=True)
def materialized_datasets(monkeypatch) -> list[dict[str, str]]:
    datasets: list[dict[str, str]] = []

    async def fake_materialize(path: str, content: str, *, format: str) -> dict[str, object]:
        datasets.append({"path": path, "content": content, "format": format})
        payload = content.encode("utf-8")
        return {
            "path": path,
            "format": format,
            "encoding": "utf-8",
            "size_bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
        }

    monkeypatch.setattr(module, "materialize_text", fake_materialize)
    return datasets


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
    payload = json.loads(result)
    normalized = payload["preview"][0]["normalized_value"]

    assert normalized["raw"] == 416_161_000_000
    assert normalized["usd_billions"] == 416.161
    assert normalized["usd_hundred_millions"] == 4161.61
    assert payload["dataset"]["format"] == "jsonl"
    assert payload["dataset"]["row_count"] == 1


async def test_company_facts_match_exact_tags_and_use_bounded_default(
    monkeypatch,
    materialized_datasets,
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
    payload = json.loads(result)
    records = [json.loads(line) for line in materialized_datasets[-1]["content"].splitlines()]

    assert len(records) == 20
    assert {record["concept"] for record in records} == {"Assets"}
    assert payload["preview"] == [
        {
            key: value
            for key, value in record.items()
            if key not in {"cik", "source", "retrieved_at"}
        }
        for record in records[:3]
    ]
    assert len(result) < 10_000


async def test_filing_materializes_full_text_and_returns_only_focused_snippets(
    monkeypatch,
    materialized_datasets,
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "LangAlpha test@example.com")
    get_settings.cache_clear()
    filler = "General disclosure without the requested phrase. " * 4_000
    html = (
        "<html><body><h1>Annual report</h1><p>"
        f"{filler}</p><h2>Data center</h2><p>Data center revenue grew 40 percent."
        " Demand remains strong.</p></body></html>"
    )

    async def fake_get(_client: object, url: str) -> httpx.Response:
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            text=html,
        )

    monkeypatch.setattr(module, "_get", fake_get)
    result = await module.sec_get_filing.coroutine(
        cik="0001045810",
        accession_number="0001045810-25-000116",
        primary_document="nvda-20250126.htm",
        queries=["data center revenue"],
        snippet_chars=1_200,
        runtime=_runtime(),
    )
    payload = json.loads(result)
    saved = materialized_datasets[-1]

    assert saved["path"].endswith("/nvda-20250126.htm.txt")
    assert len(saved["content"]) > 100_000
    assert payload["dataset"]["path"] == saved["path"]
    assert payload["query_results"][0]["match_type"] == "exact"
    assert "Data center revenue grew" in payload["query_results"][0]["snippets"][0]["text"]
    assert len(result) < 6_000


def test_filing_requires_bounded_focused_queries() -> None:
    schema = module.sec_get_filing.args_schema

    with pytest.raises(ValidationError):
        schema(
            cik="0001045810",
            accession_number="0001045810-25-000116",
            primary_document="nvda-20250126.htm",
            queries=[],
            runtime=_runtime(),
        )


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


def test_model_retry_skips_deterministic_size_errors() -> None:
    class ModelError(Exception):
        status_code = 429

    assert is_retryable_model_error(ModelError("temporarily rate limited")) is True
    assert (
        is_retryable_model_error(ModelError("Request too large: Limit 200000, Requested 205984"))
        is False
    )


def test_sec_tool_schemas_hide_runtime_injection() -> None:
    for sec_tool in module.SEC_TOOLS:
        assert "runtime" in sec_tool.args_schema.model_fields
        assert "runtime" not in sec_tool.tool_call_schema.model_json_schema()["properties"]
