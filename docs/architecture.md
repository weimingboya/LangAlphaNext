# LangAlpha Next architecture

## Runtime ownership

LangAlpha has one Deep Agents experience:

- `main` owns the user-facing turn and asynchronous delegation;
- `researcher` performs evidence and computation work;
- `reporter` produces verified deliverables.

`DeepAgentFactory` is the only place that calls `create_deep_agent`.

LangGraph Agent Server is the unique runtime system of record. It owns
assistants, threads, runs, checkpoints, messages, todos, interrupts,
cancellation, history, Store data, and resumable stream cursors. Production
uses LangSmith Deployment.

FastAPI is a thin product BFF. It owns only:

- the product-thread to Agent Server-thread mapping;
- server-issued project, owner, workspace, and assistant bindings;
- the stable Daytona sandbox binding;
- user uploads and product-facing artifact metadata;
- short-lived user guidance consumed by agent middleware;
- UI snapshot shaping, redaction, and stream proxying.

SQLite deliberately has only `product_threads`, `runtime_bindings`, `artifacts`,
and `guidance`. It has no run, checkpoint, message, event, cursor, terminal, or
Outbox table. Old runtime projection databases are rejected instead of
silently treated as compatible.

## Request flow

For a new turn the BFF creates an Agent Server run with:

- `multitask_strategy="reject"` for concurrency ownership;
- `stream_resumable=True`;
- `messages`, `updates`, and `custom` stream modes;
- an immutable server-issued `RunContext`;
- metadata containing product thread, control, and turn IDs.

The public run ID is the Agent Server run ID. The separate control ID exists
only so `TurnSteeringMiddleware` can claim guidance without trusting browser
identity.

The UI consumes a per-run SSE proxy over `join_stream`. The BFF stores no cursor
and does no background stream following. On reload, one snapshot is rebuilt
from Agent Server state/runs and Daytona artifact metadata. See
[event-contract.md](event-contract.md).

Resume creates a successor Agent Server run with `command={"resume": value}`
and `parent_run_id` metadata. Cancel delegates directly to
`runs.cancel(action="interrupt")`. No compatibility routes or local run state
machine exist.

## Deep Agents, MCP, and Daytona

The implementation directly reuses Deep Agents for planning, filesystem tools,
shell/Python execution, summarization, subagents, skills, memory, permissions,
and model/tool limits.

The context-aware `CompositeBackend` routes:

- default workspace operations to lazy `ContextDaytonaSandbox`;
- `/skills/` and `/memory/` to packaged read-only resources;
- `/memories/user/` and `/memories/workspace/` to LangGraph Store;
- `/memos/` to a read-only Store namespace.

There is no `WorkspaceSeeder` or generic initial-file injection. Daytona is
created only when a workspace operation or upload requires it.

MCP servers and credentials remain in the Agent Server host. The model sees MCP
tool schemas and calls host tools. Large results are materialized under
`/workspace/input/<logical_operation_id>` and ordinary Daytona Python reads
those files for computation. Python inside Daytona does not call MCP directly.
User-visible outputs belong under `/workspace/artifacts`.

## Deployment and limits

Local development runs Agent Server on port 2024 and the BFF on port 8000.
Production points `LANGGRAPH_SERVER_URL` at LangSmith Deployment configured by
`langgraph.json`. LangSmith is the only tracing system.

The simplified research defaults are 40 model calls, 150 tool calls, three
async task starts, 20 minutes per run, 25 MiB uploads, Daytona auto-stop after
60 idle minutes, and auto-archive after seven days. Estimated cost is shown
only when both model rates are configured; it is derived from Agent Server
message state and is not persisted locally.
