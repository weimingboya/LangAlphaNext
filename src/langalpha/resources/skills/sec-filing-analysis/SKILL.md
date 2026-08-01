---
name: sec-filing-analysis
description: Analyze SEC filings with primary-document citations and explicit evidence boundaries.
---

# SEC filing analysis

Use this skill when a conclusion depends on a public company's regulatory filings.

1. Resolve the issuer with `sec_resolve_company`; never infer a CIK from memory.
2. Use `sec_list_filings` to identify the exact form, filing date, accession,
   and primary document.
3. For each issuer, call `sec_list_filings` first, then pass its exact
   `accessionNumber` and `primaryDocument` values to `sec_get_filing`; never
   infer either identifier. Pass a small list of focused section names or phrases.
   Use the returned excerpts first; search or read the materialized text dataset
   only when the excerpts are insufficient. Never load a full filing into context.
4. Treat management statements, audited facts, risk disclosures, and your own
   inference as separate evidence classes.
5. Cite the filing URL and identify the form, filing date, and relevant section.
6. For numeric comparisons, use `sec_get_company_facts`. Treat its preview as
   orientation only. Use Python over the returned JSONL dataset to filter, align
   periods, deduplicate, aggregate, and calculate; a read-only researcher should
   hand the dataset reference to the main agent for that work. Retain taxonomy,
   concept, unit, period, form, accession, and source metadata. Do not echo the
   dataset back into model context.

Materialized SEC datasets under `/workspace/.langalpha/datasets/` are private
working data, not user deliverables. Put only final reports and charts under
`/workspace/artifacts/`.

Do not treat an earnings release, search snippet, or third-party summary as a
substitute for the filing when the SEC source is available.
