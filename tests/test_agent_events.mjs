import assert from "node:assert/strict";
import test from "node:test";

import {
  assistantMessageText,
  buildChartModel,
  formatUsageSummary,
  initialAgentProjection,
  parseAgentEvent,
  reduceAgentEvent,
} from "../src/langalpha/server/static/agent-events.mjs";

function event(overrides = {}) {
  return {
    id: "runtime-event-1",
    thread_id: "thread-1",
    run_id: "run-1",
    type: "run.started",
    payload: {},
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

test("transient Agent Events preserve arrival order and deduplicate resumable IDs", () => {
  let state = initialAgentProjection();
  state = reduceAgentEvent(state, event());
  state = reduceAgentEvent(state, event());
  state = reduceAgentEvent(
    state,
    event({ id: "runtime-event-2", type: "message.delta" }),
  );
  assert.deepEqual(
    state.events.map((item) => item.id),
    ["runtime-event-1", "runtime-event-2"],
  );
  assert.equal(state.activeRunId, "run-1");
});

test("terminal Agent Event clears the active run", () => {
  let state = reduceAgentEvent(initialAgentProjection(), event());
  state = reduceAgentEvent(
    state,
    event({ id: "terminal:run-1:success", type: "run.success" }),
  );
  assert.equal(state.activeRunId, null);
  assert.equal(state.status.label, "Analysis complete");
});

test("Agent Event has no local sequence or durable source key contract", () => {
  const parsed = parseAgentEvent(event());
  assert.equal(parsed.id, "runtime-event-1");
  assert.equal("sequence" in parsed, false);
  assert.equal("source_event_key" in parsed, false);
});

test("chart model remains bounded and numeric", () => {
  const model = buildChartModel({
    kind: "line",
    x_field: "date",
    y_fields: ["price"],
    data: [
      { date: "Mon", price: 10 },
      { date: "Tue", price: "12.5" },
    ],
  });
  assert.deepEqual(model.labels, ["Mon", "Tue"]);
  assert.deepEqual(model.series[0].values, [10, 12.5]);
  assert.equal(model.minimum, 0);
  assert.equal(model.maximum, 12.5);
});

test("assistant message projection hides tool payloads and empty tool calls", () => {
  assert.equal(
    assistantMessageText({
      value: [
        {
          type: "tool",
          name: "show_widget",
          content: '{"title":"raw tool result"}',
        },
      ],
    }),
    "",
  );
  assert.equal(
    assistantMessageText({
      value: [{ type: "ai", content: "", tool_calls: [{ name: "read_file" }] }],
    }),
    "",
  );
  assert.equal(
    assistantMessageText({
      value: [
        {
          type: "ai",
          content: [{ type: "text", text: "The report is ready." }],
        },
      ],
    }),
    "The report is ready.",
  );
  assert.equal(
    assistantMessageText({
      value: [
        { type: "ai", content: "The" },
        { type: "ai", content: "The report" },
        { type: "ai", content: "The report is ready." },
      ],
    }),
    "The report is ready.",
  );
});

test("usage summary distinguishes cached input from output", () => {
  assert.equal(
    formatUsageSummary({
      input_tokens: 170334,
      output_tokens: 1965,
      total_tokens: 172299,
      cached_input_tokens: 156432,
      estimated_cost_usd: null,
    }),
    "Usage: 170.3K input (156.4K cached) · 2K output",
  );
});
