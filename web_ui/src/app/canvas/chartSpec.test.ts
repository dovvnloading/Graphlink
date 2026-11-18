/**
 * ADR-013 stage 13.1 exit criterion: "Spec validates on both ends; a
 * hand-authored spec renders." This file proves the first half - the same
 * validation rules backend/tests/test_chart_data_validation.py pins for
 * graphlink_chart_data.py's canonicalize_chart_data, ported here.
 */
import { describe, expect, it } from "vitest";
import { CHART_SPEC_VERSION, ChartSpecError, canonicalizeChartSpec } from "./chartSpec";
import { validateSceneState } from "../../lib/bridge-core/generated/scene-state";
import { initialSceneState } from "./sceneStore";

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

  it("does not accept JavaScript-only Number coercions Python would reject", () => {
    for (const value of [null, true, false, [], [5], {}, "", "  ", "0x10", "0b11"]) {
      expect(() => canonicalizeChartSpec(barData({ values: [value, 2, 3] }), "bar")).toThrow(/finite/);
    }
    expect(canonicalizeChartSpec(barData({ values: [" 1.25 ", "3e2", -4] }), "bar").values)
      .toEqual([1.25, 300, -4]);
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

  it("does not collide when a sankey node name contains a separator", () => {
    const flows = [
      { source: "A\u0000B", target: "C", value: 1 },
      { source: "A", target: "B\u0000C", value: 2 },
    ];
    const result = canonicalizeChartSpec({ type: "sankey", flows }, "sankey");
    expect(result.flows).toHaveLength(2);
    expect(result.flows).toEqual(expect.arrayContaining(flows));
  });

  it("rejects overflow after adding duplicate or distinct sankey flows", () => {
    expect(() => canonicalizeChartSpec({ type: "sankey", flows: [
      { source: "A", target: "B", value: 1e308 },
      { source: "A", target: "B", value: 1e308 },
    ] }, "sankey")).toThrow(/flow totals must be finite/);
    expect(() => canonicalizeChartSpec({ type: "sankey", flows: [
      { source: "A", target: "B", value: 1e308 },
      { source: "A", target: "C", value: 1e308 },
    ] }, "sankey")).toThrow(/node totals must be finite/);
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

  describe("legacy nested sankey shape", () => {
    it("resolves a legacy nested payload with string node names", () => {
      const canonical = canonicalizeChartSpec(
        {
          type: "sankey",
          data: {
            nodes: ["A", "B"],
            links: [{ source: "A", target: "B", value: 5 }],
          },
        },
        "sankey",
      );
      expect(canonical.flows).toEqual([{ source: "A", target: "B", value: 5 }]);
    });

    it("resolves a legacy nested payload with dict nodes (name field)", () => {
      const canonical = canonicalizeChartSpec(
        {
          type: "sankey",
          data: {
            nodes: [{ name: "Alpha" }, { name: "Beta" }],
            links: [{ source: "Alpha", target: "Beta", value: 3 }],
          },
        },
        "sankey",
      );
      expect(canonical.flows).toEqual([{ source: "Alpha", target: "Beta", value: 3 }]);
    });

    it("falls back to 'Node {index}' for a dict node missing a name", () => {
      const canonical = canonicalizeChartSpec(
        {
          type: "sankey",
          data: {
            nodes: [{}, { name: "Beta" }],
            links: [{ source: 0, target: 1, value: 2 }],
          },
        },
        "sankey",
      );
      expect(canonical.flows).toEqual([{ source: "Node 0", target: "Beta", value: 2 }]);
    });

    it("resolves integer link source/target indices via the names array", () => {
      const canonical = canonicalizeChartSpec(
        {
          type: "sankey",
          data: {
            nodes: ["A", "B", "C"],
            links: [
              { source: 0, target: 2, value: 4 },
              { source: 1, target: 2, value: 1 },
            ],
          },
        },
        "sankey",
      );
      expect(canonical.flows).toEqual([
        { source: "A", target: "C", value: 4 },
        { source: "B", target: "C", value: 1 },
      ]);
    });

    it("rejects a legacy payload with too many links before any other processing", () => {
      const hugeLinks = Array.from({ length: 301 }, () => ({ source: 0, target: 1, value: 1 }));
      expect(() =>
        canonicalizeChartSpec(
          { type: "sankey", data: { nodes: [{ name: "A" }, { name: "B" }], links: hugeLinks } },
          "sankey",
        ),
      ).toThrow(/at most 300 flows/);
    });

    it("rejects a legacy payload with too many nodes before any other processing", () => {
      const hugeNodes = Array.from({ length: 302 }, (_, i) => ({ name: `n${i}` }));
      expect(() =>
        canonicalizeChartSpec(
          { type: "sankey", data: { nodes: hugeNodes, links: [{ source: 0, target: 1, value: 1 }] } },
          "sankey",
        ),
      ).toThrow(/at most 301 nodes/);
    });

    it("falls through to the 'at least one flow' error when there is no data key", () => {
      expect(() => canonicalizeChartSpec({ type: "sankey" }, "sankey")).toThrow(/at least one flow/);
    });

    it("falls through to the 'at least one flow' error when data is not an object", () => {
      expect(() => canonicalizeChartSpec({ type: "sankey", data: "not an object" }, "sankey")).toThrow(
        /at least one flow/,
      );
    });

    it("still rejects an explicitly-empty flows array without consulting the legacy shape", () => {
      expect(() =>
        canonicalizeChartSpec(
          {
            type: "sankey",
            flows: [],
            data: { nodes: ["A", "B"], links: [{ source: "A", target: "B", value: 1 }] },
          },
          "sankey",
        ),
      ).toThrow(/at least one flow/);
    });
  });
});


describe("boxplot and heatmap v1 chart specs", () => {
  const box = {
    type: "boxplot", groups: [
      { label: "Control", values: [1, 2, 3, 10] },
      { label: "Treatment", values: [-1, 0, 2, 3] },
    ],
  };
  const heat = {
    type: "heatmap", rowLabels: ["A", "B"], columnLabels: ["x", "y"],
    matrix: [[-2, 0], [1, 5]], valueUnit: "AU",
  };

  it("normalizes samples without changing the source or version", () => {
    const before = JSON.stringify(box);
    const result = canonicalizeChartSpec(box, "boxplot");
    expect(result).toMatchObject({
      version: 1, type: "boxplot", title: "Boxplot Chart", xAxis: "Group", yAxis: "Value",
      groups: box.groups,
    });
    expect(result.type).toBe("boxplot");
    expect(canonicalizeChartSpec(result, "boxplot")).toEqual(result);
    expect(JSON.stringify(box)).toBe(before);
  });

  it("normalizes a rectangular matrix and preserves the exact row/column order", () => {
    const result = canonicalizeChartSpec(heat, "heatmap");
    expect(result).toMatchObject({
      version: 1, type: "heatmap", title: "Heatmap Chart", xAxis: "Columns", yAxis: "Rows",
      rowLabels: ["A", "B"], columnLabels: ["x", "y"],
      matrix: [[-2, 0], [1, 5]], valueUnit: "AU",
    });
    expect(result.type).toBe("heatmap");
    expect(canonicalizeChartSpec(result, "heatmap")).toEqual(result);
  });

  it.each([
    [{ type: "boxplot", groups: [] }, /nonempty groups/],
    [{ type: "boxplot", groups: [{ label: "A", values: [1] }] }, /at least two/],
    [{ type: "boxplot", groups: [
      { label: "A", values: [1, 2] }, { label: "A", values: [2, 3] },
    ] }, /Duplicate/],
    [{ type: "boxplot", groups: [{ label: "A", values: [1, NaN] }] }, /finite/],
    [{ type: "boxplot", groups: [{ label: "A", values: [-1e308, 1e308] }] }, /range must be finite/],
    [{ type: "boxplot", groups: [{ label: "A", values: Array(301).fill(1) }] }, /at most 300/],
    [{ type: "heatmap", rowLabels: [], columnLabels: ["x"], matrix: [] }, /rowLabels/],
    [{ type: "heatmap", rowLabels: ["A"], columnLabels: ["x", "y"], matrix: [[1]] }, /length must match/],
    [{ type: "heatmap", rowLabels: ["A"], columnLabels: ["x"], matrix: [[true]] }, /finite/],
    [{ type: "heatmap", rowLabels: ["A"], columnLabels: ["x"], matrix: [[Infinity]] }, /finite/],
    [{ type: "heatmap", rowLabels: ["A"], columnLabels: ["x", "y"], matrix: [[-1e308, 1e308]] }, /range must be finite/],
    [{ type: "heatmap", rowLabels: Array(20).fill("r"), columnLabels: Array(16).fill("c"), matrix: [] }, /at most 300/],
  ] as [Record<string, unknown>, RegExp][])("rejects malformed chart data %j", (input, error) => {
    expect(() => canonicalizeChartSpec(input, String(input.type))).toThrow(error);
  });

  it("keeps existing charts unchanged", () => {
    expect(canonicalizeChartSpec({ type: "pie", labels: ["A", "B"], values: [1, 2] }, "pie")).toEqual({
      version: 1, type: "pie", title: "Pie Chart",
      labels: ["A", "B"], values: [1, 2], xAxis: "Sequence", yAxis: "Value",
    });
  });
});


// Prior workspace versions omitted the nested type on PR 586/590 charts.
// The generated scene validator must accept those bytes before ChartRenderer
// restores the discriminator from the containing chart node.
describe("saved chart wire compatibility", () => {
  const savedCharts = [
    ["bar", { labels: ["A", "B"], values: [1, 2] }],
    ["line", { labels: ["A", "B"], values: [1, 3] }],
    ["pie", { labels: ["A", "B"], values: [1, 2] }],
    ["histogram", { values: [1, 2, 3], bins: 2 }],
    ["sankey", { flows: [{ source: "A", target: "B", value: 3 }] }],
    ["scatter", {
      xValues: [1, 10], series: [
        { name: "Control", values: [2, 3], yError: [[0.1, 0.2], [0.2, 0.3]] },
      ], xScale: "log10", yScale: "linear", xUnit: "mg/L", yUnit: "mV",
    }],
    ["boxplot", { groups: [{ label: "Control", values: [1, 2, 3, 10] }] }],
    ["heatmap", {
      rowLabels: ["A", "B"], columnLabels: ["X", "Y"],
      matrix: [[-2, 0], [1, 5]], valueUnit: "AU",
    }],
  ] as const;

  it.each(savedCharts)("validates and restores historical %s chart data without altering the plotted values", (chartType, saved) => {
    const typed = canonicalizeChartSpec({ type: chartType, ...saved }, chartType);
    const legacy = { ...typed };
    delete legacy.type;

    for (const chartData of [typed, legacy]) {
      const wire = validateSceneState({
        ...initialSceneState,
        nodes: [{
          id: "saved-chart", x: 0, y: 0, title: "Saved chart",
          kind: "chart", chartType, chartData,
        }],
      });
      expect(wire.ok).toBe(true);
      if (!wire.ok) continue;
      const loaded = canonicalizeChartSpec(wire.value.nodes[0].chartData, wire.value.nodes[0].chartType);
      expect(loaded).toEqual(typed);
      expect(loaded.type).toBe(chartType);
    }
  });
});
