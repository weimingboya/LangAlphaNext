from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any

import httpx
from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from langalpha.agent.context import RunContext
from langalpha.capabilities.gateway import gateway
from langalpha.config import get_settings

_CIK = re.compile(r"^\d{1,10}$")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_SEARCH_TERM = re.compile(r"[a-z0-9]+")


class PublicRuntimeInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: ToolRuntime[RunContext, object]


class ResolveCompanyInput(PublicRuntimeInput):
    query: str = Field(min_length=1, max_length=200)
    max_results: int = Field(default=5, ge=1, le=20)


class FilingsInput(PublicRuntimeInput):
    cik: str
    forms: list[str] | None = Field(default=None, max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    limit: int = Field(default=40, ge=1, le=200)

    @field_validator("cik")
    @classmethod
    def normalize_cik(cls, value: str) -> str:
        normalized = value.strip().lstrip("0") or "0"
        if not _CIK.fullmatch(normalized):
            raise ValueError("cik must contain at most 10 digits")
        return normalized.zfill(10)


class FilingDocumentInput(PublicRuntimeInput):
    cik: str
    accession_number: str
    primary_document: str
    query: str | None = Field(default=None, max_length=300)
    max_chars: int = Field(default=30_000, ge=2_000, le=100_000)

    @field_validator("cik")
    @classmethod
    def normalize_cik(cls, value: str) -> str:
        normalized = value.strip().lstrip("0") or "0"
        if not _CIK.fullmatch(normalized):
            raise ValueError("cik must contain at most 10 digits")
        return normalized.zfill(10)

    @field_validator("accession_number")
    @classmethod
    def validate_accession(cls, value: str) -> str:
        if not _ACCESSION.fullmatch(value):
            raise ValueError("accession_number must use 0000000000-00-000000 format")
        return value

    @field_validator("primary_document")
    @classmethod
    def validate_document(cls, value: str) -> str:
        if not _DOCUMENT.fullmatch(value):
            raise ValueError("primary_document contains unsupported characters")
        return value


class CompanyFactsInput(PublicRuntimeInput):
    cik: str
    concepts: list[str] = Field(min_length=1, max_length=20)
    forms: list[str] | None = Field(default=None, max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    limit_per_concept: int = Field(default=40, ge=1, le=200)

    @field_validator("cik")
    @classmethod
    def normalize_cik(cls, value: str) -> str:
        normalized = value.strip().lstrip("0") or "0"
        if not _CIK.fullmatch(normalized):
            raise ValueError("cik must contain at most 10 digits")
        return normalized.zfill(10)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        elif tag.lower() in {"p", "br", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


def _html_to_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(value)
    return "\n".join(
        normalized
        for line in "".join(parser.parts).splitlines()
        if (normalized := re.sub(r"\s+", " ", line).strip())
    )


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=8),
        follow_redirects=True,
        headers={
            "User-Agent": get_settings().require_sec_user_agent(),
            "Accept": "application/json,text/html,application/xhtml+xml",
            "Accept-Encoding": "gzip, deflate",
        },
    )


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    response = await client.get(url)
    if response.status_code >= 400:
        raise RuntimeError(f"SEC request failed with HTTP {response.status_code}")
    return response


def _envelope(records: list[dict[str, Any]], *, source: str) -> str:
    return json.dumps(
        {
            "records": records,
            "provider": "U.S. Securities and Exchange Commission",
            "source": source,
            "retrieved_at": datetime.now(UTC).isoformat(),
        },
        ensure_ascii=False,
    )


def _search_terms(value: str) -> tuple[str, ...]:
    return tuple(_SEARCH_TERM.findall(value.casefold()))


def _normalized_financial_value(value: Any, unit: str) -> dict[str, Any] | None:
    if unit.upper() != "USD" or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return {
        "raw": value,
        "unit": "USD",
        "usd_millions": round(value / 1_000_000, 6),
        "usd_billions": round(value / 1_000_000_000, 6),
        "usd_hundred_millions": round(value / 100_000_000, 6),
        "display_rule": (
            "Use usd_billions for USD billions; Chinese 亿美元 equals "
            "usd_hundred_millions. Do not recalculate these values mentally."
        ),
    }


@tool(args_schema=ResolveCompanyInput)
async def sec_resolve_company(
    query: str,
    runtime: ToolRuntime[RunContext, object],
    max_results: int = 5,
) -> str:
    """Resolve a company name, ticker, or CIK using the SEC company ticker file."""
    gateway.admit_runtime("sec.resolve_company", runtime)
    source = "https://www.sec.gov/files/company_tickers.json"
    async with _client() as client:
        payload = (await _get(client, source)).json()
    normalized = " ".join(_search_terms(query))
    query_terms = set(_search_terms(query))
    if not query_terms:
        return _envelope([], source=source)
    rows = payload.values() if isinstance(payload, dict) else []
    ranked = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker", ""))
        title = str(row.get("title", ""))
        cik = str(row.get("cik_str", ""))
        normalized_ticker = ticker.casefold()
        normalized_title = " ".join(_search_terms(title))
        normalized_cik = cik.lstrip("0")
        haystack = " ".join((normalized_ticker, normalized_title, normalized_cik))
        haystack_terms = set(_search_terms(haystack))
        if normalized not in haystack and not query_terms.issubset(haystack_terms):
            continue
        if normalized in {normalized_ticker, normalized_cik}:
            rank = 0
        elif normalized == normalized_title:
            rank = 1
        elif normalized in haystack:
            rank = 2
        else:
            rank = 3
        ranked.append(
            (
                rank,
                {
                    "cik": cik.zfill(10),
                    "ticker": ticker,
                    "name": title,
                    "submissions_url": (
                        f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
                    ),
                },
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]["ticker"]))
    return _envelope([row for _, row in ranked[:max_results]], source=source)


