---
name: valuation
description: Produce transparent valuation scenarios using sourced market, filing, and macro inputs.
---

# Valuation

1. State the valuation date, security, share class, currency, and method.
2. Source operating inputs from SEC filings, prices and corporate actions from
   Massive, and macro assumptions from FRED.
3. Separate observed inputs, normalized inputs, forecasts, and judgment.
4. Adjust historical prices and per-share measures consistently for splits and
   other relevant corporate actions.
5. Build base, upside, and downside cases with explicit drivers.
6. Show sensitivity to the assumptions that materially change the conclusion.
7. Save calculations and tables under `/workspace/artifacts/`, including source
   URLs and retrieval timestamps.

Present valuation as a range with limitations, not as a precise forecast.
