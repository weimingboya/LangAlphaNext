import { describe, expect, test } from "vitest";

import {
  DEFAULT_THREAD_TITLE,
  isDefaultThreadTitle,
  threadTitleFromMessage,
} from "./thread-title";

describe("thread titles", () => {
  test("uses a normalized first question", () => {
    expect(threadTitleFromMessage("  Compare   Nvidia\nand AMD margins  ")).toBe(
      "Compare Nvidia and AMD margins",
    );
  });

  test("truncates long titles at a readable boundary", () => {
    expect(
      threadTitleFromMessage(
        "Compare Nvidia and AMD revenue growth, gross margin trends, and valuation",
      ),
    ).toBe("Compare Nvidia and AMD revenue growth, gross…");
  });

  test("handles long CJK titles without requiring spaces", () => {
    const title = threadTitleFromMessage(
      "请详细比较英伟达和AMD最近五年的收入增长毛利率现金流估值水平以及人工智能芯片市场份额变化并给出投资风险提示",
    );
    expect(title.endsWith("…")).toBe(true);
    expect(Array.from(title).length).toBe(48);
  });

  test("recognizes the default title case-insensitively", () => {
    expect(isDefaultThreadTitle("New Research")).toBe(true);
    expect(threadTitleFromMessage("   ")).toBe(DEFAULT_THREAD_TITLE);
  });
});
