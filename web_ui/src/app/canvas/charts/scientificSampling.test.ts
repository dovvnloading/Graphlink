import { describe, expect, it } from "vitest";
import { scientificMarkIndices, scientificMarkBudget } from "./scientificSampling";
import { canonicalizeChartSpec } from "../chartSpec";

describe("full-resolution scientific charts", () => {
  it("retains all 10000 samples through canonicalization and selects a bounded SVG subset", () => {
    const xs = Array.from({ length: 10_000 }, (_, i) => i);
    const ys = xs.map(i => i === 5432 ? 2000 : Math.sin(i / 60));
    const spec = canonicalizeChartSpec({ type: "scatter", xValues: xs, values: ys });
    expect(spec.xValues).toHaveLength(10_000);
    expect(spec.values?.[5432]).toBe(2000);
    const indices = scientificMarkIndices(xs, ys);
    expect(indices.length).toBeLessThanOrEqual(1200);
    expect(indices).toContain(0);
    expect(indices).toContain(9999);
    expect(indices).toContain(5432);
    expect(ys[5432]).toBe(2000);
    expect(() => canonicalizeChartSpec({ type: "scatter",
      xValues: [...xs, 10_000], values: [...ys, 0] })).toThrow(/at most 10000/);
  });
  it("reselects original indices on zoom without changing observations", () => {
    const xs = Array.from({ length: 10_000 }, (_, i) => i);
    const ys = xs.map(i => Math.sin(i / 100));
    const snapshot = ys.slice();
    const viewport = { x: [1000, 1100] as [number, number], y: [-1, 1] as [number, number] };
    const indices = scientificMarkIndices(xs, ys, viewport);
    expect(indices).toEqual(Array.from({ length: 101 }, (_, i) => 1000 + i));
    expect(scientificMarkIndices(xs, ys, viewport, 20).length).toBeLessThanOrEqual(20);
    expect(ys).toEqual(snapshot);
  });
  it("respects 12-series SVG budgets and highlights errorbar extrema", () => {
    expect(scientificMarkBudget(12)).toBe(100);
    const xs = Array.from({ length: 5000 }, (_, i) => i);
    const ys = xs.map(() => 2);
    const errors = ys.map((_, i) => i === 2500 ? [100, 200] : [0, 0]);
    const indices = scientificMarkIndices(xs, ys, undefined, 200, "line", errors);
    expect(indices).toContain(2500);
    expect(indices.length).toBeLessThanOrEqual(200);
  });
});
