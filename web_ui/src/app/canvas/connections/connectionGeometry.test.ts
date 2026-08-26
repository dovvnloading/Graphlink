import { describe, expect, it, vi } from "vitest";
import {
  anchorPoint,
  connectionPath,
  isPointOnConnection,
  traceConnection,
  type ConnectionPath,
  type HandleGeometry,
} from "./connectionGeometry";

// Direct unit coverage of connection geometry: where a link anchors to a
// handle, the curve (or right-angle step) joining two anchors, and the
// hit-test a pointer is checked against. Same "one test per documented
// semantic, named so a regression fails loudly" posture as
// smartGuides.test.ts (the sibling pure-geometry module this one is modelled
// on) - every branch in anchorPoint's side switch, controlPoints' floor, and
// isPointOnConnection's orthogonal-vs-curved routing gets its own case.

function handle(overrides: Partial<HandleGeometry> = {}): HandleGeometry {
  return { x: 0, y: 0, width: 8, height: 8, side: "bottom", ...overrides };
}

describe("anchorPoint", () => {
  it("anchors to the outer rim, centred across the box, for a bottom-facing handle", () => {
    const p = anchorPoint(100, 200, handle({ x: 10, y: 30, width: 8, height: 8, side: "bottom" }));
    // x centred across the handle's own width; y at the handle's far (outer) edge.
    expect(p).toEqual({ x: 114, y: 238 });
  });

  it("anchors to the outer rim, centred across the box, for a top-facing handle", () => {
    const p = anchorPoint(100, 200, handle({ x: 10, y: 30, width: 8, height: 8, side: "top" }));
    // x centred the same way; y stays at the handle's own (near) edge, unlike bottom.
    expect(p).toEqual({ x: 114, y: 230 });
  });

  it("anchors to the outer rim, centred across the box, for a right-facing handle", () => {
    const p = anchorPoint(0, 0, handle({ x: 5, y: 5, width: 10, height: 20, side: "right" }));
    expect(p).toEqual({ x: 15, y: 15 }); // x + width; y centred by height
  });

  it("anchors to the outer rim, centred across the box, for a left-facing handle", () => {
    const p = anchorPoint(0, 0, handle({ x: 5, y: 5, width: 10, height: 20, side: "left" }));
    expect(p).toEqual({ x: 5, y: 15 }); // x untouched; y centred by height
  });
});

describe("connectionPath", () => {
  it("departs each anchor perpendicular to its own handle side, for a mostly-vertical span", () => {
    // source is bottom-facing -> vertical=true -> span is |dy|=100, offset=max(50,20)=50.
    const path = connectionPath({ x: 0, y: 0 }, "bottom", { x: 40, y: 100 }, "top", false);
    expect(path.c1).toEqual({ x: 0, y: 50 });
    expect(path.c2).toEqual({ x: 40, y: 50 });
  });

  it("departs each anchor perpendicular to its own handle side, for a mostly-horizontal span", () => {
    // source is right-facing -> vertical=false -> span is |dx|=100, offset=50.
    const path = connectionPath({ x: 0, y: 0 }, "right", { x: 100, y: 40 }, "left", false);
    expect(path.c1).toEqual({ x: 50, y: 0 });
    expect(path.c2).toEqual({ x: 50, y: 40 });
  });

  it("only the SOURCE side decides vertical-vs-horizontal, not the target's", () => {
    // source is right-facing (horizontal) even though the target is top-facing;
    // the offset must follow dx, not dy.
    const path = connectionPath({ x: 0, y: 0 }, "right", { x: 100, y: 400 }, "top", false);
    expect(path.c1).toEqual({ x: 50, y: 0 }); // pushed along x, from dx=100 -> offset 50
    expect(path.c2).toEqual({ x: 100, y: 350 }); // pushed along y regardless (target is top-facing)
  });

  it("floors the control-point offset at 20, so a very short link stays curved rather than collapsing", () => {
    const path = connectionPath({ x: 0, y: 0 }, "bottom", { x: 5, y: 5 }, "top", false);
    // dy=5 -> 0.5*5=2.5, below the 20 floor.
    expect(path.c1).toEqual({ x: 0, y: 20 });
    expect(path.c2).toEqual({ x: 5, y: -15 });
  });

  it("computes midY as the exact midpoint between source.y and target.y", () => {
    const path = connectionPath({ x: 0, y: 10 }, "bottom", { x: 0, y: 50 }, "top", false);
    expect(path.midY).toBe(30);
  });

  it("passes the orthogonal flag straight through, both ways", () => {
    expect(connectionPath({ x: 0, y: 0 }, "bottom", { x: 0, y: 0 }, "top", true).orthogonal).toBe(true);
    expect(connectionPath({ x: 0, y: 0 }, "bottom", { x: 0, y: 0 }, "top", false).orthogonal).toBe(false);
  });
});

