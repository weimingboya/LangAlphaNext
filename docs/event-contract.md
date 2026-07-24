# Agent Event and snapshot contract

LangAlpha does not maintain a second durable runtime event log. Agent Server is
the source of truth for runs, checkpoints, messages, interrupts, history, and
resumable stream cursors.

## Live stream

The browser follows one Agent Server run through the BFF:

```http
GET /api/threads/{product_thread_id}/runs/{graph_run_id}/stream
Last-Event-ID: <agent-server-event-id>
```

The BFF calls `runs.join_stream(cancel_on_disconnect=False,
last_event_id=...)`, redacts the payload, and returns a transient envelope:

```json
{
  "id": "agent-server-event-id",
  "thread_id": "product-thread-id",
  "run_id": "agent-server-run-id",
  "type": "message.completed",
  "payload": {},
  "created_at": "2026-07-24T00:00:00Z"
}
```

`id` is the Agent Server cursor when one is available. A content-derived
`volatile:*` ID is used only for upstream frames without IDs and is not a
durability guarantee. The BFF emits one synthetic
`terminal:<run_id>:<status>` frame after reconciling the run and thread state.

Stable UI event types are:

- `message.delta`, `message.completed`
- `state.updated`, `agent.custom`, `agent.metadata`
- `sandbox.bound`, `artifact.updated`, `widget.ready`
- `interrupt.requested`, `steering.delivered`
- `run.success`, `run.error`, `run.interrupted`, `run.cancelled`

The browser reducer preserves arrival order and ignores duplicate IDs. It does
not invent a local sequence, replay cursor, or durable source key.

## Reload and recovery

Reload does not replay BFF events. It calls:

```http
GET /api/threads/{product_thread_id}/snapshot
```

The response is assembled from:

- Agent Server `threads.get_state` for messages, todos, and interrupts;
- Agent Server `runs.list` for run history and current status;
- `show_widget` ToolMessages for structured widgets;
- AI message usage metadata for token/cost totals;
- the product artifact index reconciled against the bound Daytona workspace.

HITL is distinguished from cancellation using native semantics:

- a successful Agent Server run with checkpoint interrupts is product
  `interrupted`;
- an Agent Server run whose status is `interrupted` after explicit cancel is
  product `cancelled`;
- a historical run with a successor whose `parent_run_id` points to it remains
  product `interrupted` after the checkpoint is resumed.

## Product-owned side effects

Only two live custom events update the product database:

- `sandbox.bound` records the stable Daytona binding;
- `artifact.changed` upserts product-facing artifact metadata.

These are product resources, not a mirror of Agent Server runtime state.
