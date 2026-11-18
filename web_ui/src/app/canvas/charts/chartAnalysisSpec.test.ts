import { describe, expect, it } from "vitest";
import {
  canonicalizeChartSpec, canonicalizeChartAnalysis, ChartSpecError,
  chartAnalysisOf, type ChartAnnotation,
} from "../chartSpec";

const data = {
  type: "scatter" as const,
  title: "Untouched", xValues: [1, 10, 100], values: [2, 3, 4],
  xScale: "log10" as const, yScale: "linear" as const, xUnit: "mg/L", yUnit: "mV",
};

const annotations: ChartAnnotation[] = [
  { kind: "label", x: 10, y: 3, text: "Threshold" },
  { kind: "arrow", x: 1, y: 2, x2: 10, y2: 3 },
  { kind: "region", axis: "x", from: 1, to: 100 },
  { kind: "region", axis: "y", from: 2, to: 3 },
];

describe("optional chart analysis canonicalization", () => {
  it("does not change default data or add analysis to untouched charts", () => {
    const before = JSON.stringify(data);
    const normalized = canonicalizeChartSpec(data);
    expect(normalized.values).toEqual([2, 3, 4]);
    expect(normalized.xValues).toEqual([1, 10, 100]);
    expect((normalized as { analysis?: unknown }).analysis).toBeUndefined();
    expect(JSON.stringify(data)).toEqual(before);
    expect(chartAnalysisOf(normalized).annotations).toEqual([]);
  });

  it("preserves data, annotation coordinates, units, and locked ranges exactly", () => {
    const annotated = { ...data, analysis: {
      lockX: true, lockY: false, xRange: [1, 100],
      annotations,
    } };
    const before = JSON.stringify(annotated);
    const normalized = canonicalizeChartSpec(annotated);
    expect(normalized.xUnit).toBe("mg/L");
    expect(normalized.yUnit).toBe("mV");
    expect(normalized.values).toEqual([2, 3, 4]);
    expect(chartAnalysisOf(normalized)).toEqual({
      lockX: true, lockY: false, xRange: [1, 100], annotations,
    });
    expect(JSON.stringify(annotated)).toEqual(before);
    expect(canonicalizeChartSpec(normalized)).toEqual(normalized);
  });

  it("rejects malformed annotations, log bounds, unknown properties and disabled kinds", () => {
    const rejected: unknown[] = [
      { lockX: true, annotations: [] },
      { xRange: [0, 100], annotations: [] },
      { xRange: [100, 10], annotations: [] },
      { annotations: [{ kind: "label", x: 0, y: 2, text: "not positive" }] },
      { annotations: [{ kind: "region", axis: "x", from: -1, to: 2 }] },
      { annotations: [{ kind: "region", axis: "y", from: 3, to: 2 }] },
      { annotations: [{ kind: "arrow", x: 2, y: 3, x2: Infinity, y2: 3 }] },
      { annotations: [{ kind: "label", x: 2, y: 3, text: "" }] },
      { annotations: [{}] },
      { annotations: Array.from({ length: 61 }, () => annotations[0]) },
      { nonsense: 1, annotations: [] },
    ];
    for (const analysis of rejected) {
      expect(() => canonicalizeChartSpec({ ...data, analysis })).toThrow(ChartSpecError);
    }
    expect(() => canonicalizeChartAnalysis(
      { annotations: [] }, "bar", "linear", "linear",
    )).toThrow(/only supported/);
  });

  it("accepts zero on linear axes and strictly positive values on log axes", () => {
    expect(canonicalizeChartAnalysis({
      annotations: [{ kind: "label", x: 0, y: -1, text: "Origin" }],
    }, "line")).toMatchObject({
      annotations: [{ kind: "label", x: 0, y: -1, text: "Origin" }],
    });
    expect(() => canonicalizeChartAnalysis({
      annotations: [{ kind: "label", x: 0, y: 2, text: "Invalid log x" }],
    }, "scatter", "log10", "linear")).toThrow(/positive/);
  });
});
