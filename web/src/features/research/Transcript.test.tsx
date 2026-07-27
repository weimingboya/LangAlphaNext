import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import type { AgentProjection } from "../../domain/types";
import { Transcript } from "./Transcript";

describe("Transcript activity", () => {
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
        htmlPreview={null}
        onDownload={async () => undefined}
        onResume={async () => undefined}
        projection={projection}
      />,
    );

    expect(html).toContain('class="activity-reasoning"');
    expect(html).toContain("Toggle full analysis");
    expect(html).toContain("Full analysis content.");
    expect(html).not.toContain("…");
  });
});
