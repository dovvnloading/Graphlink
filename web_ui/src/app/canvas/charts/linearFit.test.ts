import { describe, expect, it } from "vitest";
import { canonicalizeChartSpec } from "../chartSpec";
import { linearFits, regressionYExtent } from "./linearFit";

const source = { type: "scatter", xValues: [0, 1, 2, 3, 4], values: [1, 3, 2, 5, 4] };

describe("scientific OLS layers", () => {
  it("matches a SciPy linregress / t.ppf reference without changing source data", () => {
    const spec = canonicalizeChartSpec({ ...source, analysis: { regression: "ols95" } });
    const fit = linearFits(spec)?.[0];
    expect(fit?.r2).toBeCloseTo(0.64, 12);
    expect(fit?.points).toHaveLength(65);
    expect(fit?.points[0][1]).toBeCloseTo(1.4, 12);
    expect(fit?.points[0][3]).toBeCloseTo(4.10039524, 7);
    expect(fit?.points[32][2]).toBeCloseTo(1.44092608, 7);
    expect(fit?.points[64][1]).toBeCloseTo(4.6, 12);
    expect(regressionYExtent(spec, [1, 5])[0]).toBeLessThan(1);
    expect(spec.values).toEqual(source.values);
    expect(canonicalizeChartSpec(spec)).toEqual(spec);
  });

  it("rejects categorical/log/constant-X and invalid regression requests", () => {
    for (const raw of [
      { ...source, xScale: "log10", xValues: [1, 2, 3, 4, 5] },
      { ...source, xValues: [1, 1, 1, 1, 1] },
      { type: "line", labels: ["a", "b", "c"], values: [1, 2, 3] },
      { ...source, xValues: [1, 2], values: [3, 4] },
    ]) {
      expect(() => canonicalizeChartSpec({ ...raw, analysis: { regression: "ols95" } })).toThrow(/Linear regression/);
    }
    expect(() => canonicalizeChartSpec({ ...source, analysis: { regression: "unknown" } })).toThrow();
  });

  it("fits constant Y and multiple independent series", () => {
    const spec = canonicalizeChartSpec({ type: "scatter", xValues: [0, 1, 2],
      series: [{ name: "flat", values: [7, 7, 7] }, { name: "slope", values: [1, 3, 5] }],
      analysis: { regression: "ols95" } });
    const fits = linearFits(spec);
    expect(fits?.[0].r2).toBeNull();
    expect(fits?.[0].points[32]).toEqual([1, 7, 7, 7]);
    expect(fits?.[1].r2).toBeCloseTo(1, 12);
    expect(fits?.[1].points[64][1]).toBeCloseTo(5, 12);
  });
});
