import { describe, expect, it } from "vitest";
import { previewNodeRects } from "./OrganizationDialog";

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