@tool(args_schema=FilingsInput)
async def sec_list_filings(
    cik: str,
    runtime: ToolRuntime[RunContext, object],
    forms: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 40,
) -> str:
    """List recent SEC filings with accession and primary-document identifiers."""
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    gateway.admit_runtime("sec.list_filings", runtime)
    source = f"https://data.sec.gov/submissions/CIK{cik}.json"
    async with _client() as client:
        payload = (await _get(client, source)).json()
    recent = (payload.get("filings") or {}).get("recent") or {}
    normalized_forms = {value.upper() for value in forms or []}
    keys = [
        "accessionNumber",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "act",
        "form",
        "fileNumber",
        "filmNumber",
        "items",
        "size",
        "isXBRL",
        "isInlineXBRL",
        "primaryDocument",
        "primaryDocDescription",
    ]
    length = max((len(recent.get(key, [])) for key in keys), default=0)
    records = []
    for index in range(length):
        row = {key: recent.get(key, [])[index] for key in keys if index < len(recent.get(key, []))}
        filing_date = row.get("filingDate")
        form = str(row.get("form", "")).upper()
        if normalized_forms and form not in normalized_forms:
            continue
        if start_date and filing_date and filing_date < start_date.isoformat():
            continue
        if end_date and filing_date and filing_date > end_date.isoformat():
            continue
        accession = str(row.get("accessionNumber", ""))
        document = str(row.get("primaryDocument", ""))
        row["filing_url"] = (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{accession.replace('-', '')}/{document}"
        )
        records.append(row)
        if len(records) >= limit:
            break
    return _envelope(records, source=source)


@tool(args_schema=FilingDocumentInput)
async def sec_get_filing(
    cik: str,
    accession_number: str,
    primary_document: str,
    runtime: ToolRuntime[RunContext, object],
    query: str | None = None,
    max_chars: int = 30_000,
) -> str:
    """Retrieve bounded readable text from a specific SEC primary filing document."""
    gateway.admit_runtime("sec.get_filing", runtime)
    source = (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accession_number.replace('-', '')}/{primary_document}"
    )
    async with _client() as client:
        response = await _get(client, source)
    full_text = _html_to_text(response.text)
    text = full_text
    if query:
        match = re.search(re.escape(query), text, flags=re.IGNORECASE)
        if match:
            start = max(0, match.start() - max_chars // 3)
            text = text[start : start + max_chars]
        else:
            text = text[:max_chars]
    else:
        text = text[:max_chars]
    return json.dumps(
        {
            "cik": cik,
            "accession_number": accession_number,
            "primary_document": primary_document,
            "query": query,
            "text": text,
            "truncated": len(full_text) > len(text),
            "provider": "U.S. Securities and Exchange Commission",
            "source": source,
            "retrieved_at": datetime.now(UTC).isoformat(),
        },
        ensure_ascii=False,
    )


@tool(args_schema=CompanyFactsInput)
async def sec_get_company_facts(
    cik: str,
    concepts: list[str],
    runtime: ToolRuntime[RunContext, object],
    forms: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit_per_concept: int = 40,
) -> str:
    """Get bounded SEC XBRL company facts for requested financial concepts."""
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    gateway.admit_runtime("sec.company_facts", runtime)
    source = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    async with _client() as client:
        payload = (await _get(client, source)).json()
    requested = [value.casefold() for value in concepts]
    normalized_forms = {value.upper() for value in forms or []}
    records = []
    facts = payload.get("facts") or {}
    for taxonomy, taxonomy_facts in facts.items():
        if not isinstance(taxonomy_facts, dict):
            continue
        for tag, fact in taxonomy_facts.items():
            label = str(fact.get("label", ""))
            haystack = f"{taxonomy}:{tag} {tag} {label}".casefold()
            if not any(value == tag.casefold() or value in haystack for value in requested):
                continue
            units = fact.get("units") or {}
            concept_rows = []
            for unit, observations in units.items():
                for observation in observations if isinstance(observations, list) else []:
                    form = str(observation.get("form", "")).upper()
                    end = observation.get("end")
                    filed = observation.get("filed")
                    if normalized_forms and form not in normalized_forms:
                        continue
                    comparison_date = end or filed
                    if start_date and comparison_date and comparison_date < start_date.isoformat():
                        continue
                    if end_date and comparison_date and comparison_date > end_date.isoformat():
                        continue
                    row = {
                        "taxonomy": taxonomy,
                        "concept": tag,
                        "label": label,
                        "description": fact.get("description"),
                        "unit": unit,
                        **observation,
                    }
                    normalized_value = _normalized_financial_value(
                        observation.get("val"),
                        unit,
                    )
                    if normalized_value is not None:
                        row["normalized_value"] = normalized_value
                    concept_rows.append(row)
            concept_rows.sort(key=lambda row: str(row.get("filed", "")), reverse=True)
            records.extend(concept_rows[:limit_per_concept])
    return _envelope(records, source=source)


SEC_TOOLS = [
    sec_resolve_company,
    sec_list_filings,
    sec_get_filing,
    sec_get_company_facts,
]
