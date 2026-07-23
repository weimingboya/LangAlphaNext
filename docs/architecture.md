# LangAlpha Next architecture

## Boundaries

LangAlpha uses one Deep Agents graph family:

- `main` owns the user-facing turn and asynchronous delegation;
- `researcher` performs evidence and computation work;
- `reporter` turns verified evidence into a deliverable.

All three graphs are created by `DeepAgentFactory`. No other module may call
`create_deep_agent`.

LangGraph Agent Server owns execution state. The FastAPI control plane owns
product identifiers, mappings to Agent Server threads/runs, durable DomainEvents,
and artifact metadata. The browser consumes only the product API.

## Native capability reuse

The implementation directly reuses Deep Agents for planning, filesystem tools,
shell/Python execution, summarization, subagents, skills, memory, permissions,
and model/tool call limits.

The backend is a context-aware `CompositeBackend`:

- the default backend is a lazy `ContextDaytonaSandbox`;
- `/skills/` routes to packaged, read-only application skills;
- `/memory/` routes to packaged, read-only product memory;
- `/memories/user/` and `/memories/workspace/` route to the managed
  LangGraph Store with server-issued user/workspace namespaces;
- `/memos/` routes to a read-only Store namespace;
- command execution always routes to Daytona.

This is a direct backend instance rather than the deprecated callable backend
factory. It reads the immutable LangGraph `RunContext` at operation time, so
concurrent runs retain workspace isolation without creating sandboxes eagerly.

Deep Agents 0.6.12 can enforce read-only permissions on the routed application
resources above, but it rejects a global filesystem allow/deny policy when the
backend also exposes `execute`. LangAlpha does not add a file-tool-only policy
that would leave shell access outside the same contract. Daytona supplies the
actual host isolation, has no business credentials, and is created with network
access blocked. The exact upstream limitation is recorded in the spike report.

## MCP and data computation

Configured MCP servers are discovered once in the Agent Server process through
`MultiServerMCPClient`. Business credentials stay in the host environment.
Business tools never get installed into Daytona.

For small results the model can inspect the tool response directly. Data that
needs batch computation, reuse, charting, or reproducible analysis is
materialized with `materialize_dataset` to
`/workspace/input/<logical_operation_id>/<name>.jsonl|csv`, then consumed by
ordinary Daytona Python. The MCP call still happens in the Agent Server host;
Python never calls MCP directly. For large data, `source_tool_call_id` resolves
the prior ToolMessage from Agent state so the model does not copy the records.

There is no `WorkspaceSeeder` and no generic initial-file injection. Built-in
skills and memory remain on their read-only backend routes; user uploads,
materialized datasets, and agent-generated artifacts are created only when the
run needs them. The model discovers business capabilities from MCP tool schemas
and skills, not from generated client code inside the sandbox. It receives a
stable `DatasetRef` after materialization, including path, format, schema, row
count, source, and checksum, then writes ordinary Python that reads that file.

## Streaming and recovery

The local product API starts Agent Server runs as resumable runs and follows
them with `join_stream`. Raw stream parts are normalized into DomainEvents and
persisted before they are exposed over SSE.

On API startup, unfinished product runs are discovered from SQLite and
reattached to Agent Server. SSE clients can reconnect with `Last-Event-ID` or
`?after=<sequence>` and replay from the durable event log.

The Agent Server stream itself uses resumable `join_stream(last_event_id=...)`.
Transient disconnects consume a bounded reconnect budget and durable final
state reconciliation prevents duplicate final messages, usage, artifacts, or
terminal events.

Product cancellation is modeled separately from runtime interruption. The
control plane persists `cancel_requested` before invoking Agent Server cancel.
Only a remote `interrupted` or `cancelled` state observed with that intent is
projected as product `cancelled`; HITL and other interrupts remain
`interrupted`. The cancel endpoint and stream follower share a stable terminal
event key so completion races remain idempotent.

The local projection database enforces at most one `pending` or `running`
product run per thread with a partial unique index. This closes the
check-then-create race; the Agent Server remains authoritative for the run's
actual execution state.

Each durable DomainEvent and its Outbox row are committed atomically. Local SSE
polls the event log. When `REDIS_URL` is configured, an asyncio publisher sends
events to `langalpha:events:<thread_id>` with at-least-once delivery and marks
each row only after Redis acknowledges it; event IDs keep consumers idempotent.

The browser uses one pure DomainEvent reducer for both live SSE and replay.
Incoming envelopes are shape-checked, ordered by durable sequence, and deduped
by event ID before UI projection.

Deep Agents event-streaming helpers may be used for in-process diagnostics, but
the product contract does not depend on their provider-specific event shapes.

## Deployment

Local development uses the in-memory Agent Server on port 2024 and FastAPI on
port 8000. Production Agent Server deployment uses LangSmith Deployment and the
repository `langgraph.json`; set OpenAI, Daytona, LangSmith, and MCP variables in
the deployment environment. The deployment currently pins Agent Server
`0.10.3` because the latest server and current official Daytona SDK have
incompatible OpenTelemetry dependency ranges. The pin is removed only after a
clean dependency resolution and contract rerun.

The control plane remains a separate small service and points
`LANGGRAPH_SERVER_URL` at the managed deployment. No legacy data migration or
compatibility adapter is required.

## Limits

The default simplified research-project limits are:

- 40 model calls per run;
- 150 tool calls per run;
- 3 async task starts per run;
- 20 minutes wall-clock time per run;
- USD 1 estimated-cost warning when explicit model rates are configured;
- Daytona auto-stop after 60 idle minutes and auto-archive after seven days.

LangSmith tracing is the sole trace system when its key is configured.
