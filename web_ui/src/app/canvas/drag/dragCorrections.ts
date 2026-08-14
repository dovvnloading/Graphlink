/**
 * The canvas drag pipeline's pure core.
 *
 * Dragging a node on this canvas is not "move it to the pointer". Three
 * corrections shape every frame, and they must all be applied to the SAME
 * position value before anything renders it:
 *
 *   1. the drag-speed factor (scaleDragPosition), the Qt canvas's own
 *      contract - motion is scaled relative to where the gesture started,
 *      so at a factor below 1 the node deliberately trails the pointer;
 *   2. smart-guide snapping, which pulls the position onto an alignment
 *      with other nodes;
 *   3. the group cascade, which carries a group's members by whatever delta
 *      the group itself ended up moving - necessarily AFTER 1 and 2, or
 *      members ride an uncorrected delta and drift away from their group.
 *
 * Why this file exists at all: these corrections used to be inline in
 * SceneCanvas's change handler, downstream of React Flow's own bookkeeping,
 * which meant the library and this app held two different positions for the
 * same node on every frame - and since the library computes CONNECTION
 * geometry from its own records, a node card and the line attached to it
 * were placed from different numbers. Correcting inside React Flow's change
 * pipeline (see useNodeDragPipeline) makes the corrected position the only
 * position. Keeping the maths here - pure, React-free, framework-free -
 * is what makes that correctness testable without mounting a canvas, and
 * keeps the rule "one position, computed once" enforceable in one place
 * rather than spread through a component.
 *
 * Everything in this module is a pure function of its arguments. The only
 * mutable state a drag needs (where each node started, the size cache) is
 * owned by the caller and passed in, so a test can drive a hundred
 * simulated frames deterministically.
 */

import type { NodeChange } from "@xyflow/react";
// Type-only import: erased at compile time, so this creates no runtime
// dependency back onto the component that consumes this module.
import type { SceneFlowNode } from "../SceneCanvas";
import { scaleDragPosition } from "../sceneStore";
import type { GuideLine } from "../smartGuides";

/** Where a node sat when the current gesture began, keyed by node id. */
export type DragStartPositions = Map<string, { x: number; y: number }>;

/** Node sizes captured once per gesture, for smart-guide alignment. */
export type DragSizeCache = Map<string, { width: number; height: number }>;

/**
 * Everything a correction pass needs from the outside world. Passed in
 * rather than reached for, so this module never depends on React, on the
 * canvas component, or on when the caller happens to run it.
 */
export interface DragCorrectionContext {
  /** The nodes as they currently stand, including any earlier frame's result. */
  nodes: SceneFlowNode[];
  /** Gesture-scoped start positions; this pass records newly-seen nodes into it. */
  dragStarts: DragStartPositions;
  /** Gesture-scoped node sizes, built once at gesture start by the caller. */
  sizeCache: DragSizeCache;
  /** The scene's drag-speed factor (1 = pointer-exact). */
  dragFactor: number;
  /** Whether smart-guide snapping is enabled for this scene. */
  smartGuidesEnabled: boolean;
  /** Snap + guide computation for one frame, injected so this stays pure. */
  computeSnap: (
    nodes: SceneFlowNode[],
    nodeId: string,
    position: { x: number; y: number },
    sizeCache: DragSizeCache,
  ) => { position: { x: number; y: number }; guides: GuideLine[] };
  /** Group-member cascade for one corrected position, injected for the same reason. */
  computeGroupCascade: (
    nodes: SceneFlowNode[],
    draggedId: string,
    position: { x: number; y: number },
  ) => NodeChange<SceneFlowNode>[];
}

export interface DragCorrectionResult {
  /** The batch to hand onward: corrected changes plus any member changes. */
  changes: NodeChange<SceneFlowNode>[];
  /** Alignment guides this frame produced, for the caller to render. */
  guides: GuideLine[];
  /** True when this batch contained at least one frame of an active gesture. */
  touchedGesture: boolean;
}

