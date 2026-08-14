/**
 * Connection geometry: where a link starts and ends, what curve joins them,
 * and whether a pointer is on it.
 *
 * Pure and framework-free by design. The canvas renderer that consumes this
 * (ConnectionCanvas.tsx) redraws every frame, so all of this has to be
 * cheap, allocation-light and deterministic - and testable without a canvas
 * or a browser.
 */

/** The four sides a handle can sit on, mirroring the canvas library's own. */
export type HandleSide = "top" | "right" | "bottom" | "left";

export interface HandleGeometry {
  /** Offset of the handle box within its node, in flow units. */
  x: number;
  y: number;
  width: number;
  height: number;
  side: HandleSide;
}

export interface Point {
  x: number;
  y: number;
}

/**
 * Where a connection meets a handle, in flow coordinates.
 *
 * A link anchors to the OUTER rim of the handle along the side it faces,
 * centred across that side - not to the handle's centre. This matches the
 * geometry the node cards are drawn with, so a link visually touches the
 * dot rather than stopping short of it or overshooting into the card.
 */
export function anchorPoint(nodeX: number, nodeY: number, handle: HandleGeometry): Point {
  const x = nodeX + handle.x;
  const y = nodeY + handle.y;
  switch (handle.side) {
    case "top":
      return { x: x + handle.width / 2, y };
    case "right":
      return { x: x + handle.width, y: y + handle.height / 2 };
    case "bottom":
      return { x: x + handle.width / 2, y: y + handle.height };
    case "left":
    default:
      return { x, y: y + handle.height / 2 };
  }
}

/**
 * Control points for the curve between two anchors.
 *
 * The curve leaves each anchor perpendicular to the side its handle sits
 * on, so a link always departs a card square-on rather than at an angle.
 * The offset grows with the distance being spanned but is clamped, so short
 * links stay taut and long ones do not develop absurd loops.
 */
function controlPoints(source: Point, sourceSide: HandleSide, target: Point, targetSide: HandleSide) {
  const dx = Math.abs(target.x - source.x);
  const dy = Math.abs(target.y - source.y);
  const vertical = sourceSide === "top" || sourceSide === "bottom";
  const span = vertical ? dy : dx;
  // 0.5 of the span reproduces the familiar S-curve; the floor keeps very
  // short links from collapsing into a straight segment.
  const offset = Math.max(span * 0.5, 20);
  const push = (p: Point, side: HandleSide): Point => {
    switch (side) {
      case "top":
        return { x: p.x, y: p.y - offset };
      case "bottom":
        return { x: p.x, y: p.y + offset };
      case "left":
        return { x: p.x - offset, y: p.y };
      case "right":
      default:
        return { x: p.x + offset, y: p.y };
    }
  };
  return { c1: push(source, sourceSide), c2: push(target, targetSide) };
}

export interface ConnectionPath {
  source: Point;
  target: Point;
  c1: Point;
  c2: Point;
  /** Right-angle routing rather than a curve. */
  orthogonal: boolean;
  /** The mid-line a right-angle route steps across. */
  midY: number;
}

/** Everything needed to draw one connection, resolved from live positions. */
export function connectionPath(
  source: Point,
  sourceSide: HandleSide,
  target: Point,
  targetSide: HandleSide,
  orthogonal: boolean,
): ConnectionPath {
  const { c1, c2 } = controlPoints(source, sourceSide, target, targetSide);
  return { source, target, c1, c2, orthogonal, midY: source.y + (target.y - source.y) / 2 };
}

/** One point on a cubic bezier at parameter t. */
function bezierAt(p: ConnectionPath, t: number): Point {
  const u = 1 - t;
  const a = u * u * u;
  const b = 3 * u * u * t;
  const c = 3 * u * t * t;
  const d = t * t * t;
  return {
    x: a * p.source.x + b * p.c1.x + c * p.c2.x + d * p.target.x,
    y: a * p.source.y + b * p.c1.y + c * p.c2.y + d * p.target.y,
  };
}

/** Squared distance from a point to a segment - no square roots in the loop. */
function distanceToSegmentSquared(p: Point, a: Point, b: Point): number {
  const vx = b.x - a.x;
  const vy = b.y - a.y;
  const lengthSquared = vx * vx + vy * vy;
  if (lengthSquared === 0) {
    const dx = p.x - a.x;
    const dy = p.y - a.y;
    return dx * dx + dy * dy;
  }
  let t = ((p.x - a.x) * vx + (p.y - a.y) * vy) / lengthSquared;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const dx = p.x - (a.x + t * vx);
  const dy = p.y - (a.y + t * vy);
  return dx * dx + dy * dy;
}

/**
 * Whether a point is within `tolerance` of a connection.
 *
 * The curve is sampled into short segments and each is measured; sampling
 * is what makes this work identically for curved and right-angle routes,
 * and 16 segments is comfortably enough for a pointer-sized tolerance at
 * any zoom this canvas allows.
 */
export function isPointOnConnection(
  path: ConnectionPath,
  point: Point,
  tolerance: number,
  segments = 16,
): boolean {
  const toleranceSquared = tolerance * tolerance;
  let previous: Point = path.source;
  if (path.orthogonal) {
    const corners: Point[] = [
      path.source,
      { x: path.source.x, y: path.midY },
      { x: path.target.x, y: path.midY },
      path.target,
    ];
    for (let i = 1; i < corners.length; i++) {
      if (distanceToSegmentSquared(point, corners[i - 1], corners[i]) <= toleranceSquared) return true;
    }
    return false;
  }
  for (let i = 1; i <= segments; i++) {
    const current = bezierAt(path, i / segments);
    if (distanceToSegmentSquared(point, previous, current) <= toleranceSquared) return true;
    previous = current;
  }
  return false;
}

/** Trace a connection onto a 2D context. The caller owns stroke styling. */
export function traceConnection(ctx: CanvasRenderingContext2D, path: ConnectionPath): void {
  ctx.beginPath();
  ctx.moveTo(path.source.x, path.source.y);
  if (path.orthogonal) {
    ctx.lineTo(path.source.x, path.midY);
    ctx.lineTo(path.target.x, path.midY);
    ctx.lineTo(path.target.x, path.target.y);
  } else {
    ctx.bezierCurveTo(path.c1.x, path.c1.y, path.c2.x, path.c2.y, path.target.x, path.target.y);
  }
  ctx.stroke();
}
