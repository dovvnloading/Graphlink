import { describe, expect, it } from "vitest";
import {
  chartFrame, datumAt, domainFromPixels, invertScale, panDomain,
  supportsChartAnalysis, zoomDomain,
} from "./chartViewport";

describe("data-space chart viewport math", () => {
  it("enables only the four numerical analysis chart kinds", () => {
    for (const type of ["line", "scatter", "histogram", "boxplot"]) {
      expect(supportsChartAnalysis(type)).toBe(true);
    }
    for (const type of ["bar", "pie", "sankey", "heatmap"]) {
      expect(supportsChartAnalysis(type)).toBe(false);
    }
  });

  it("interpolates and box-zooms logarithmic coordinates in log space", () => {
    expect(invertScale(150, [1, 1000], [0, 300], "log10")).toBeCloseTo(Math.sqrt(1000));
    const selected = domainFromPixels(100, 200, [1, 1000], [0, 300], "log10");
    expect(selected[0]).toBeCloseTo(10);
    expect(selected[1]).toBeCloseTo(100);
    const zoomed = zoomDomain([1, 1000], "log10", 10, 2);
    expect(zoomed[0]).toBeCloseTo(Math.sqrt(10)); // Halfway from 1 to 10 in log space.
    expect(zoomed[1]).toBeCloseTo(100);
    const panned = panDomain([1, 100], "log10", 0.5);
    expect(panned[0]).toBeCloseTo(10);
    expect(panned[1]).toBeCloseTo(1000);
  });

  it("pans and boxes linear axes using data coordinates, not scaled SVG axes", () => {
    expect(panDomain([0, 100], "linear", 0.25)).toEqual([25, 125]);
    expect(zoomDomain([0, 100], "linear", 50, 2)).toEqual([25, 75]);
    expect(domainFromPixels(40, 160, [0, 100], [0, 200], "linear"))
      .toEqual([20, 80]);
    expect(domainFromPixels(180, 20, [0, 100], [0, 200], "linear"))
      .toEqual([10, 90]);
  });

  it("uses the existing default chart geometry without modifying observations", () => {
    const line = { type: "line" as const, labels: ["a", "b", "c"], values: [1, 3, 8] };
    const before = JSON.stringify(line);
    const frame = chartFrame(line, "line", 600, 400);
    expect(frame.base.x).toEqual([0, 2]);
    expect(frame.base.y[0]).toBeLessThan(1);
    expect(frame.base.y[1]).toBeGreaterThan(8);
    expect(frame.width).toBeGreaterThan(0);
    expect(JSON.stringify(line)).toBe(before);

    const middle = datumAt(frame.left + frame.width / 2, frame.top + frame.height / 2, frame, frame.base);
    expect(middle.x).toBeCloseTo(1);
    expect(middle.y).toBeCloseTo((frame.base.y[0] + frame.base.y[1]) / 2);
  });

  it("keeps log readouts in original physical units on both axes", () => {
    const scatter = {
      type: "scatter" as const, xValues: [1, 10, 100], values: [1, 10, 100],
      xScale: "log10" as const, yScale: "log10" as const,
      xUnit: "mg/L", yUnit: "mV",
    };
    const frame = chartFrame(scatter, "scatter", 600, 400);
    const mid = datumAt(frame.left + frame.width / 2, frame.top + frame.height / 2, frame, frame.base);
    expect(mid.x).toBeGreaterThan(0);
    expect(mid.y).toBeGreaterThan(0);
    expect(Math.log10(mid.x)).toBeCloseTo(
      (Math.log10(frame.base.x[0]) + Math.log10(frame.base.x[1])) / 2,
    );
  });

  it("leaves original ranges intact if a zoom would collapse or overflow them", () => {
    expect(zoomDomain([1, 1e300], "log10", 1e280, 0)).toEqual([1, 1e300]);
    expect(panDomain([Number.MIN_VALUE, 10], "log10", -10_000))
      .toEqual([Number.MIN_VALUE, 10]);
  });
});
