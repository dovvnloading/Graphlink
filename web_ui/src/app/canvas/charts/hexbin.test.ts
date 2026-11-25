import { describe, expect, it } from "vitest";
import { hexbinPoints, hexagonVertices } from "./hexbin";
import { canonicalizeChartSpec } from "../chartSpec";

describe("hexagonal scientific density", () => {
  it("counts every visible observation without sampling or editing measurements", () => {
    const xs = Array.from({ length: 10000 }, (_, i) => i / 1000);
    const ys = xs.map(x => (Math.floor(x) % 2) + 0.2);
    const before = JSON.stringify({ xs, ys });
    const bins = hexbinPoints(xs, ys, [0, 10], [0, 2], 600, 400);
    expect(bins.length).toBeLessThan(1200);
    expect(bins.reduce((s, b) => s + b.count, 0)).toBe(10000);
    expect(Math.max(...bins.map(b => b.count))).toBeGreaterThan(1);
    const zoomed = hexbinPoints(xs, ys, [2, 3], [0, 2], 600, 400);
    expect(zoomed.reduce((s, b) => s + b.count, 0)).toBe(1001);
    expect(JSON.stringify({ xs, ys })).toBe(before);
    expect(hexagonVertices(0, 0, 10).split(" ")).toHaveLength(6);
  });

  it("validates density only on a single-series linear scatter", () => {
    const raw = { type: "scatter", xValues: [0, 1, 2], values: [1, 2, 3],
      analysis: { density: "hexbin" } };
    const spec = canonicalizeChartSpec(raw);
    expect(spec.analysis?.density).toBe("hexbin");
    expect(canonicalizeChartSpec(spec)).toEqual(spec);
    for (const invalid of [
      { ...raw, type: "line", labels: ["a", "b", "c"] },
      { ...raw, xScale: "log10", xValues: [1, 2, 3] },
      { ...raw, series: [
        { name: "A", values: [1, 2, 3] },
        { name: "B", values: [2, 3, 4] },
      ], values: undefined },
      { ...raw, analysis: { density: "unknown" } },
    ]) expect(() => canonicalizeChartSpec(invalid)).toThrow();
  });
});
