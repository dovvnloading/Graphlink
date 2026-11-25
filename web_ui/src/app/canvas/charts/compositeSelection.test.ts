import { describe, expect, it } from "vitest";
import { canonicalizeChartSpec } from "../chartSpec";
import { compositeBrushBounds, compositeRowsInRect, brushPixelToData } from "./compositeSelection";

const SOURCE = canonicalizeChartSpec({ type: "composite", version: 2,
  composite: {
    numbers: {
      x: [0, 1, 2, 3, 4, 5],
      y: [0, 10, 20, 30, 40, 50],
      low: [-1, 9, 19, 29, 39, 49],
      high: [1, 11, 21, 31, 41, 51],
    },
    categories: { group: ["A", "A", "A", "B", "B", "B"] },
    facetBy: "group",
    layers: [
      { mark: "band", x: "x", y: "low", y2: "high" },
      { mark: "line", x: "x", y: "y" },
      { mark: "point", x: "x", y: "y" },
    ],
  },
}).composite!;

describe("source-row brushing", () => {
  it("selects all original rows inside a panel rectangle, not sampled glyphs", () => {
    const snapshot = JSON.stringify(SOURCE);
    expect(compositeRowsInRect(SOURCE, "A", {
      x: [0, 2.5], y: [-2, 25],
    })).toEqual([0, 1, 2]);
    expect(compositeRowsInRect(SOURCE, "B", {
      x: [0, 5], y: [25, 60],
    })).toEqual([3, 4, 5]);
    expect(compositeRowsInRect(SOURCE, "B", {
      x: [0, 5], y: [-2, 25],
    })).toEqual([]);
    expect(JSON.stringify(SOURCE)).toBe(snapshot);
  });
  it("does not select by uncertainty-band lower bounds when point marks exist", () => {
    expect(compositeRowsInRect(SOURCE, "A", {
      x: [1, 1], y: [9, 9],
    })).toEqual([]);
  });
  it("supports a band-only figure by interval overlap and includes endpoints", () => {
    const spec = { ...SOURCE, layers: [SOURCE.layers[0]] };
    expect(compositeRowsInRect(spec, "A", {
      x: [1, 1], y: [9.5, 10.5],
    })).toEqual([1]);
  });
  it("preserves log and linear axis values with reversible rectangle mapping", () => {
    expect(brushPixelToData({ x: 50, y: 25 }, 100, 100,
      [1, 100], [1, 1000], true, true)).toMatchObject({
      x: 10,
    });
    expect(brushPixelToData({ x: 25, y: 75 }, 100, 100,
      [0, 10], [0, 100])).toEqual({ x: 2.5, y: 25 });
    expect(compositeBrushBounds({ x: 4, y: 8 }, { x: 1, y: 20 })).toEqual({
      x: [1, 4], y: [8, 20],
    });
  });
  it("handles 2000 aligned source rows without losing extrema or mutating input", () => {
    const rows = 2000;
    const x = Array.from({ length: rows }, (_, i) => i);
    const y = x.map(i => i === 1234 ? 5000 : i % 17);
    const spec = canonicalizeChartSpec({ type: "composite", version: 2, composite: {
      numbers: { x, y }, layers: [{ mark: "point", x: "x", y: "y" }],
    } }).composite!;
    expect(compositeRowsInRect(spec, null, {
      x: [1234, 1234], y: [4999, 5001],
    })).toEqual([1234]);
    expect(compositeRowsInRect(spec, null, {
      x: [400, 599], y: [-1, 20],
    })).toHaveLength(200);
    expect(y[1234]).toBe(5000);
  });
});
