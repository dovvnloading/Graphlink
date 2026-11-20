import { describe, expect, it } from "vitest";
import { exceedsHarnessTaskLimit, MAX_HARNESS_TASK_LENGTH } from "./harnessTaskLimit";

describe("exceedsHarnessTaskLimit", () => {
  it("uses Python-compatible Unicode code points at the exact 20k boundary", () => {
    const atLimit = "😀".repeat(MAX_HARNESS_TASK_LENGTH);
    expect(atLimit.length).toBe(2 * MAX_HARNESS_TASK_LENGTH);
    expect(exceedsHarnessTaskLimit(atLimit)).toBe(false);
    expect(exceedsHarnessTaskLimit(atLimit + "😀")).toBe(true);
    expect(exceedsHarnessTaskLimit("x".repeat(MAX_HARNESS_TASK_LENGTH))).toBe(false);
    expect(exceedsHarnessTaskLimit("x".repeat(MAX_HARNESS_TASK_LENGTH + 1))).toBe(true);
  });
});
