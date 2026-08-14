/**
 * Synchronous connection-path updates during a drag.
 *
 * THE PROBLEM THIS SOLVES. A node card's position is a CSS transform on a
 * small div; a connection is a path inside a canvas-sized SVG whose shape
 * has to be recomputed and re-rasterized. Those two are not equally cheap
 * and they do not have to land in the same frame. When they don't, the card
 * arrives on screen at the new position while the line is still drawn for
 * the old one, and a gap opens between the card's connection dot and the
 * end of its line. The gap grows with pointer speed, points opposite the
 * direction of travel, and closes the moment movement stops - because the
 * line simply catches up once nothing is moving any more. Nothing about the
 * app's state is wrong while this happens: the geometry React eventually
 * renders is correct, it just reaches the screen a frame late.
 *
 * THE APPROACH. This is the technique working node editors use. Drawflow,
 * for instance, does not wait for a framework to re-render during a drag:
 * its pointer handler writes the node's position and then immediately
 * writes the `d` attribute of every affected connection, so both are in the
 * DOM before the browser paints that frame. This module does the same thing
 * for this canvas: on each drag frame, after the corrected node position is
 * known, it writes the affected paths directly, inside the same pointer
 * event. React Flow still renders those same edges from its own state a
 * moment later and computes the identical `d` - this write is not a
 * replacement for that, it just gets the correct value into the DOM in time
 * for the current frame instead of the next one.
 *
 * WHY IT IS SAFE. Every value written here is computed the way React Flow
 * computes it (see handlePoint below, which mirrors the library's own
 * handle-anchoring rules), so the imperative write and the subsequent
 * render agree. If anything here cannot resolve - an edge with no rendered
 * path element, a node whose handles have not been measured yet, an edge
 * type this module does not know how to draw - that edge is skipped and
 * left entirely to React Flow, which is the pre-existing behaviour. The
 * plan is rebuilt at the start of every gesture, so it can never describe
 * a stale graph.
 */

import { Position, getBezierPath } from "@xyflow/react";

/** The handle geometry React Flow measured for one node, in flow units. */
interface HandleBound {
  id?: string | null;
  x: number;
  y: number;
  width: number;
  height: number;
  position: Position;
}

interface InternalNodeLike {
  internals: {
    positionAbsolute: { x: number; y: number };
    handleBounds?: { source?: HandleBound[] | null; target?: HandleBound[] | null } | null;
  };
}

/** One edge this gesture must keep in step, resolved once at gesture start. */
export interface EdgeSyncEntry {
  path: SVGPathElement;
  sourceId: string;
  targetId: string;
  source: HandleBound;
  target: HandleBound;
  /** Orthogonal edges use a right-angle path; see OrthogonalEdge.tsx. */
  orthogonal: boolean;
}

/**
 * Where a connection meets a handle, in flow coordinates.
 *
 * Mirrors @xyflow/system's own getHandlePosition: the anchor is centred on
 * the handle across the edge it sits on, and sits at the handle's OUTER rim
 * along the direction it faces - not at the handle's centre. Reproducing
 * that exactly is what keeps this module's write identical to the value
 * React Flow renders a moment later.
 */
function handlePoint(node: InternalNodeLike, handle: HandleBound): { x: number; y: number } {
  const x = handle.x + node.internals.positionAbsolute.x;
  const y = handle.y + node.internals.positionAbsolute.y;
  const { width, height } = handle;
  switch (handle.position) {
    case Position.Top:
      return { x: x + width / 2, y };
    case Position.Right:
      return { x: x + width, y: y + height / 2 };
    case Position.Bottom:
      return { x: x + width / 2, y: y + height };
    case Position.Left:
    default:
      return { x, y: y + height / 2 };
  }
}

/**
 * Resolve every edge touching `movingIds` to the DOM path and handle
 * geometry needed to redraw it, once, at the start of a gesture.
 *
 * Returns an empty array when nothing is resolvable, in which case the
 * caller simply does no synchronous work and React Flow's own rendering is
 * the only thing driving the edges - exactly as before this module existed.
 */
export function buildEdgeSyncPlan(
  edges: Array<{ id: string; source: string; target: string; type?: string }>,
  movingIds: ReadonlySet<string>,
  getInternalNode: (id: string) => InternalNodeLike | undefined,
  container: ParentNode = document,
): EdgeSyncEntry[] {
  const plan: EdgeSyncEntry[] = [];
  for (const edge of edges) {
    if (!movingIds.has(edge.source) && !movingIds.has(edge.target)) continue;
    const sourceNode = getInternalNode(edge.source);
    const targetNode = getInternalNode(edge.target);
    const source = sourceNode?.internals.handleBounds?.source?.[0];
    const target = targetNode?.internals.handleBounds?.target?.[0];
    if (!source || !target) continue;
    const path = container.querySelector<SVGPathElement>(
      `.react-flow__edge[data-id="${CSS.escape(edge.id)}"] .react-flow__edge-path`,
    );
    if (!path) continue;
    plan.push({
      path,
      sourceId: edge.source,
      targetId: edge.target,
      source,
      target,
      orthogonal: edge.type === "orthogonal",
    });
  }
  return plan;
}

/**
 * Redraw every planned edge from the positions this frame produced.
 *
 * `positions` supplies the nodes that moved this frame; anything absent
 * falls back to the position React Flow currently holds, so an edge with
 * only one moving end is drawn correctly without the caller having to
 * enumerate the stationary side.
 */
export function syncEdgePaths(
  plan: readonly EdgeSyncEntry[],
  positions: ReadonlyMap<string, { x: number; y: number }>,
  getInternalNode: (id: string) => InternalNodeLike | undefined,
): void {
  for (const entry of plan) {
    const sourceNode = getInternalNode(entry.sourceId);
    const targetNode = getInternalNode(entry.targetId);
    if (!sourceNode || !targetNode) continue;

    const movedSource = positions.get(entry.sourceId);
    const movedTarget = positions.get(entry.targetId);
    // Draw from this frame's corrected position where the node moved, and
    // from React Flow's own current value where it did not.
    const sourceOrigin = movedSource
      ? { internals: { ...sourceNode.internals, positionAbsolute: movedSource } }
      : sourceNode;
    const targetOrigin = movedTarget
      ? { internals: { ...targetNode.internals, positionAbsolute: movedTarget } }
      : targetNode;

    const s = handlePoint(sourceOrigin, entry.source);
    const t = handlePoint(targetOrigin, entry.target);

    let d: string;
    if (entry.orthogonal) {
      // The right-angle shape OrthogonalEdge.tsx draws, reproduced exactly.
      const midY = s.y + (t.y - s.y) / 2;
      d = `M ${s.x},${s.y} L ${s.x},${midY} L ${t.x},${midY} L ${t.x},${t.y}`;
    } else {
      [d] = getBezierPath({
        sourceX: s.x,
        sourceY: s.y,
        sourcePosition: entry.source.position,
        targetX: t.x,
        targetY: t.y,
        targetPosition: entry.target.position,
      });
    }
    entry.path.setAttribute("d", d);
  }
}
