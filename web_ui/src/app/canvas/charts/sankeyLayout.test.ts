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

describe("bounded Sankey geometry", () => {
  const cases = [
    ...[30, 150, 300].map((count) => ({
      name: `${count} disconnected flows`,
      flows: Array.from({ length: count }, (_, i) => ({ source: `A${i}`, target: `B${i}`, value: 1 })),
    })),
    {
      name: "skewed fan-out and fan-in with a disconnected fractional flow",
      flows: [
        { source: "A", target: "B", value: 1000 },
        { source: "A", target: "C", value: 1 },
        { source: "B", target: "D", value: 1000 },
        { source: "C", target: "D", value: 1 },
        { source: "X", target: "Y", value: 0.1 },
      ],
    },
    {
      name: "fractional flows",
      flows: [
        { source: "A", target: "B", value: 0.01 },
        { source: "A", target: "C", value: 0.03 },
      ],
    },
    {
      name: "finite flows with an overflowing raw total",
      flows: [
        { source: "A", target: "B", value: 1e308 },
        { source: "A", target: "C", value: 1e308 },
      ],
    },
  ];

  it.each(cases)("fits all nodes and conserves proportional ports: $name", ({ flows }) => {
    const layout = computeSankeyLayout(flows);
    const byName = new Map(layout.nodes.map((node) => [node.name, node]));
    const maxValue = Math.max(...flows.map((flow) => flow.value));
    const scale = Math.max(...layout.flows.map((flow) => flow.startY1 - flow.startY0));
    const epsilon = 1e-10;
    expect(layout.flows).toHaveLength(flows.length);
    expect(scale).toBeGreaterThan(0);
    for (const node of layout.nodes) {
      expect(node.x).toBeGreaterThanOrEqual(0);
      expect(node.x + node.width).toBeLessThanOrEqual(1);
      expect(node.y).toBeGreaterThanOrEqual(0.09 - epsilon);
      expect(node.y + node.height).toBeLessThanOrEqual(0.91 + epsilon);
      expect(node.height).toBeGreaterThan(0);
      const outgoing = layout.flows.filter((flow) => flow.source === node.name);
      const incoming = layout.flows.filter((flow) => flow.target === node.name);
      const weight = Math.max(
        outgoing.reduce((sum, flow) => sum + flow.value / maxValue, 0),
        incoming.reduce((sum, flow) => sum + flow.value / maxValue, 0),
      );
      expect(node.height).toBeCloseTo(weight * scale, 10);
      for (const ports of [
        outgoing.map((flow) => [flow.startY0, flow.startY1]),
        incoming.map((flow) => [flow.endY0, flow.endY1]),
      ]) {
        let cursor = node.y;
        for (const [start, end] of ports.sort((a, b) => a[0] - b[0])) {
          expect(start).toBeCloseTo(cursor, 10);
          expect(end).toBeGreaterThan(start);
          expect(end).toBeLessThanOrEqual(node.y + node.height + epsilon);
          cursor = end;
        }
      }
    }
    for (const flow of layout.flows) {
      const expected = (flow.value / maxValue) * scale;
      expect(flow.startY1 - flow.startY0).toBeCloseTo(expected, 10);
      expect(flow.endY1 - flow.endY0).toBeCloseTo(expected, 10);
      expect(flow.x0).toBeCloseTo(byName.get(flow.source)!.x + byName.get(flow.source)!.width, 10);
      expect(flow.x1).toBeCloseTo(byName.get(flow.target)!.x, 10);
    }
    for (let level = 0; level <= layout.maxLevel; level += 1) {
      const column = layout.nodes.filter((node) => node.level === level).sort((a, b) => a.y - b.y);
      for (let i = 1; i < column.length; i += 1) {
        expect(column[i].y).toBeGreaterThanOrEqual(column[i - 1].y + column[i - 1].height - epsilon);
      }
    }
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
