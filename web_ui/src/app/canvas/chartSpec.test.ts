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
