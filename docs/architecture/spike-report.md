# Infrastructure spike report

Status date: 2026-07-24

This report distinguishes contract coverage from real external-service proof.
No API key or secret value is recorded here.

| Spike | Current evidence | Status |
|---|---|---|
| Deep Agents kernel | `main`, `researcher`, and `reporter` load in local Agent Server; expected native tools are present | Passed locally |
| OpenAI harness | GPT-5.6 Luna Responses profile plus real Responses API invocation | Passed |
| Daytona integration | Real private/network-blocked lifecycle, upload/download, execute, blocked egress, stop/start, archive/start, checksum preservation and delete | Passed |
| Agent Server events | resumable create/join, cursor rejoin, duplicate suppression, state reconciliation, resume contracts, and real local cancel-to-interrupted projection | Passed locally; cancel smoke produced one `run.cancelled` terminal |
| Redis Outbox | atomic event/outbox insert, outage retention, retry and publish acknowledgement | Passed with deterministic fake Redis |
| QuickJS PTC | official middleware loads with fixed memory/time/call/result limits and a static allowlist | Passed locally |
| Async subagents | Official start/check/update/cancel/list tools; real co-deployed researcher task launched and checked | Passed |
| LangSmith Deployment | repository has `langgraph.json`, pins the locally verified Agent Server `0.10.3`, and has no self-hosted production path | Real deployment pending |
| LangSmith Trace | tracing configuration is present and no parallel trace system exists | Real trace pending |

Real OpenAI, Daytona, async-subagent, artifact/widget, usage, and HITL gates
passed through the local two-service stack. LangSmith Deployment and trace
proof remain pending because no `LANGSMITH_API_KEY` was supplied.

Deep Agents 0.6.12 rejects global filesystem allow/deny rules when the selected
backend also exposes `execute`, because the execute tool is not yet governed by
those rules. LangAlpha therefore uses the supported permission contract only
to make `/skills`, `/memory`, and `/memos` read-only. It does not add a partial
file-tool-only policy and claim that shell execution is covered. Host isolation,
secret exclusion, and network blocking remain Daytona responsibilities; a
future Deep Agents version can be adopted once one permission contract covers
both file tools and `execute`.

## Reproducible local evidence

```bash
uv run ruff check src tests
uv run pytest -q
node --check src/langalpha/server/static/app.js
OPENAI_API_KEY=<local-secret> uv run langgraph dev --no-browser --no-reload --port 2024
uv run uvicorn langalpha.server.main:app --host 127.0.0.1 --port 8000
```

Browser QA currently proves page identity, non-blank rendering, no framework
overlay, clean error/warning console, desktop/mobile geometry, thread creation,
request submission, SSE replay, and a single authoritative failed-run state
under a deliberately invalid test key.

The latest local suite has 57 passing Python tests and 6 passing browser reducer
tests. It includes a golden control-plane path covering upload, runtime events,
artifact download, widget, usage, Todo, duplicate suppression, terminal state,
cursor-tail replay, and stable full replay. A separate two-service
smoke test against the local Agent Server verifies that explicit product
cancellation persists `cancel_requested`, maps the runtime's `interrupted`
status to `cancelled`, emits exactly one `run.cancelled`, and does not persist
the deliberately invalid test credential.

Browser QA after the reducer extraction verifies module loading, thread
creation, request submission, SSE terminal projection, identical projection
after a full reload, disabled terminal controls, and zero console
errors/warnings.

Structured Widget QA additionally verifies native SVG line and bar rendering
from durable `widget.ready` replay. The browser fixture produced two line
series, nine points, three bars, bounded axis labels, and complete legends with
zero console errors or warnings. Invalid chart contracts fall back to the
bounded data table.

The resumable stream contract is executed 20 times in the deterministic suite;
all 20 runs rejoin from the saved cursor and produce no duplicate metadata or
terminal events.

Concurrent active-run creation is also exercised directly at the repository
boundary; the database admits one request and rejects the competing request.
Configured model rates now produce an idempotent `cost.warning` at the default
USD 1 threshold; no monetary estimate or warning is invented when rates are
absent.

Four paid external gates are present but skipped by the normal suite. The
canonical `make external-test` run passed all four using the Git-ignored
`.env`. They exercise the configured
OpenAI model and the real Daytona create/execute/network/upload/stop/archive/
restore/delete lifecycle, plus a complete two-service local vertical slice from
file upload through finance data, materialization, Daytona Python, report,
widget, official AsyncSubAgent start/check evidence, usage, durable event
validation, and provider cleanup. A separate real HITL gate covers Ask User,
interrupt projection, successor Run resume, and final completion. When a
LangSmith key is present, the vertical slice additionally queries its isolated
LangSmith project for the completed trace hierarchy. The external gate fails
clearly when required core provider variables are absent.

## Known dependency gate

The local and managed Agent Server configuration is pinned to
`langgraph-api 0.10.3`. See `docs/dependency-notes.md` for the
Daytona/OpenTelemetry constraint that currently prevents a valid upgrade to
`0.11.1`. Managed LangSmith Deployment remains the production runtime target.
