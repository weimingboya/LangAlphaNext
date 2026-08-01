import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import type { AgentProjection } from "../../domain/types";
import { Transcript } from "./Transcript";

const branch = {
  current_checkpoint_id: "checkpoint-1",
  current_index: 0,
  options: [
    {
      checkpoint_id: "checkpoint-1",
      preview: "Question",
      created_at: "2026-01-01T00:00:00Z",
    },
  ],
  can_edit_latest: true,
};

describe("Transcript activity", () => {
  test("offers editing only on the latest user message with compact branch controls", () => {
    const projection: AgentProjection = {
      activeRunId: null,
      status: { label: "Analysis complete", mode: "idle" },
      events: [
        {
          id: "user-1",
          thread_id: "thread-1",
          run_id: "run-1",
          type: "user.message",
          payload: { content: "First question" },
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "assistant-1",
          thread_id: "thread-1",
          run_id: "run-1",
          type: "message.completed",
          payload: {
            messages: [
              { id: "answer-1", role: "assistant", content: "First answer" },
            ],
          },
          created_at: "2026-01-01T00:00:01Z",
        },
        {
          id: "user-2",
          thread_id: "thread-1",
          run_id: "run-2",
          type: "user.message",
          payload: { content: "Latest question" },
          created_at: "2026-01-01T00:00:02Z",
        },
      ],
    };
    const html = renderToStaticMarkup(
      <Transcript
        assets={[]}
        branch={{
          ...branch,
          options: [
            branch.options[0],
            {
              checkpoint_id: "checkpoint-2",
              preview: "Edited question",
              created_at: "2026-01-01T00:00:03Z",
            },
          ],
        }}
        htmlPreview={null}
        onDownload={async () => undefined}
        onEditLatest={async () => undefined}
        onResume={async () => undefined}
        onSelectBranch={async () => undefined}
        projection={projection}
      />,
    );

    expect(html.match(/aria-label="Edit latest message"/g)).toHaveLength(1);
    expect(html).toContain('aria-label="Previous branch"');
    expect(html).toContain('aria-label="Next branch"');
    expect(html).toContain("1 / 2");
  });

  test("keeps the complete reasoning summary in an expandable disclosure", () => {
    const detail = `First paragraph.\n\n${"Full analysis content. ".repeat(20)}`;
    const projection: AgentProjection = {
      activeRunId: null,
      status: { label: "Analysis complete", mode: "idle" },
      events: [
        {
          id: "activity-1",
          thread_id: "thread-1",
          run_id: "run-1",
          type: "activity.updated",
          payload: {
            id: "reasoning:run-1:reasoning-1",
            kind: "reasoning",
            title: "Analysis",
            detail,
            status: "complete",
          },
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "terminal:run-1:success",
          thread_id: "thread-1",
          run_id: "run-1",
          type: "run.success",
          payload: {},
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    };

    const html = renderToStaticMarkup(
      <Transcript
        assets={[]}
        branch={branch}
        htmlPreview={null}
        onDownload={async () => undefined}
        onEditLatest={async () => undefined}
        onResume={async () => undefined}
        onSelectBranch={async () => undefined}
        projection={projection}
      />,
    );

    expect(html).toContain('class="activity-reasoning"');
    expect(html).toContain("Toggle full analysis");
    expect(html).toContain("Full analysis content.");
    expect(html).not.toContain("Show full analysis");
    expect(html).not.toContain(">Research complete</span>");
    expect(html).not.toContain("…");
  });

  test("settles a running analysis when the next tool starts", () => {
    const projection: AgentProjection = {
      activeRunId: "run-1",
      status: { label: "Analysis running", mode: "active" },
      events: [
        {
          id: "activity-reasoning",
          thread_id: "thread-1",
          run_id: "run-1",
          type: "activity.updated",
          payload: {
            id: "reasoning:run-1:reasoning-1",
            kind: "reasoning",
            title: "Analysis",
            detail: "Choose the next research tool",
            status: "running",
          },
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "activity-tool",
          thread_id: "thread-1",
          run_id: "run-1",
          type: "activity.updated",
          payload: {
            id: "tool:run-1:tool-1",
            kind: "tool",
            title: "Search the web",
            status: "running",
          },
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    };

    const html = renderToStaticMarkup(
      <Transcript
        assets={[]}
        branch={branch}
        htmlPreview={null}
        onDownload={async () => undefined}
        onEditLatest={async () => undefined}
        onResume={async () => undefined}
        onSelectBranch={async () => undefined}
        projection={projection}
      />,
    );

    expect(html).toContain('class="activity-event complete reasoning"');
    expect(html).toContain('class="activity-event running tool"');
    expect(html).not.toContain('class="activity-event running reasoning"');
  });

  test("settles a running analysis visually when the final response is visible", () => {
    const projection: AgentProjection = {
      activeRunId: "run-1",
      status: { label: "Analysis complete", mode: "idle" },
      events: [
        {
          id: "activity-1",
          thread_id: "thread-1",
          run_id: "run-1",
          type: "activity.updated",
          payload: {
            id: "reasoning:run-1:reasoning-1",
            kind: "reasoning",
            title: "Analysis",
            detail: "Final reasoning summary",
            status: "running",
          },
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "message-completed-1",
          thread_id: "thread-1",
          run_id: "run-1",
          type: "message.completed",
          payload: {
            messages: [
              { id: "assistant-1", role: "assistant", content: "Final answer" },
            ],
          },
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    };

    const html = renderToStaticMarkup(
      <Transcript
        assets={[]}
        branch={branch}
        htmlPreview={null}
        onDownload={async () => undefined}
        onEditLatest={async () => undefined}
        onResume={async () => undefined}
        onSelectBranch={async () => undefined}
        projection={projection}
      />,
    );

    expect(html).toContain('class="activity-event complete reasoning"');
    expect(html).not.toContain('class="activity-event running reasoning"');
  });
});
