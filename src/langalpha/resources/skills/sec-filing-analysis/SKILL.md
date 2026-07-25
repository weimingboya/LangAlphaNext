---
name: sec-filing-analysis
description: Analyze SEC filings with primary-document citations and explicit evidence boundaries.
---

# SEC filing analysis

Use this skill when a conclusion depends on a public company's regulatory filings.

1. Resolve the issuer with `sec_resolve_company`; never infer a CIK from memory.
2. Use `sec_list_filings` to identify the exact form, filing date, accession,
   and primary document.
3. Retrieve the filing with `sec_get_filing`. Search for the relevant section
   before requesting more text.
4. Treat management statements, audited facts, risk disclosures, and your own
   inference as separate evidence classes.
5. Cite the filing URL and identify the form, filing date, and relevant section.
6. For numeric comparisons, use `sec_get_company_facts` and retain taxonomy,
   concept, unit, period, form, and accession metadata.

Do not treat an earnings release, search snippet, or third-party summary as a
substitute for the filing when the SEC source is available.
