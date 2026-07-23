import assert from "node:assert/strict";
import test from "node:test";

import {
  buildChartModel,
  initialEventProjection,
  parseDomainEvent,
  reduceDomainEvent,
  replayDomainEvents,
} from "../src/langalpha/server/static/domain-events.mjs";

function event(sequence, type, runId = "run-1", payload = {}) {
  return {
    schema_version: 1,
    delivery: "durable",
    sequence,
    id: `event-${sequence}`,
    source_event_key: `source-${sequence}`,
    project_id: "project",
    workspace_id: "workspace",
    thread_id: "thread",
    turn_id: "turn",
    run_id: runId,
    type,
    source: { agent_id: "main", parent_agent_id: null },
    payload,
    created_at: "2026-07-24T00:00:00Z",
  };
}

test("live reduction and replay produce the same projection", () => {
  const source = [
    event(1, "user.message", null, { content: "Analyze" }),
    event(2, "run.started"),
    event(3, "message.completed", "run-1", { content: "Done" }),
    event(4, "run.success"),
  ];
  const live = source.reduce(reduceDomainEvent, initialEventProjection());
  const replayed = replayDomainEvents([...source].reverse());
  assert.deepEqual(live, replayed);
  assert.equal(live.activeRunId, null);
  assert.deepEqual(live.status, { label: "Analysis complete", mode: "idle" });
});

test("duplicate delivery is idempotent", () => {
  const started = event(1, "run.started");
  const once = reduceDomainEvent(initialEventProjection(), started);
  const twice = reduceDomainEvent(once, started);
  assert.strictEqual(twice, once);
  assert.equal(twice.events.length, 1);
});

test("interrupt and explicit cancellation remain distinct", () => {
  const interrupted = replayDomainEvents([
    event(1, "run.started"),
    event(2, "run.interrupted"),
  ]);
  assert.deepEqual(interrupted.status, { label: "Waiting for input", mode: "idle" });

  const cancelled = reduceDomainEvent(interrupted, event(3, "run.cancelled"));
  assert.deepEqual(cancelled.status, { label: "Run cancelled", mode: "idle" });
});

test("invalid public event shapes are rejected", () => {
  assert.throws(
    () => parseDomainEvent({ ...event(1, "run.started"), sequence: "1" }),
    /sequence/,
  );
  assert.throws(
    () => parseDomainEvent({ ...event(1, "run.started"), payload: [] }),
    /payload/,
  );
});

test("chart widgets normalize bounded numeric series and preserve gaps", () => {
  const model = buildChartModel({
    kind: "line",
    data: [
      { date: "2026-07-22", AAPL: "1.25", MSFT: -0.5 },
      { date: "2026-07-23", AAPL: 2, MSFT: null },
      { date: "2026-07-24", AAPL: "", MSFT: "3.5" },
    ],
    x_field: "date",
    y_fields: ["AAPL", "MSFT"],
  });

  assert.deepEqual(model.labels, ["2026-07-22", "2026-07-23", "2026-07-24"]);
  assert.deepEqual(model.series, [
    { field: "AAPL", values: [1.25, 2, null] },
    { field: "MSFT", values: [-0.5, null, 3.5] },
  ]);
  assert.equal(model.minimum, -0.5);
  assert.equal(model.maximum, 3.5);
});

test("chart widgets reject incomplete or nonnumeric chart contracts", () => {
  assert.throws(
    () =>
      buildChartModel({
        kind: "bar",
        data: [{ label: "A", value: "not-a-number" }],
        x_field: "label",
        y_fields: ["value"],
      }),
    /numeric/,
  );
  assert.throws(
    () =>
      buildChartModel({
        kind: "line",
        data: [{ label: "A", value: 1 }],
        x_field: null,
        y_fields: ["value"],
      }),
    /x_field/,
  );
});
