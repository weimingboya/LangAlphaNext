from __future__ import annotations

import json
from datetime import date
from typing import Any

from langchain.tools import tool

FIXTURE_VERSION = "2026-07-26.v1"

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
FRED_DFF_URL = "https://fred.stlouisfed.org/series/DFF"
APPLE_IR_URL = "https://investor.apple.com/"


def _envelope(
    records: list[dict[str, Any]],
    *,
    provider: str,
    source: str,
) -> str:
    return json.dumps(
        {
            "records": records,
            "provider": provider,
            "source": source,
            "retrieved_at": "2026-07-26T00:00:00Z",
            "fixture_version": FIXTURE_VERSION,
        },
        ensure_ascii=False,
    )


@tool("sec_resolve_company")
async def fixture_sec_resolve_company(query: str, max_results: int = 5) -> str:
    """Resolve a company name, ticker, or CIK from a deterministic SEC fixture."""
    matches = []
    if query.strip().casefold() in {"apple", "apple inc.", "aapl", "320193", "0000320193"}:
        matches.append(
            {
                "cik": "0000320193",
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "submissions_url": ("https://data.sec.gov/submissions/CIK0000320193.json"),
            }
        )
    return _envelope(
        matches[:max_results],
        provider="U.S. Securities and Exchange Commission",
        source=SEC_TICKERS_URL,
    )


@tool("sec_get_company_facts")
async def fixture_sec_get_company_facts(
    cik: str,
    concepts: list[str],
    forms: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit_per_concept: int = 40,
) -> str:
    """Return deterministic Apple revenue facts for evaluator fixtures."""
    del concepts, forms, start_date, end_date, limit_per_concept
    records = []
    if cik.strip().lstrip("0") == "320193":
        records = [
            {
                "concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                "label": "Net sales",
                "value": 383_285_000_000,
                "unit": "USD",
                "fy": 2023,
                "fp": "FY",
                "form": "10-K",
                "filed": "2023-11-03",
                "normalized_value": {
                    "raw": 383_285_000_000,
                    "unit": "USD",
                    "usd_billions": 383.285,
                },
            },
            {
                "concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                "label": "Net sales",
                "value": 391_035_000_000,
                "unit": "USD",
                "fy": 2024,
                "fp": "FY",
                "form": "10-K",
                "filed": "2024-11-01",
                "normalized_value": {
                    "raw": 391_035_000_000,
                    "unit": "USD",
                    "usd_billions": 391.035,
                },
            },
        ]
    return _envelope(
        records,
        provider="U.S. Securities and Exchange Commission",
        source=SEC_FACTS_URL,
    )


@tool("fred_get_observations")
async def fixture_fred_get_observations(
    series_ids: list[str],
    start_date: date | None = None,
    end_date: date | None = None,
    units: str = "lin",
    frequency: str | None = None,
    aggregation_method: str = "avg",
    limit: int = 5_000,
) -> str:
    """Return deterministic annual effective federal funds rate fixtures."""
    del start_date, end_date, units, frequency, aggregation_method, limit
    records = []
    if "DFF" in {series_id.upper() for series_id in series_ids}:
        records.append(
            {
                "series_id": "DFF",
                "title": "Federal Funds Effective Rate",
                "frequency": "Annual",
                "units": "Percent",
                "observations": [
                    {"date": "2023-01-01", "value": "5.02"},
                    {"date": "2024-01-01", "value": "5.14"},
                ],
            }
        )
    return _envelope(
        records,
        provider="Federal Reserve Bank of St. Louis (FRED)",
        source=FRED_DFF_URL,
    )


@tool("public_company_guidance")
async def fixture_public_company_guidance(company: str, fiscal_year: int) -> str:
    """Return a deliberately conflicting public-source fixture for grounding tests."""
    records = []
    if company.strip().casefold() in {"apple", "apple inc.", "aapl"} and fiscal_year == 2024:
        records.append(
            {
                "company": "Apple Inc.",
                "fiscal_year": 2024,
                "reported_revenue_usd_billions": 400.0,
                "note": (
                    "Deliberately conflicting secondary-source fixture. "
                    "The SEC filing is authoritative."
                ),
            }
        )
    return _envelope(
        records,
        provider="Secondary public-source fixture",
        source=APPLE_IR_URL,
    )


FIXTURE_RESEARCH_TOOLS = [
    fixture_sec_resolve_company,
    fixture_sec_get_company_facts,
    fixture_fred_get_observations,
    fixture_public_company_guidance,
]
