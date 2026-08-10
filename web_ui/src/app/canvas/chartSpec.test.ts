/**
 * ADR-013 stage 13.1 exit criterion: "Spec validates on both ends; a
 * hand-authored spec renders." This file proves the first half - the same
 * validation rules backend/tests/test_chart_data_validation.py pins for
 * graphlink_chart_data.py's canonicalize_chart_data, ported here.
 */
import { describe, expect, it } from "vitest";
import { CHART_SPEC_VERSION, ChartSpecError, canonicalizeChartSpec } from "./chartSpec";

function barData(overrides: Record<string, unknown> = {}) {
  return { type: "bar", title: "Sales", labels: ["A", "B", "C"], values: [1, 2, 3], ...overrides };
}

describe("canonicalizeChartSpec", () => {
  it("stamps the spec version", () => {
    const canonical = canonicalizeChartSpec(barData(), "bar");
    expect(canonical.version).toBe(CHART_SPEC_VERSION);
    expect(CHART_SPEC_VERSION).toBe(1);
  });

  it("renders a hand-authored bar spec with defaults filled in", () => {
    const canonical = canonicalizeChartSpec({ type: "bar", labels: ["Q1", "Q2"], values: [10, 20] }, "bar");
    expect(canonical).toEqual({
      version: 1,
      type: "bar",
      title: "Bar Chart",
      labels: ["Q1", "Q2"],
      values: [10, 20],
      xAxis: "Category",
      yAxis: "Value",
    });
  });

  it("rejects a non-list values container", () => {
    expect(() => canonicalizeChartSpec(barData({ values: { A: 1 } }), "bar")).toThrow(/both be lists/);
  });

  it("rejects a non-finite value", () => {
    expect(() => canonicalizeChartSpec(barData({ values: [1, NaN, 3] }), "bar")).toThrow(/finite/);
  });

  it("rejects mismatched labels/values length", () => {
    expect(() => canonicalizeChartSpec(barData({ values: [1, 2] }), "bar")).toThrow(/same length/);
  });

  it("rejects pie values that are not strictly positive", () => {
    expect(() =>
      canonicalizeChartSpec({ type: "pie", labels: ["A", "B"], values: [5, 0] }, "pie"),
    ).toThrow(/greater than zero/);
  });

  it("requires at least two points for a line chart", () => {
    expect(() => canonicalizeChartSpec({ type: "line", labels: ["A"], values: [1] }, "line")).toThrow(
      /at least two data points/,
    );
  });

  it("clamps histogram bins to [2, 24, valueCount]", () => {
    const canonical = canonicalizeChartSpec(
      { type: "histogram", values: [1, 2, 3, 4], bins: 999 },
      "histogram",
    );
    expect(canonical.bins).toBe(4);
  });

  it("aggregates duplicate sankey flows and sorts them", () => {
    const canonical = canonicalizeChartSpec(
      {
        type: "sankey",
        flows: [
          { source: "A", target: "B", value: 1 },
          { source: "A", target: "B", value: 2 },
        ],
      },
      "sankey",
    );
    expect(canonical.flows).toEqual([{ source: "A", target: "B", value: 3 }]);
  });

  it("rejects a sankey cycle", () => {
    expect(() =>
      canonicalizeChartSpec(
        {
          type: "sankey",
          flows: [
            { source: "A", target: "B", value: 1 },
            { source: "B", target: "A", value: 1 },
          ],
        },
        "sankey",
      ),
    ).toThrow(/cannot contain cycles/);
  });

  it("rejects an unsupported chart type", () => {
    expect(() => canonicalizeChartSpec(barData({ type: "radar" }), "radar")).toThrow(ChartSpecError);
  });

  it("rejects a payload type mismatched with the requested type", () => {
    expect(() => canonicalizeChartSpec(barData(), "line")).toThrow(/must be line/);
  });

  it("never mutates the input object", () => {
    const input = barData();
    const snapshot = JSON.stringify(input);
    canonicalizeChartSpec(input, "bar");
    expect(JSON.stringify(input)).toBe(snapshot);
  });
});
