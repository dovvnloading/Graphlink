import { describe, expect, it } from "vitest";
import { raincloudDensity, raincloudJitter } from "./raincloud";
import { canonicalizeChartSpec } from "../chartSpec";

describe("raincloud layer", () => {
  it("preserves data coordinates and yields a normalized symmetric cloud", () => {
    const values = [1, 2, 3, 4, 5];
    const density = raincloudDensity(values);
    expect(density).toHaveLength(65);
    expect(density[0][0]).toBe(1);
    expect(density[64][0]).toBe(5);
    expect(Math.max(...density.map(p => p[1]))).toBe(1);
    expect(density[0][1]).toBeCloseTo(density[64][1], 12);
    expect(raincloudDensity([7, 7])).toEqual([]);
    expect(values).toEqual([1, 2, 3, 4, 5]);
    const jitter = Array.from({ length: 300 }, (_, i) => raincloudJitter(i));
    expect(new Set(jitter).size).toBe(300);
    expect(Math.min(...jitter)).toBeGreaterThanOrEqual(0.15);
    expect(Math.max(...jitter)).toBeLessThanOrEqual(0.43);
  });

  it("accepts the opt-in only on boxplots", () => {
    const spec = canonicalizeChartSpec({ type: "boxplot", groups: [{ label: "A", values: [1, 2, 3] }],
      analysis: { distribution: "raincloud" } });
    expect(spec.analysis?.distribution).toBe("raincloud");
    expect(canonicalizeChartSpec(spec)).toEqual(spec);
    expect(() => canonicalizeChartSpec({ type: "histogram", values: [1, 2, 3],
      analysis: { distribution: "raincloud" } })).toThrow(/Raincloud/);
  });
});