describe("isPointOnConnection", () => {
  // A curved path whose control points are chosen colinear with source/target,
  // so the cubic bezier reduces to an exact straight line - lets the sampled
  // hit-test be checked against known-exact math instead of an approximation.
  const straightLine: ConnectionPath = {
    source: { x: 0, y: 0 },
    target: { x: 100, y: 0 },
    c1: { x: 33, y: 0 },
    c2: { x: 66, y: 0 },
    orthogonal: false,
    midY: 0,
  };

  it("finds a point exactly on a straight (colinear-control-point) curve", () => {
    expect(isPointOnConnection(straightLine, { x: 50, y: 0 }, 1)).toBe(true);
  });

  it("misses a point well off the curve", () => {
    expect(isPointOnConnection(straightLine, { x: 50, y: 50 }, 1)).toBe(false);
  });

  it("uses an inclusive <= tolerance boundary", () => {
    expect(isPointOnConnection(straightLine, { x: 50, y: 5 }, 5)).toBe(true); // exactly at tolerance
    expect(isPointOnConnection(straightLine, { x: 50, y: 5.01 }, 5)).toBe(false); // just past it
  });

  it("an orthogonal route is a genuinely different path from a straight line between the same endpoints", () => {
    // Same source/target/midY for both; only the routing (and control points,
    // irrelevant to the orthogonal branch) differ.
    const corners = { source: { x: 0, y: 0 }, target: { x: 100, y: 100 }, midY: 50 };
    const stepped: ConnectionPath = { ...corners, c1: { x: 0, y: 0 }, c2: { x: 0, y: 0 }, orthogonal: true };
    const straight: ConnectionPath = { ...corners, c1: { x: 25, y: 25 }, c2: { x: 75, y: 75 }, orthogonal: false };
    // (25, 25) sits exactly on the direct diagonal but is ~25px from every
    // segment of the source->midY->target step (down, then across, then down).
    expect(isPointOnConnection(straight, { x: 25, y: 25 }, 1)).toBe(true);
    expect(isPointOnConnection(stepped, { x: 25, y: 25 }, 1)).toBe(false);
  });

  it("finds a point on each of the three orthogonal segments: down to midY, across, then down to target", () => {
    const path: ConnectionPath = {
      source: { x: 0, y: 0 },
      target: { x: 100, y: 100 },
      c1: { x: 0, y: 0 },
      c2: { x: 0, y: 0 },
      orthogonal: true,
      midY: 50,
    };
    expect(isPointOnConnection(path, { x: 0, y: 25 }, 1)).toBe(true); // first vertical segment
    expect(isPointOnConnection(path, { x: 50, y: 50 }, 1)).toBe(true); // horizontal segment
    expect(isPointOnConnection(path, { x: 100, y: 75 }, 1)).toBe(true); // second vertical segment
  });
});

describe("traceConnection", () => {
  // A minimal call-recording stand-in for CanvasRenderingContext2D - traceConnection
  // only ever calls the path-building subset, so only that subset needs a spy.
  function fakeCtx() {
    const calls: string[] = [];
    const ctx = {
      beginPath: vi.fn(() => calls.push("beginPath")),
      moveTo: vi.fn((x: number, y: number) => calls.push(`moveTo ${x},${y}`)),
      lineTo: vi.fn((x: number, y: number) => calls.push(`lineTo ${x},${y}`)),
      bezierCurveTo: vi.fn((c1x: number, c1y: number, c2x: number, c2y: number, x: number, y: number) =>
        calls.push(`bezierCurveTo ${c1x},${c1y},${c2x},${c2y},${x},${y}`),
      ),
      stroke: vi.fn(() => calls.push("stroke")),
    };
    return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
  }

  it("traces a single bezier curve for a non-orthogonal path", () => {
    const { ctx, calls } = fakeCtx();
    const path: ConnectionPath = {
      source: { x: 0, y: 0 },
      target: { x: 100, y: 50 },
      c1: { x: 10, y: 0 },
      c2: { x: 90, y: 50 },
      orthogonal: false,
      midY: 25,
    };
    traceConnection(ctx, path);
    expect(calls).toEqual(["beginPath", "moveTo 0,0", "bezierCurveTo 10,0,90,50,100,50", "stroke"]);
  });

  it("traces a 3-segment right-angle step for an orthogonal path, and never calls bezierCurveTo", () => {
    const { ctx, calls } = fakeCtx();
    const path: ConnectionPath = {
      source: { x: 0, y: 0 },
      target: { x: 100, y: 100 },
      c1: { x: 0, y: 0 },
      c2: { x: 0, y: 0 },
      orthogonal: true,
      midY: 50,
    };
    traceConnection(ctx, path);
    expect(calls).toEqual(["beginPath", "moveTo 0,0", "lineTo 0,50", "lineTo 100,50", "lineTo 100,100", "stroke"]);
  });
});
