---
name: financial-statements
description: Build reproducible income statement, balance sheet, and cash-flow analysis from SEC facts.
---

# Financial statement analysis

1. Define the metric, accounting concept, unit, fiscal period, and comparison
   basis before collecting data.
2. Resolve the issuer, then request only the needed SEC XBRL concepts.
3. Prefer facts from 10-K and 10-Q filings. Detect amended filings, duplicate
   periods, restatements, instant-versus-duration concepts, and unit changes.
4. Materialize large fact sets before joins or calculations.
5. Compute growth, margins, working-capital changes, leverage, and cash
   conversion in Python; keep the transformation code with the artifact.
6. Reconcile derived totals against the filing and explain discrepancies.
7. Report fiscal periods rather than assuming calendar periods.

Never mix annual, quarterly, year-to-date, and trailing-period values without an
explicit normalization.
