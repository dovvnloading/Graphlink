import { describe, expect, it } from "vitest";
import { connectionPath, connectionSvgPath, isPointOnConnection } from "./connectionGeometry";
import { ReturnRouteCache, type RoutingNode } from "./returnRoute";

const card = (x: number, y: number, width = 100, height = 80) => ({
  measured: { width, height },
  internals: { positionAbsolute: { x, y } },
});

describe("return links", () => {
  const source = { x: 50, y: 180 }, target = { x: 450, y: 0 };
  const from = card(0, 100), to = card(400, 0);
  const nodes = new Map([["from", from], ["to", to]]);
  it("follows an open lane to the far port and moves lanes when a third card enters it", () => {
    const router = new ReturnRouteCache();
    router.update(nodes);
    const first = router.route("e", source, target, from, to)!;
    expect(first).toContainEqual({ x: 250, y: 189 });
    router.update(nodes);
    expect(router.route("e", source, target, from, to)).toBe(first);
    const blocked = new Map(nodes).set("other", card(200, 80));
    router.update(blocked);
    const next = router.route("e", source, target, from, to)!;
    expect(next).not.toContainEqual({ x: 250, y: 189 });
    const path = connectionPath(source, "bottom", target, "top", false);
    path.waypoints = next;
    expect(isPointOnConnection(path, { x: 250, y: 120 }, 6)).toBe(false);
    expect(isPointOnConnection(path, { x: 50, y: 185 }, 5)).toBe(true);
    expect(connectionSvgPath(path)).toContain(` L ${next[4].x},${next[4].y}`);
  });
  it("leaves transparent group frames out of obstacle selection", () => {
    const router = new ReturnRouteCache();
    router.update(nodes);
    const first = router.route("e", source, target, from, to);
    router.update(new Map<string, RoutingNode>([...nodes, ["frame", { ...card(210, 0), type: "frame" }]]));
    expect(router.route("e", source, target, from, to)).toBe(first);
  });
});
