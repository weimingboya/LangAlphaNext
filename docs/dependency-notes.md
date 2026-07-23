# Dependency notes

The lockfile and `langgraph.json` currently pin Agent Server to
`langgraph-api 0.10.3`. LangSmith managed deployments default to their latest
stable server unless `api_version` is set, so the deployment pin is required
for the same dependency set verified locally.

`langgraph-api 0.11.1` was evaluated but cannot coexist in one Python
environment with `daytona 0.200.1`: their OpenTelemetry dependency ranges are
disjoint through `opentelemetry-exporter-prometheus 0.58b0` and Daytona's
`opentelemetry-instrumentation-aiohttp-client >=0.59b0`. The project therefore
keeps the compatible lock and managed-server pin instead of replacing the
official Daytona SDK or forcing an invalid resolver override.

Re-evaluate this when Daytona updates its OpenTelemetry instrumentation range.
`0.10.3` is in critical-support status, so this is a temporary release gate,
not an indefinite version policy. LangSmith Deployment remains the production
Agent Server target.
