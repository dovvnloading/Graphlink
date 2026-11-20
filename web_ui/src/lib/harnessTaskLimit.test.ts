import { describe, expect, it, vi } from "vitest";
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

  it("counts combining marks and unpaired surrogates as individual code points", () => {
    expect(exceedsHarnessTaskLimit("e\u0301".repeat(MAX_HARNESS_TASK_LENGTH / 2))).toBe(false);
    expect(exceedsHarnessTaskLimit("e\u0301".repeat(MAX_HARNESS_TASK_LENGTH / 2) + "x")).toBe(true);
    expect(exceedsHarnessTaskLimit("\uD800".repeat(MAX_HARNESS_TASK_LENGTH))).toBe(false);
    expect(exceedsHarnessTaskLimit("\uD800".repeat(MAX_HARNESS_TASK_LENGTH + 1))).toBe(true);
  });

  it("stops before visiting the rest of a very large pasted task", () => {
    let visited = 0;
    // The iterator throws if validation tries to inspect even one point
    // beyond the first disallowed point (20,001). An Array.from gate
    // would consume the whole iterator and fail this regression.
    const iteratorSpy = vi.spyOn(String.prototype, Symbol.iterator).mockImplementation(function* () {
      for (let i = 0; i <= MAX_HARNESS_TASK_LENGTH; i++) {
        visited++;
        yield "x";
      }
      throw new Error("task validation walked past the limit");
    });
    let oversized: boolean;
    try {
      oversized = exceedsHarnessTaskLimit("x".repeat(5_000_000));
    } finally {
      iteratorSpy.mockRestore();
    }
    expect(oversized).toBe(true);
    expect(visited).toBe(MAX_HARNESS_TASK_LENGTH + 1);
  });
});
