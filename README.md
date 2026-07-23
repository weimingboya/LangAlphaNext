# LangAlpha Next

LangAlpha Next is a clean-room rewrite on the Deep Agents stack. It has one
agent experience rather than separate Flash/PTC modes.

The implementation uses:

- Deep Agents as the only agent harness;
- LangGraph Agent Server for thread, run, checkpoint, cancellation, and stream state;
- OpenAI `gpt-5.6-luna` through the Responses API;
- Daytona as the only Python, shell, and workspace sandbox;
- host-side MCP tools through `langchain-mcp-adapters`;
- a thin FastAPI control plane with durable, replayable DomainEvents;
- a responsive local research UI served by the control plane.

## Local development

Requirements: Python 3.12+, `uv`, an OpenAI API key, and a Daytona API key.
LangSmith tracing is verified additionally when `LANGSMITH_API_KEY` is set.

```bash
cp .env.example .env
# Fill keys locally. The file is ignored by Git.
make sync
make agent
```

In another terminal:

```bash
make api
```

Open:

- product UI: `http://127.0.0.1:8000`
- API docs: `http://127.0.0.1:8000/docs`
- Agent Server: `http://127.0.0.1:2024`
- LangGraph Studio: the URL printed by `make agent`

`make agent` disables file watching because the local SQLite database and
stream checkpoints otherwise cause continuous development reloads. Restart the
command after changing graph code.

## Workspace behavior

A new thread does not create a Daytona sandbox and has no initial business
files. Daytona is resolved only when an agent executes code, reads/writes a
workspace file, materializes a dataset, or a user uploads a file.

Product-owned skills and memory are mounted read-only from the application
package at `/skills` and `/memory`; they are not copied into the user workspace.
Cross-thread user memory and workspace-scoped memory use the managed LangGraph
Store through `/memories/user` and `/memories/workspace`.

MCP tools run in the Agent Server host process. Their schemas are exposed to the
model, while credentials never enter Daytona. Large results can be materialized
under `/workspace/input/<logical_operation_id>` and analyzed with ordinary
Python. Prefer `source_tool_call_id` so large Tool results are read from the
existing ToolMessage rather than copied through another model call. Python does
not call MCP directly. User-visible outputs belong under `/workspace/artifacts`.

The product UI supports durable run progress, replay, artifacts, HITL
Ask/Plan cards, guidance, cancellation, structured data widgets, native SVG
bar/line charts, and token usage. Cost is shown only when explicit input/output
rates are configured. The default cumulative per-run warning threshold is USD 1
and can be changed with `COST_WARNING_USD`.
Live SSE delivery and history replay feed the same validated, idempotent
DomainEvent reducer.

Local SSE reads the durable SQLite event log directly. Set `REDIS_URL` when an
external event fan-out is needed; the atomic Outbox publisher retains
unpublished events during Redis outages and resumes from them after recovery.

## Quality checks

```bash
make lint
make test
```

`make test` runs both the Python contract/golden suite and the browser
DomainEvent reducer tests.

After exporting real credentials in the local shell, run the paid provider
gates separately:

```bash
make external-test
```

This verifies the configured OpenAI model plus Daytona create, execute,
blocked-egress, upload/download, stop/start, archive/restore checksum, and
cleanup lifecycle. It also runs the complete local two-service vertical slice:
upload → finance tool → dataset materialization → Daytona Python → report and
widget → async researcher → durable events and replay checks, plus a real
Ask User interrupt/resume cycle. When a LangSmith key is configured, it also
queries the isolated LangSmith project through the LangSmith CLI to prove that
the real trace tree is available. It never runs as part of the normal unit
suite.

See [architecture.md](docs/architecture.md) and
[event-contract.md](docs/event-contract.md) for the runtime contract. Current
external-service verification status is recorded in
[spike-report.md](docs/architecture/spike-report.md), with the requirement-level
completion state in [completion-audit.md](docs/completion-audit.md).
