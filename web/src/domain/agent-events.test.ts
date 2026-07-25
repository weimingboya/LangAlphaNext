import { describe, expect, test } from "vitest";

import {
  assistantMessageContent,
  assistantMessageText,
  buildChartModel,
  formatUsageSummary,
  initialAgentProjection,
  parseAgentEvent,
  reduceAgentEvent,
} from "./agent-events";
import {
  citationSegments,
  fileReferenceSegments,
  serializeFileReference,
} from "./message-references";
import type { AgentEvent, Asset } from "./types";

function event(overrides: Partial<AgentEvent> = {}): AgentEvent {
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

describe("AgentEvent projection", () => {
  test("preserves arrival order and deduplicates resumable IDs", () => {
    let state = initialAgentProjection();
    state = reduceAgentEvent(state, event());
    state = reduceAgentEvent(state, event());
    state = reduceAgentEvent(
      state,
      event({ id: "runtime-event-2", type: "message.delta" }),
    );
    expect(state.events.map((item) => item.id)).toEqual([
      "runtime-event-1",
      "runtime-event-2",
    ]);
    expect(state.activeRunId).toBe("run-1");
  });

  test("terminal events clear the active run", () => {
    let state = reduceAgentEvent(initialAgentProjection(), event());
    state = reduceAgentEvent(
      state,
      event({ id: "terminal:run-1:success", type: "run.success" }),
    );
    expect(state.activeRunId).toBeNull();
    expect(state.status.label).toBe("Analysis complete");
  });

  test("keeps runtime IDs without a local sequence contract", () => {
    const parsed = parseAgentEvent(event());
    expect(parsed.id).toBe("runtime-event-1");
    expect("sequence" in parsed).toBe(false);
    expect("source_event_key" in parsed).toBe(false);
  });
});

describe("Agent output rendering", () => {
  test("builds a bounded numeric chart model", () => {
    const model = buildChartModel({
      kind: "line",
      x_field: "date",
      y_fields: ["price"],
      data: [
        { date: "Mon", price: 10 },
        { date: "Tue", price: "12.5" },
      ],
    });
    expect(model.labels).toEqual(["Mon", "Tue"]);
    expect(model.series[0].values).toEqual([10, 12.5]);
    expect(model.minimum).toBe(0);
    expect(model.maximum).toBe(12.5);
  });

  test("hides tool payloads and selects the latest assistant message", () => {
    expect(
      assistantMessageText({
        value: [{ type: "tool", name: "show_widget", content: "raw tool result" }],
      }),
    ).toBe("");
    expect(
      assistantMessageText({
        value: [{ type: "ai", content: "", tool_calls: [{ name: "read_file" }] }],
      }),
    ).toBe("");
    expect(
      assistantMessageText({
        value: [
          { type: "ai", content: "The" },
          { type: "ai", content: "The report" },
          { type: "ai", content: "The report is ready." },
        ],
      }),
    ).toBe("The report is ready.");
  });

  test("formats cached input, output, and web search usage", () => {
    expect(
      formatUsageSummary({
        input_tokens: 170334,
        output_tokens: 1965,
        total_tokens: 172299,
        cached_input_tokens: 156432,
        web_search_calls: 3,
        estimated_cost_usd: null,
      }),
    ).toBe("Usage: 170.3K input (156.4K cached) · 2K output · 3 web searches");
  });

  test("preserves safe OpenAI URL citations", () => {
    const content = assistantMessageContent({
      type: "ai",
      content: [
        {
          type: "text",
          text: "Revenue increased.",
          annotations: [
            {
              type: "url_citation",
              url: "https://www.sec.gov/example",
              title: "SEC filing",
              start_index: 0,
              end_index: 17,
            },
          ],
        },
      ],
    });
    expect(content.text).toBe("Revenue increased.");
    expect(citationSegments(content.text, content.citations)).toEqual([
      { kind: "text", value: "Revenue increased" },
      {
        kind: "citation",
        index: 1,
        url: "https://www.sec.gov/example",
        title: "SEC filing",
      },
      { kind: "text", value: "." },
    ]);
  });

  test("rejects executable citation URLs", () => {
    expect(
      citationSegments("Unsafe", [
        {
          url: "javascript:alert(1)",
          title: "bad",
          start_index: 0,
          end_index: 6,
        },
      ]),
    ).toEqual([{ kind: "text", value: "Unsafe" }]);
  });
});

test("workspace mentions serialize to sandbox paths and render friendly segments", () => {
  const reference = {
    id: "8f32",
    filename: "holdings.csv",
    sandbox_path: null,
  } as Asset;
  const serialized = serializeFileReference(
    "请分析 @holdings.csv，找出风险最大的仓位。",
    reference,
  );
  expect(serialized).toBe(
    '请分析 @file("/workspace/input/assets/8f32/holdings.csv")，找出风险最大的仓位。',
  );
  expect(fileReferenceSegments(serialized)).toEqual([
    { kind: "text", value: "请分析 " },
    { kind: "file", path: "/workspace/input/assets/8f32/holdings.csv" },
    { kind: "text", value: "，找出风险最大的仓位。" },
  ]);
});