/**
 * True when a position change belongs to a gesture this pipeline is
 * carrying: either React Flow flagged it as dragging, or it names a node
 * whose start position we recorded earlier in the same gesture.
 *
 * The second half is what catches React Flow's own drag-STOP change. That
 * one must receive the identical correction, or the value the library
 * settles on is not the value the user was just looking at - which is
 * exactly the release-time jump the pre-refactor code worked around by
 * substituting a position after the fact.
 */
function isGestureFrame(
  change: NodeChange<SceneFlowNode>,
  dragStarts: DragStartPositions,
): change is Extract<NodeChange<SceneFlowNode>, { type: "position" }> {
  if (change.type !== "position" || !change.position) return false;
  return change.dragging === true || dragStarts.has(change.id);
}

/**
 * Apply every drag correction to one batch of node changes.
 *
 * Non-position changes, and position changes outside an active gesture,
 * pass through untouched - this function narrows to gesture frames only and
 * has no opinion on anything else in the batch.
 *
 * Guides accumulate across every co-mover in the batch rather than being
 * replaced per node. That is a deliberate departure from the Qt original,
 * which cleared its guides per item and therefore only ever showed the
 * last-processed item's alignment - an artifact of that toolkit's per-item
 * callback ordering, not a design decision worth reproducing.
 */
export function correctDragChanges(
  changes: NodeChange<SceneFlowNode>[],
  context: DragCorrectionContext,
): DragCorrectionResult {
  const { nodes, dragStarts, sizeCache, dragFactor, smartGuidesEnabled } = context;
  const guides: GuideLine[] = [];
  const memberChanges: NodeChange<SceneFlowNode>[] = [];
  let touchedGesture = false;

  const corrected = changes.map((change) => {
    if (!isGestureFrame(change, dragStarts)) return change;
    touchedGesture = true;

    let start = dragStarts.get(change.id);
    if (!start) {
      const node = nodes.find((n) => n.id === change.id);
      start = node ? { ...node.position } : { ...change.position! };
      dragStarts.set(change.id, start);
    }

    // 1. Drag speed, measured from the gesture's own origin.
    let position = scaleDragPosition(start, change.position!, dragFactor);

    // 2. Smart-guide snap, layered on top of React Flow's native grid snap
    //    (which, when enabled, already ran before this change was emitted).
    //    Guides win per axis where both apply, reproducing the Qt canvas's
    //    own per-axis priority.
    if (smartGuidesEnabled) {
      const snapped = context.computeSnap(nodes, change.id, position, sizeCache);
      position = snapped.position;
      guides.push(...snapped.guides);
    }

    // 3. Group members ride the FINAL delta, so they stay in lockstep with
    //    the group rather than with the raw pointer.
    memberChanges.push(...context.computeGroupCascade(nodes, change.id, position));

    return { ...change, position };
  });

  return {
    changes: memberChanges.length > 0 ? [...corrected, ...memberChanges] : corrected,
    guides,
    touchedGesture,
  };
}

/**
 * The positions a settled gesture must persist, for the node that was
 * released and for every group member it carried.
 *
 * Returned as one list because they have to be committed as a single
 * batch: committing them one at a time makes the server publish a scene
 * after each, and a group's bounds are derived from its members, so those
 * intermediate publishes render as a group visibly stretching and
 * resettling rather than merely being briefly stale.
 */
export function collectSettledPositions(
  nodes: SceneFlowNode[],
  releasedNodeId: string,
  isGroup: (node: SceneFlowNode | undefined) => boolean,
  membersOf: (nodes: SceneFlowNode[], group: SceneFlowNode) => Set<string>,
): Array<{ id: string; x: number; y: number }> {
  const settled = nodes.find((n) => n.id === releasedNodeId);
  if (!settled) return [];
  const positions = [{ id: releasedNodeId, x: settled.position.x, y: settled.position.y }];
  if (isGroup(settled)) {
    for (const memberId of membersOf(nodes, settled)) {
      const member = nodes.find((n) => n.id === memberId);
      if (member) positions.push({ id: memberId, x: member.position.x, y: member.position.y });
    }
  }
  return positions;
}
