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
last_event_id=...)`, projects the upstream payload through a public allowlist,
and emits:

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
- `activity.updated`
- `sandbox.bound`, `asset.ready`, `asset.failed`, `widget.ready`
- `interrupt.requested`
- `run.success`, `run.error`, `run.interrupted`, `run.cancelled`
- internal `stream.cursor` frames, which only advance resumable SSE state

Message events contain only user/assistant text and validated HTTP(S)
citations. Tool calls, tool results, provider metadata and encrypted reasoning
never cross the product API boundary.

An upstream frame without an ID receives a content-derived `volatile:*` ID.
After the upstream stream ends, the BFF emits
`terminal:<run_id>:<status>`. The browser preserves arrival order and
deduplicates IDs; it does not invent a durable cursor.

`activity.updated` is a lightweight BFF projection used by the progress UI. Its
payload has a stable activity `id`, `kind` (`reasoning`, `tool`, or `subagent`),
`title`, `status`, and an optional short `detail`. Reasoning entries contain
only the model-provided reasoning summary. Tool results include a bounded
summary only for high-value research outputs; other tools simply transition to
complete. The first public frame projected from each upstream frame carries the
upstream SSE cursor; additional derived frames omit `id:`. `Last-Event-ID`
therefore always remains an Agent Server cursor.

`asset.ready` is emitted only after Storage upload and Asset-row persistence
succeed. `sandbox.bound` is informational: the Agent host itself persists the
binding to Thread metadata.

## Reload

```http
GET /api/threads/{thread_id}/snapshot
```

The snapshot combines:

- public user/assistant messages and server-projected activities;
- Agent Server todos and checkpoint interrupts;
- Agent Server run history and native status;
- widgets reconstructed server-side from `show_widget` ToolMessages;
- AI message usage metadata;
- OpenAI `web_search_call` actions, counted separately from model tokens;
- ready/uploading/failed Supabase Assets for the Thread.

It never scans Daytona to reconstruct durable product state.

Responses API text annotations are projected as inline citations. The browser
accepts only `http:` and `https:` URLs and opens them with
`noopener noreferrer`. Raw tool payloads and encrypted reasoning remain hidden;
model-provided reasoning summaries appear in the activity timeline.
