import { describe, expect, it } from "vitest";
import { pastedArray } from "./scientificArrayPaste";
import type { ScienceField } from "./scientificWorkbench";

const vector: ScienceField = { key: "x", kind: "vector", unit: "", description: "", label: "Time" };
const matrix: ScienceField = { ...vector, kind: "matrix" };

describe("spreadsheet input", () => {
  it("preserves numeric spellings and unit-bearing cells when transcribing a grid", () => {
    expect(pastedArray("+.5000\t3e-6\n0002\t10 mm\n", matrix)).toBe('[[0.5000,3e-6],[2,"10 mm"]]');
    expect(pastedArray("0.12345678901234567\n2\n", vector)).toBe("[0.12345678901234567,2]");
  });
  it("reports invalid cell coordinates and rejects ragged matrices or unsafe rounding", () => {
    expect(() => pastedArray("1\t2\n3\tNA", matrix)).toThrow("Row 2, column 2");
    expect(() => pastedArray("1\t2\n3", matrix)).toThrow("Row 2 has 1 cells");
    expect(() => pastedArray("1\t2\n3\t4", vector)).toThrow("one row or one column");
    expect(() => pastedArray("9007199254740993", vector)).toThrow("exactly");
  });
});
