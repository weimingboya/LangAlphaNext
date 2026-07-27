import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import { MarkdownMessage } from "./MarkdownMessage";

describe("MarkdownMessage", () => {
  test("renders GFM structure and removes raw HTML", () => {
    const html = renderToStaticMarkup(
      <MarkdownMessage
        assets={[]}
        citations={[]}
        text={[
          "## Summary",
          "",
          "**Revenue grew.**",
          "",
          "| Metric | Value |",
          "| --- | ---: |",
          "| Revenue | $10B |",
          "",
          "<script>alert('unsafe')</script>",
          "",
          "[unsafe](javascript:alert(1))",
        ].join("\n")}
      />,
    );
    expect(html).toContain("<h2>Summary</h2>");
    expect(html).toContain("<strong>Revenue grew.</strong>");
    expect(html).toContain("<table>");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("javascript:");
  });

  test("renders safe citations and friendly workspace filenames", () => {
    const html = renderToStaticMarkup(
      <MarkdownMessage
        assets={[
          {
            id: "asset-1",
            owner_id: "owner-1",
            project_id: "project-1",
            role: "input",
            status: "ready",
            logical_key: "input:asset-1",
            object_path: "owner/thread/file",
            sandbox_path: "/workspace/input/assets/asset-1/holdings.csv",
            filename: "holdings.csv",
            media_type: "text/csv",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ]}
        citations={[
          {
            url: "https://www.sec.gov/example",
            title: "SEC filing",
            start_index: null,
            end_index: null,
          },
        ]}
        text={'Reviewed @file("/workspace/input/assets/asset-1/holdings.csv").'}
      />,
    );
    expect(html).toContain("<code>@holdings.csv</code>");
    expect(html).toContain('href="https://www.sec.gov/example"');
    expect(html).toContain("SEC filing");
  });

  test("renders inline and display math with KaTeX", () => {
    const html = renderToStaticMarkup(
      <MarkdownMessage
        assets={[]}
        citations={[]}
        text={[
          "The ratio is $$289.57 \\div 30.35 \\approx 9.5\\text{倍}$$.",
          "",
          "$$",
          "\\frac{289.57}{30.35} \\approx 9.5",
          "$$",
        ].join("\n")}
      />,
    );
    expect(html).toContain('class="katex"');
    expect(html).toContain('class="katex-display"');
    expect(html).toContain("289.57");
  });

  test("keeps financial dollar amounts as text", () => {
    const html = renderToStaticMarkup(
      <MarkdownMessage
        assets={[]}
        citations={[]}
        text="Revenue rose from $10B to $12B."
      />,
    );
    expect(html).toContain("Revenue rose from $10B to $12B.");
    expect(html).not.toContain('class="katex"');
  });
});
