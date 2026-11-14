import type { Point } from "./connectionGeometry";

export interface RoutingNode {
  type?: string;
  hidden?: boolean;
  measured?: { width?: number; height?: number };
  internals: { positionAbsolute: Point };
}

interface CardBounds {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

const CLEARANCE = 18;
// Dense scenes use endpoint clearance alone. Each drag frame has bounded
// routing work even when a workspace contains thousands of cards or links.
const OBSTACLE_LIMIT = 96;

function bounds(node: RoutingNode): CardBounds | undefined {
  const { width, height } = node.measured ?? {};
  if (!width || !height) return;
  const { x, y } = node.internals.positionAbsolute;
  return { left: x, top: y, right: x + width, bottom: y + height };
}

/** Keep return links out of the cards they join. Both ports face away
 * from the destination, so a single Bézier folds back through the cards. */
function returnRoute(source: Point, target: Point, a: CardBounds, b: CardBounds, obstacles: readonly CardBounds[]): Point[] {
  const outY = source.y + CLEARANCE;
  const inY = target.y - CLEARANCE;
  const middle = (source.x + target.x) / 2;
  // Try the gap between the cards, then either flank. Penalize a crossing
  // before extra travel. Ties prefer the central lane.
  const lanes = [middle, a.left - CLEARANCE, a.right + CLEARANCE, b.left - CLEARANCE, b.right + CLEARANCE];
  let bestLane = middle;
  let fewestHits = Infinity;
  let shortest = Infinity;
  for (const x of lanes) {
    let hits = 0;
    for (const box of obstacles) {
      const crossesRow = (y: number, from: number, to: number) =>
        y > box.top - 4 && y < box.bottom + 4 && Math.max(from, to) > box.left - 4 && Math.min(from, to) < box.right + 4;
      if (crossesRow(outY, source.x, x) || crossesRow(inY, x, target.x) ||
        (x > box.left - 4 && x < box.right + 4 && outY > box.top - 4 && inY < box.bottom + 4)) hits++;
    }
    const length = Math.abs(source.x - x) + Math.abs(x - target.x);
    if (hits < fewestHits || (hits === fewestHits && length < shortest)) {
      fewestHits = hits;
      shortest = length;
      bestLane = x;
    }
  }
  const corners = [source, { x: source.x, y: outY }, { x: bestLane, y: outY }, { x: bestLane, y: inY },
    { x: target.x, y: inY }, target];
  // A few points on a tight rounded bend suffice even at maximum zoom.
  // Reuse these exact points for canvas paint, hit tests and SVG capture.
  const points = [source];
  for (let i = 1; i < corners.length - 1; i++) {
    const p = corners[i];
    const prev = corners[i - 1];
    const next = corners[i + 1];
    const incoming = Math.hypot(prev.x - p.x, prev.y - p.y);
    const outgoing = Math.hypot(next.x - p.x, next.y - p.y);
    if (!incoming || !outgoing) {
      points.push(p);
      continue;
    }
    const radius = Math.min(9, incoming / 2, outgoing / 2);
    const before = { x: p.x + (prev.x - p.x) * radius / incoming, y: p.y + (prev.y - p.y) * radius / incoming };
    const after = { x: p.x + (next.x - p.x) * radius / outgoing, y: p.y + (next.y - p.y) * radius / outgoing };
    for (let step = 0; step <= 4; step++) {
      const t = step / 4;
      const u = 1 - t;
      points.push({
        x: u * u * before.x + 2 * u * t * p.x + t * t * after.x,
        y: u * u * before.y + 2 * u * t * p.y + t * t * after.y,
      });
    }
  }
  points.push(target);
  return points;
}

export class ReturnRouteCache {
  private connections?: readonly { id: string }[];
  private cards = new Map<string, CardBounds>();
  private obstacles: CardBounds[] = [];
  private routes = new Map<string, {
    source: Point; target: Point; from: CardBounds; to: CardBounds; points: Point[];
  }>();

  /** Compare coordinates, not object identities: the flow store can mutate
   * a node's internals during a drag. Pan, zoom and hover reuse the routes. */
  update(nodes: ReadonlyMap<string, RoutingNode>, connections?: readonly { id: string }[]) {
    if (connections && this.connections !== connections) {
      this.connections = connections;
      const live = new Set(connections.map(edge => edge.id));
      for (const id of this.routes.keys()) if (!live.has(id)) this.routes.delete(id);
    }
    let changed = false;
    let count = 0;
    if (nodes.size <= OBSTACLE_LIMIT) {
      for (const [id, node] of nodes) {
        if (node.hidden || node.type === "frame" || node.type === "container") continue;
        const size = node.measured;
        if (!size?.width || !size.height) continue;
        count++;
        const { x, y } = node.internals.positionAbsolute;
        const old = this.cards.get(id);
        if (!old || old.left !== x || old.top !== y || old.right !== x + size.width || old.bottom !== y + size.height) {
          changed = true;
          break;
        }
      }
    }
    if (!changed && count === this.cards.size) return;
    this.cards.clear();
    if (nodes.size <= OBSTACLE_LIMIT) {
      for (const [id, node] of nodes) {
        if (node.hidden || node.type === "frame" || node.type === "container") continue;
        const box = bounds(node);
        if (box) this.cards.set(id, box);
      }
    }
    this.obstacles = [...this.cards.values()];
    this.routes.clear();
  }

  route(id: string, source: Point, target: Point, from: RoutingNode, to: RoutingNode): Point[] | undefined {
    const a = bounds(from);
    const b = bounds(to);
    if (!a || !b) return;
    const cached = this.routes.get(id);
    if (cached && cached.source.x === source.x && cached.source.y === source.y &&
      cached.target.x === target.x && cached.target.y === target.y &&
      cached.from.left === a.left && cached.from.right === a.right && cached.from.top === a.top && cached.from.bottom === a.bottom &&
      cached.to.left === b.left && cached.to.right === b.right && cached.to.top === b.top && cached.to.bottom === b.bottom
    ) return cached.points;
    const points = returnRoute(source, target, a, b, this.obstacles.length ? this.obstacles : [a, b]);
    this.routes.set(id, { source, target, from: a, to: b, points });
    return points;
  }
}
