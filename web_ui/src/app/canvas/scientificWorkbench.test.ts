import { describe, expect, it } from "vitest";
import { chartableScienceTypes, fieldText, matchesSource, parseInputs, sourceOptions, type ScienceField, type ScienceResult, type ScienceState } from "./scientificWorkbench";

const limits = { points: 3, columns: 2, cells: 4, text: 3 };
function parse(kind: ScienceField["kind"], raw: string) {
  return parseInputs({ id: "op", title: "Test", description: "", example: {}, fields: [
    { key: "a", label: "A", unit: "", description: "", kind },
  ] }, { a: raw }, limits).a;
}

describe("scientific catalog input and parent contracts", () => {
  it("checks the published shape limits before sending requests, including Unicode code point counts", () => {
    expect(parse("vector", "[1,2,3]")).toEqual([1, 2, 3]);
    expect(() => parse("vector", "[1,2,3,4]")).toThrow(/3 entries/);
    expect(() => parse("matrix", "[[1,2,3]]")).toThrow(/2 columns/);
    expect(() => parse("matrix", "[[1,2],[3,4],[5,6]]")).toThrow(/4 cells/);
    expect(parse("text", "🌍🌍🌍")).toBe("🌍🌍🌍");
    expect(() => parse("text", "🌍🌍🌍x")).toThrow(/3 characters/);
    expect(parse("integer", "9007199254740991")).toBe(Number.MAX_SAFE_INTEGER);
    expect(() => parse("integer", "9007199254740990.5")).toThrow(/integer notation/);
    expect(() => parse("integer", "9007199254740992")).toThrow();
    expect(() => parse("number", "1e999")).toThrow();
    expect(() => parse("vector", "[1e999]")).toThrow();
  });

  it("validates numeric spellings before rounding or underflow in scalars and both array shapes", () => {
    for (const kind of ["number", "vector", "matrix"] as const) {
      const shaped = (s: string) => kind === "number" ? s : kind === "vector" ? "[" + s + "]" : "[[" + s + "]]";
      for (const token of ["9007199254740993", "-9.007199254740993e15", "1.0000000000000001e17"]) {
        expect(() => parse(kind, shaped(token))).toThrow(/integer count/);
      }
      for (const token of ["1e-999", "-4e-4000", "1e-" + "9".repeat(10000)]) {
        expect(() => parse(kind, shaped(token))).toThrow(/too small/);
      }
      for (const token of ["9.007199254740992e15", "-1e20", "0.1", "5e-324", "0e-" + "9".repeat(10000)]) {
        expect(() => parse(kind, shaped(token))).not.toThrow();
      }
    }
  });

  it("round-trips a stored large integral value whose shortest decimal spells a different integer", () => {
    const number = 1000000000000000128;
    expect(String(number)).toBe("1000000000000000100");
    const field: ScienceField = { key: "a", label: "A", kind: "matrix", unit: "", description: "" };
    const raw = fieldText(field, [[number], [-number]]);
    expect(raw).toBe("[[1000000000000000128],[-1000000000000000128]]");
    expect(parse("matrix", raw)).toEqual([[number], [-number]]);
  });

  it("distinguishes like-named parents and a table column from a same-named plot axis", () => {
    const contents: ScienceState = { scienceVersion: 1, packId: "core", operations: [
      { id: "op", title: "Samples", description: "", example: {}, fields: [] },
    ], latest: { operationId: "op", inputs: {}, summary: "", values: {}, table: { columns: ["time"], rows: [[2], [0]] },
      plot: { title: "Signal", x: [0, 2], y: [2, 1], xLabel: "time", yLabel: "value" },
      provenance: { resultHash: "n", receiptHash: "r" },
    } };
    const options = sourceOptions(["node1", "node2"].map((nodeId) => ({
      nodeId, title: "Scientific Core", raw: JSON.stringify({ ...contents, nodeKey: nodeId }),
    })));
    expect(new Set(options.map((opt) => opt.label)).size).toBe(options.length);
    expect(options.find((opt) => opt.label === "Scientific Core (node1) · time (table column 1)")?.value).toEqual([2, 0]);
    const axis = options.find((opt) => opt.label === "Scientific Core (node1) · time (plot x)")!;
    expect(axis.value).toEqual([0, 2]);
    expect(matchesSource(axis, { ...axis.reference, nodeId: "old" })).toBe(true);
    expect(matchesSource(axis, { ...axis.reference, receiptHash: "previous" })).toBe(false);
  });
});


describe("chartable scientific results", () => {
  const result: ScienceResult = {
    operationId: "plot_xy", inputs: {}, summary: "", values: {},
    plot: { title: "Measured", x: [0, 1, 2], y: [1, 2, 3], xLabel: "Time (s)", yLabel: "Value (V)" },
    table: { columns: ["X", "Y"], rows: [[0, 1], [1, 2], [2, 3]] },
    provenance: { packId: "core", resultHash: "x" },
  };
  it("offers scatter and fitting only for genuinely compatible result shapes", () => {
    expect(chartableScienceTypes(result)).toEqual(["scatter", "boxplot", "heatmap"]);
    expect(chartableScienceTypes({ ...result, operationId: "linear_regression",
      provenance: { ...result.provenance, packId: "statistics" },
      inputs: { x: [0, 1, 2], y: [1, 2, 4] } })).toEqual([
      "line", "scatter", "boxplot", "heatmap",
    ]);
    expect(chartableScienceTypes({ ...result, plot: undefined, table: undefined,
      values: { estimate: 42, combined_standard_uncertainty: 0.2 } })).toEqual(["scatter"]);
    expect(chartableScienceTypes({ ...result, plot: undefined, table: undefined,
      values: { count: 10 } })).toEqual([]);
  });
  it("respects the existing chart point and matrix limits", () => {
    const long = Array.from({ length: 301 }, (_, i) => i);
    expect(chartableScienceTypes({ ...result, plot: { ...result.plot!, x: long, y: long },
      table: undefined })).toEqual([]);
    const table = { columns: Array.from({ length: 30 }, (_, i) => String(i)),
      rows: Array.from({ length: 11 }, () => Array.from({ length: 30 }, () => 1)) };
    expect(chartableScienceTypes({ ...result, plot: undefined, table })).toEqual([]);
  });
});
