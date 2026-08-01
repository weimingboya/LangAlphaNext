import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import { ContextPanel } from "./ContextPanel";

describe("ContextPanel", () => {
  test("renders the latest todo plan with progress and statuses", () => {
    const html = renderToStaticMarkup(
      <ContextPanel
        assets={[]}
        citations={[]}
        onClose={() => undefined}
        onOpenFile={() => undefined}
        onReference={() => undefined}
        todos={[
          { content: "Collect filings", status: "completed" },
          { content: "Calculate growth", status: "in_progress" },
          { content: "Write conclusion", status: "pending" },
        ]}
      />,
    );

    expect(html).toContain('id="plan-heading">Plan</h3>');
    expect(html).toContain('class="plan-progress">1/3</span>');
    expect(html).toContain("Collect filings");
    expect(html).toContain("Calculate growth");
    expect(html).toContain("In progress");
  });

  test("omits the plan section when there are no todos", () => {
    const html = renderToStaticMarkup(
      <ContextPanel
        assets={[]}
        citations={[]}
        onClose={() => undefined}
        onOpenFile={() => undefined}
        onReference={() => undefined}
        todos={[]}
      />,
    );

    expect(html).not.toContain('id="plan-heading"');
  });
});
