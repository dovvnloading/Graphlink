import { describe, expect, it } from "vitest";
import { computeSankeyLayout, sankeyFlowPath } from "./sankeyLayout";

describe("computeSankeyLayout", () => {
  it("levels a simple two-node chain and places the target to the right of the source", () => {
    const layout = computeSankeyLayout([{ source: "A", target: "B", value: 5 }]);
    const byName = new Map(layout.nodes.map((node) => [node.name, node]));
    expect(byName.get("A")?.level).toBe(0);
    expect(byName.get("B")?.level).toBe(1);
    expect(byName.get("B")!.x).toBeGreaterThan(byName.get("A")!.x);
    expect(layout.maxLevel).toBe(1);
    expect(layout.flows).toHaveLength(1);
  });

  it("assigns a longer chain increasing levels by longest path", () => {
    const layout = computeSankeyLayout([
      { source: "A", target: "B", value: 4 },
      { source: "B", target: "C", value: 4 },
      { source: "C", target: "D", value: 4 },
    ]);
    const byName = new Map(layout.nodes.map((node) => [node.name, node]));
    expect(byName.get("A")?.level).toBe(0);
    expect(byName.get("B")?.level).toBe(1);
    expect(byName.get("C")?.level).toBe(2);
    expect(byName.get("D")?.level).toBe(3);
  });

  it("orders same-column nodes by descending throughput (heavier flow placed first/above)", () => {
    const layout = computeSankeyLayout([
      { source: "A", target: "B", value: 3 },
      { source: "A", target: "C", value: 7 },
    ]);
    const byName = new Map(layout.nodes.map((node) => [node.name, node]));
    expect(byName.get("B")?.level).toBe(1);
    expect(byName.get("C")?.level).toBe(1);
    // C carries more flow (7 > 3) so it's placed first in the column - a
    // smaller y than B, which is placed after it.
    expect(byName.get("C")!.y).toBeLessThan(byName.get("B")!.y);
  });

  it("stacks two flows out of the same source without overlapping vertical ports", () => {
    const layout = computeSankeyLayout([
      { source: "A", target: "B", value: 3 },
      { source: "A", target: "C", value: 7 },
    ]);
    const [first, second] = layout.flows
      .filter((flow) => flow.source === "A")
      .sort((a, b) => a.startY0 - b.startY0);
    // The second flow's port starts where the first one's ends - no gap,
    // no overlap.
    expect(second.startY0).toBeCloseTo(first.startY1, 6);
  });

  it("keeps every node's height proportional to its total throughput", () => {
    const layout = computeSankeyLayout([
      { source: "A", target: "B", value: 2 },
      { source: "A", target: "C", value: 8 },
    ]);
    const byName = new Map(layout.nodes.map((node) => [node.name, node]));
    // C carries 4x the flow of B, so C's node block should be ~4x as tall.
    const ratio = byName.get("C")!.height / byName.get("B")!.height;
    expect(ratio).toBeCloseTo(4, 1);
  });

  it("does not throw on a disconnected multi-source, multi-sink graph", () => {
    const layout = computeSankeyLayout([
      { source: "A", target: "X", value: 1 },
      { source: "B", target: "Y", value: 1 },
    ]);
    expect(layout.nodes).toHaveLength(4);
    expect(layout.flows).toHaveLength(2);
  });
});

describe("sankeyFlowPath", () => {
  it("builds a closed SVG ribbon path from the flow's geometry", () => {
    const layout = computeSankeyLayout([{ source: "A", target: "B", value: 5 }]);
    const path = sankeyFlowPath(layout.flows[0]);
    expect(path.startsWith("M ")).toBe(true);
    expect(path).toContain("C ");
    expect(path.trim().endsWith("Z")).toBe(true);
  });
});
