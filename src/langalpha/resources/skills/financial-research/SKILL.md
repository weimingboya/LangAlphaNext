---
name: financial-research
description: A reproducible workflow for financial research, evidence checks, and sandbox analysis.
---

# Financial research

Use this skill for company, market, portfolio, or economic research.

1. Restate the question as a falsifiable research target.
2. Separate sourced facts, calculation assumptions, and model inference.
3. Use host-side business or MCP tools to acquire data.
4. Materialize large tabular results under
   `/workspace/input/<logical_operation_id>/`; prefer the source ToolMessage ID
   so records are not copied through another model call.
5. Use Python in Daytona for joins, statistics, and chart preparation.
6. Save durable deliverables under `/workspace/artifacts/`.
7. Report source coverage, time range, units, exclusions, and uncertainty.

Never present synthetic or demo data as observed market data.
