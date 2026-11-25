import { describe, expect, it } from "vitest";
import { clusterAxis, clusterHeatmap, dendrogramPaths } from "./heatmapClustering";
import { canonicalizeChartSpec } from "../chartSpec";

const sample = [
  [0, 0, 0.1, 0.2],
  [9, 9, 8.9, 9.1],
  [0.1, 0, 0, 0.2],
  [9.2, 9.2, 9.1, 9],
];

describe("deterministic agglomerative heatmap clustering", () => {
  it("clusters similar rows while retaining original samples", () => {
    const snapshot = JSON.stringify(sample);
    const plan = clusterHeatmap(sample, "both");
    expect(plan.rowOrder).toEqual([0, 2, 1, 3]);
    expect(plan.columnOrder).toHaveLength(4);
    expect([...plan.columnOrder].sort()).toEqual([0, 1, 2, 3]);
    expect(dendrogramPaths(plan.rowTree, plan.rowOrder, "rows", 20)).toHaveLength(3);
    expect(dendrogramPaths(plan.columnTree, plan.columnOrder, "columns", 20)).toHaveLength(3);
    expect(JSON.stringify(sample)).toBe(snapshot);
  });

  it("keeps deterministic ties, huge and tiny magnitudes finite", () => {
    for (const scale of [1e-150, 1e150]) {
      const plan = clusterAxis([[0], [scale], [scale * 1.1], [scale * 0.1]]);
      expect(plan.order).toEqual([0, 3, 1, 2]);
      expect(Number.isFinite(plan.tree!.height)).toBe(true);
    }
    expect(clusterAxis([[1], [1], [1]]).order).toEqual([0, 1, 2]);
    expect(clusterAxis([[1]]).order).toEqual([0]);
  });

  it("persists only mode and rejects unrelated annotations", () => {
    const input = { type: "heatmap", matrix: sample,
      rowLabels: ["A", "B", "C", "D"], columnLabels: ["T1", "T2", "T3", "T4"],
      analysis: { clustering: "both" } };
    const spec = canonicalizeChartSpec(input);
    expect(spec.analysis?.clustering).toBe("both");
    expect(spec.matrix).toEqual(sample);
    expect(canonicalizeChartSpec(spec)).toEqual(spec);
    for (const analysis of [
      { clustering: "ward" }, { clustering: "both", lockX: true, xRange: [0, 4] },
      { clustering: "rows", annotations: [{ kind: "label", x: 1, y: 2, text: "x" }] },
    ]) expect(() => canonicalizeChartSpec({ ...input, analysis })).toThrow();
  });
});
