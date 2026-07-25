---
name: financial-research
description: A reproducible workflow for financial research, evidence checks, and sandbox analysis.
---

# Financial research

Use this skill for company, market, portfolio, or economic research.

1. Restate the question as a falsifiable research target.
2. Separate sourced facts, calculation assumptions, and model inference.
3. Choose the strongest first-party source for each claim:
   SEC for filings and XBRL facts, FRED for macro series, Massive for market
   data and corporate actions, and OpenAI Web Search for broader public context.
   Do not silently switch providers when a source fails.
4. Materialize large tabular results under
   `/workspace/input/<logical_operation_id>/`; prefer the source ToolMessage ID
   so records are not copied through another model call.
5. Use Python in Daytona for joins, statistics, and chart preparation.
6. Save durable deliverables under `/workspace/artifacts/`.
7. Attach an exact source URL to every externally verifiable factual claim.
   Prefer primary sources; corroborate high-impact claims when practical.
8. Report source coverage, retrieval time, observation period, units,
   transformations, exclusions, stale-data risk, and uncertainty.

Never present synthetic or demo data as observed market data.
