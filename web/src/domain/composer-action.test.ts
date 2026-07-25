import { describe, expect, test } from "vitest";

import { composerAction } from "./composer-action";

describe("composer primary action", () => {
  test("starts an idle thread with enqueue", () => {
    expect(composerAction(null, "Research Nvidia")).toBe("enqueue");
  });

  test("stops the active run when the composer is empty", () => {
    expect(composerAction("run-1", "   ")).toBe("cancel");
  });

  test("interrupts the active run when a new direction is provided", () => {
    expect(composerAction("run-1", "Focus on cash flow")).toBe("interrupt");
  });
});
