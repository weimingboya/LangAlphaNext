# DomainEvent contract

Every browser-visible event has this envelope:

```json
{
  "schema_version": 1,
  "delivery": "durable",
  "sequence": 42,
  "id": "event-uuid",
  "source_event_key": "runtime:run-id:message:message-id:final",
  "project_id": "langalpha-local",
  "workspace_id": "workspace-uuid",
  "thread_id": "product-thread-uuid",
  "turn_id": "turn-uuid",
  "run_id": "product-run-uuid-or-null",
  "type": "message.completed",
  "source": {"agent_id": "main", "parent_agent_id": null},
  "payload": {},
  "created_at": "2026-07-24T00:00:00Z"
}
```

`sequence` is the durable cursor. It is monotonically increasing for the local
database and is used as the SSE `id`. Consumers must ignore unknown event types
and fields to remain forward compatible.

Stable event families currently consumed by the UI:

- `user.message`
- `run.started`, `run.success`, `run.error`, `run.interrupted`,
  `run.cancelled`, `run.timeout`
- `message.delta`, `message.completed`
- `agent.state.updated`, `agent.custom`, `agent.metadata`
- `todo.updated`, `sandbox.bound`
- `artifact.created`, `artifact.updated`
- `widget.ready`, `usage.updated`, `cost.warning`
- `interrupt.requested`, `interrupt.resumed`
- `steering.accepted`, `steering.delivered`, `steering.reclaimed`

`source_event_key` is stable for the same runtime fact. The database uniqueness
constraint and deterministic event ID make replay and rejoin idempotent.
`DomainEvent` and its Outbox row are committed in one transaction.

Cancellation has an explicit product intent. Before requesting Agent Server
cancellation, the control plane durably sets `cancel_requested=true`. Agent
Server currently reports the resulting runtime state as `interrupted`; the
adapter maps that state to `run.cancelled` only when the intent flag is present.
An interrupt caused by HITL or another runtime condition remains
`run.interrupted`. Both the synchronous cancel endpoint and the stream bridge
use the same deterministic terminal key, so a race still produces one terminal
event.

SSE reconnect:

```http
GET /api/threads/{thread_id}/events
Last-Event-ID: 42
```

or:

```http
GET /api/threads/{thread_id}/events?after=42
```

Agent Server stream modes and Deep Agents internal event versions are adapters,
not public product contracts.

The browser parses this envelope before reduction. Live SSE frames and replayed
frames use the same pure reducer; duplicate `id` values are ignored and events
are projected in durable `sequence` order.
