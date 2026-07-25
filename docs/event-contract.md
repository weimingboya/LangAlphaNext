# Agent Event and snapshot contract

Agent Server owns durable runtime state and resumable cursors. The BFF does not
store an event log.

## Live stream

```http
GET /api/threads/{thread_id}/runs/{run_id}/stream
Authorization: Bearer <supabase-access-token>
Last-Event-ID: <agent-server-event-id>
```

The BFF calls `runs.join_stream(cancel_on_disconnect=False,
last_event_id=...)`, redacts the payload and emits:

```json
{
  "id": "agent-server-event-id",
  "thread_id": "langgraph-thread-id",
  "run_id": "langgraph-run-id",
  "type": "message.completed",
  "payload": {},
  "created_at": "2026-07-25T00:00:00Z"
}
```

Stable product types are:

- `message.delta`, `message.completed`
- `state.updated`, `agent.custom`, `agent.metadata`
- `sandbox.bound`, `asset.ready`, `asset.failed`, `widget.ready`
- `interrupt.requested`
- `run.success`, `run.error`, `run.interrupted`, `run.cancelled`

An upstream frame without an ID receives a content-derived `volatile:*` ID.
After the upstream stream ends, the BFF emits
`terminal:<run_id>:<status>`. The browser preserves arrival order and
deduplicates IDs; it does not invent a durable cursor.

`asset.ready` is emitted only after Storage upload and Asset-row persistence
succeed. `sandbox.bound` is informational: the Agent host itself persists the
binding to Thread metadata.

## Reload

```http
GET /api/threads/{thread_id}/snapshot
```

The snapshot combines:

- Agent Server state for messages, todos and checkpoint interrupts;
- Agent Server run history and native status;
- `show_widget` ToolMessages;
- AI message usage metadata;
- OpenAI `web_search_call` actions, counted separately from model tokens;
- ready/uploading/failed Supabase Assets for the Thread.

It never scans Daytona to reconstruct durable product state.

Responses API text annotations are projected as inline citations. The browser
accepts only `http:` and `https:` URLs and opens them with
`noopener noreferrer`; tool payloads and reasoning blocks remain hidden.
