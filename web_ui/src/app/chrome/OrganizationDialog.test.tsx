import { describe, expect, it } from "vitest";
import { previewComparisonRects, previewEdgePath, previewNodeRects } from "./OrganizationDialog";
import type { OrganizationPreview } from "../canvas/sceneStore";

describe("previewNodeRects", () => {
  it("fits arbitrary positions and sizes within the preview without changing input", () => {
    const nodes = [
      { id: "a", kind: "chat", title: "A", x: -300, y: 100, width: 600, height: 700, changed: true },
      { id: "b", kind: "code", title: "B", x: 1400, y: 1800, width: 800, height: 300, changed: false },
    ];
    const before = JSON.stringify(nodes);
    const result = previewNodeRects(nodes);
    expect(JSON.stringify(nodes)).toBe(before);
    expect(result).toHaveLength(2);
    for (const item of result) {
      expect(item.px).toBeGreaterThanOrEqual(0);
      expect(item.py).toBeGreaterThanOrEqual(0);
      expect(item.px + item.pw).toBeLessThanOrEqual(520.00001);
      expect(item.py + item.ph).toBeLessThanOrEqual(245.00001);
    }
  });

  it("ignores corrupt nonfinite coordinates instead of breaking the SVG", () => {
    expect(previewNodeRects([
      { id: "bad", kind: "chat", title: "", x: Infinity, y: 0, width: 10, height: 10, changed: true },
    ])).toEqual([]);
  });
});

describe("read-only before/after comparison", () => {
  const nodes: OrganizationPreview["nodes"] = [
    {
      id: "a", kind: "chat", title: "A", x: 1200, y: 0,
      width: 100, height: 50, previousX: 0, previousY: 0,
      previousWidth: 100, previousHeight: 50, changed: true,
    },
    {
      id: "b", kind: "chat", title: "B", x: 1450, y: 400,
      width: 100, height: 50, previousX: 200, previousY: 200,
      previousWidth: 100, previousHeight: 50, changed: true,
    },
  ];

  it("uses identical viewport scaling for both stages without mutating nodes", () => {
    const snapshot = JSON.stringify(nodes);
    const before = previewComparisonRects(nodes, "before");
    const after = previewComparisonRects(nodes, "after");
    expect(before).toHaveLength(2);
    expect(after).toHaveLength(2);
    expect(before[0].pw).toBeCloseTo(after[0].pw);
    expect(before[0].ph).toBeCloseTo(after[0].ph);
    expect(before[0].px).toBeLessThan(after[0].px);
    expect(JSON.stringify(nodes)).toBe(snapshot);
    for (const rect of [...before, ...after]) {
      expect(rect.px).toBeGreaterThanOrEqual(0);
      expect(rect.py).toBeGreaterThanOrEqual(0);
      expect(rect.px + rect.pw).toBeLessThanOrEqual(520.00001);
      expect(rect.py + rect.ph).toBeLessThanOrEqual(245.00001);
    }
  });

  it("draws port-based orthogonal connections without diagonal segments", () => {
    const rectangles = previewComparisonRects(nodes, "after");
    const path = previewEdgePath(rectangles[0], rectangles[1]);
    const points = path.match(/-?[0-9]+(?:[.][0-9]+)?/g)?.map(Number) ?? [];
    expect(points).toHaveLength(8);
    for (let index = 0; index < 6; index += 2) {
      const isHorizontal = points[index + 1] === points[index + 3];
      const isVertical = points[index] === points[index + 2];
      expect(isHorizontal || isVertical).toBe(true);
    }
  });

  it("skips corrupt before rectangles rather than drawing invalid SVG coordinates", () => {
    const damaged = [{ ...nodes[0], previousWidth: Infinity }];
    expect(previewComparisonRects(damaged, "before")).toEqual([]);
  });
});
