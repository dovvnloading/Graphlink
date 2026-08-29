import { useCallback, useEffect, useRef, useState } from "react";
import type { MutableRefObject, RefObject } from "react";
import {
  experimental_useOnNodesChangeMiddleware,
  useReactFlow,
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import type { SceneState } from "../../lib/bridge-core/generated/scene-state";
import type { SceneStore } from "./sceneStore";
import { NODE_SIZE_REPORT_DEBOUNCE_MS } from "./canvasConstants";
import type { GuideLine } from "./smartGuides";
import {
  applyGroupDragDelta,
  buildDragSizeCache,
  collectChangedNodeSizes,
  collectTransitiveMemberIds,
  computeSmartGuideFrame,
  groupDragKindOf,
  measuredNodeSize,
  type SceneFlowNode,
} from "./SceneCanvas";

/**
 * ADR-011 follow-up / DRAG-SYNC REBUILD extraction: the single, already-
 * interdependent node-drag + node-size-reporting subsystem, pulled out of
 * CanvasInner as its own hook - matching this directory's small-shared-
 * hook convention (see useLodVisibility.ts/useStreamBuffer.ts), but moved
 * VERBATIM (same variable names, same comments, same logic, same relative
 * order) since this is a pure relocation, not a rewrite. This is the most
 * safety-critical piece of the canvas: getting the drag-position
 * correction and node/edge sync wrong here was a real, previously-shipped,
 * user-visible bug (see the middleware's own DRAG-SYNC REBUILD doc below).
 *
 * The main scene-sync effect (which translates backend scene snapshots
 * into React Flow's node list) is NOT here - it is a separate concern that
 * stays in CanvasInner - but it reads `nodesRef.current`, `draggingRef
 * .current`, and depends on `dragActive` in its own dependency array, so
 * this hook returns the REF OBJECTS themselves (not `.current` values) so
 * that effect keeps reading/mutating the exact same ref identities.
 *
 * `scene` is threaded through (rather than destructuring just
 * `smartGuides`) so the middleware can read the current guide setting in its
 * event-time callback. The drag-speed factor no longer affects node motion;
 * it scales canvas panning instead (useCanvasPan.ts), so it is intentionally
 * absent from the middleware's dependency list.
 */
export function useNodeDragAndSizeSync(
  scene: SceneState,
  reactFlow: ReturnType<typeof useReactFlow>,
  store: SceneStore,
): {
  // MutableRefObject, not RefObject: CanvasInner's own scene-sync effect
  // reassigns `nodesRef.current` directly (see that effect's own comment),
  // which RefObject's readonly `current` would reject.
  nodesRef: MutableRefObject<SceneFlowNode[]>;
  draggingRef: RefObject<boolean>;
  dragActive: boolean;
  smartGuideLines: GuideLine[];
  onNodesChange: (changes: NodeChange<SceneFlowNode>[]) => void;
  onDelete: (payload: { nodes: Node[]; edges: Edge[] }) => void;
  // BUG FIX (node position reverting after a fast drag+release): ids just
  // committed via store.moveNodes below, whose real position CanvasInner's
  // scene-sync effect must keep trusting from `nodesRef` rather than from
  // `scene` - see that effect's own doc for why `scene` is stale at exactly
  // this moment and what clears an id back out of this set.
  pendingSettledIdsRef: MutableRefObject<Set<string>>;
} {
  // Local node state exists so dragging is fluid; backend snapshots are the
  // truth and reconcile in whenever nothing is being dragged. dragStartRef
  // records where each gesture began (see the middleware's bookkeeping).
  // React Flow owns the rendered node collection (see the <ReactFlow>
  // element's own defaultNodes comment). This component keeps only a mirror
  // ref for its own logic - drag corrections, delete routing, scene merge -
  // so a drag frame never has to travel through React state to reach the
  // renderer.
  // Which nodes the current gesture is carrying. Membership is all this
  // needs to record: it exists so the drag-STOP change (which arrives
  // without the dragging flag) can still be recognised as part of the
  // gesture. It held start POSITIONS while the drag-speed factor scaled
  // node motion from its origin; that factor now applies to canvas panning
  // instead, so the positions were dead weight.
  const dragStartRef = useRef<Set<string>>(new Set());
  const draggingRef = useRef(false);
  // ADR-011 stage 11.3: the smart-guide size cache - populated ONCE per drag
  // gesture (see the `startingNewDrag` check inside onNodesChange below,
  // keyed off draggingRef's value from BEFORE that call) via
  // buildDragSizeCache, then read back by computeSmartGuideFrame for every
  // remaining frame of that SAME gesture instead of re-querying the DOM. A
  // plain ref (not state) since a rebuild must never itself trigger a
  // re-render - it only matters to onNodesChange's own closure.
  const dragSizeCacheRef = useRef<Map<string, { width: number; height: number }>>(new Map());
  // BUG FIX (node position reverting after a fast drag+release): store
  // .moveNodes below is fire-and-forget (SceneStore.moveNodes just calls
  // transport.fireIntent, no local optimistic scene update) - the backend
  // echo carrying the new position back into `scene` has not landed yet by
  // the time this drag's own settle runs. CanvasInner's scene-sync effect
  // reconciles the instant `dragActive` flips false (see that effect's own
  // dependency-array comment, added in #REVIEW-FIX round 3 to stop losing an
  // UNRELATED mid-drag scene update) - which, without this set, rebuilds
  // straight from that still-stale `scene` and snaps the just-dropped node
  // back to where it started, only to jump forward again once the real echo
  // arrives moments later. See withPreservedFlowState's own doc for the read
  // side of this ref.
  const pendingSettledIdsRef = useRef<Set<string>>(new Set());

  // R7.5b-3: the smart-guide lines currently visible during a drag - local
  // component state only, never scene state, matching legacy's non-persisted
  // ChatScene.smart_guide_lines. Set per drag frame in onNodesChange, cleared
  // on drag end; the render-time gate below (not an effect) hides any stale
  // lines instantly if the toggle flips off mid-drag.
  const [smartGuideLines, setSmartGuideLines] = useState<GuideLine[]>([]);

  // True for exactly the duration of an active node-drag gesture, mirrored
  // from onNodesChange's own dragging/drag-end changes (state, unlike
  // draggingRef, because it must re-render <ReactFlow> with a different
  // onlyRenderVisibleElements value below). Why it exists: with
  // off-viewport culling active, React Flow removes any node whose rect
  // leaves the viewport - INCLUDING the node currently being dragged.
  // Measured on a real scene: dragging a connected node across the
  // viewport boundary unmounted it mid-gesture for 49 straight frames (the
  // node simply vanished under the cursor) while its edge stayed rendered,
  // pointing at nothing. Auto-pan during a drag makes this worse: every
  // node the pan pushes across the boundary pops in or out mid-gesture.
  // Suspending culling for the duration of the drag (same suspension
  // mechanism exportInProgress already uses) keeps everything mounted while
  // anything is moving; culling resumes the moment the drag ends.
  const [dragActive, setDragActive] = useState(false);

  // ADR-011 follow-up / drag-sync rebuild: the CURRENT local flow nodes,
  // readable from the change middleware below without making it a
  // dependency (a middleware that re-registers on every node change would
  // thrash the store's middleware map every frame of a drag).
  // Kept in step with `nodes` by the two places that ever produce a new
  // array (the scene-sync effect and onNodesChange below), never by a
  // write during render: a drag can deliver several frames before React
  // commits, and each frame must build on the previous frame's result
  // rather than on whatever the last commit happened to hold.
  const nodesRef = useRef<SceneFlowNode[]>([]);
  // Guides produced by the middleware's most recent drag frame, handed to
  // onNodesChange below to publish as state. The middleware itself must
  // stay side-effect-free with respect to React state: it executes INSIDE
  // React Flow's own store update, where a setState call would be a
  // render-phase update.
  const pendingGuidesRef = useRef<GuideLine[]>([]);
  // Node-size reporting: the backend fits frames/containers around their
  // members but cannot measure a rendered node itself, so the canvas tells
  // it what it actually laid out. Debounced for the same reason onMove is
  // (a streaming reply re-measures its node on nearly every token) and
  // diffed against what was last sent, so a settled canvas reports nothing.
  const nodeSizeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reportedSizesRef = useRef<Map<string, string>>(new Map());
  // REVIEW-FIX: which node ids were unmeasurable as of the last flush - a
  // node genuinely unmounted under onlyRenderVisibleElements (off-viewport)
  // has no DOM to measure, so measuredNodeSize returns null for it and
  // collectChangedNodeSizes correctly skips it rather than reporting a
  // false zero. If that node's content grows while it sits off-viewport
  // (e.g. a streaming member inside a frame), the growth produces no
  // dimensions change at all until the node remounts - so the backend's
  // frame/container bbox keeps computing against the member's last known
  // (smaller) size, and the member can render visibly outside its frame's
  // box for a moment once it scrolls back in. Tracked here so onNodesChange
  // below can recognise exactly that "remount of a previously-unmeasurable
  // node" transition and flush its real size immediately instead of behind
  // the standard debounce, which exists to coalesce a busy streaming
  // node's continuous resizes - not to delay a one-off catch-up report that
  // is already late by definition. Rebuilt wholesale on every flush (below)
  // rather than mutated incrementally, so it always reflects that flush's
  // own measurements.
  const unmeasurableIdsRef = useRef<Set<string>>(new Set());
  useEffect(
    () => () => {
      if (nodeSizeTimerRef.current) clearTimeout(nodeSizeTimerRef.current);
    },
    [],
  );
  // The actual measure-diff-send pass, factored out so it can run either
  // debounced (reportNodeSizesSoon, the common streaming-resize case) or
  // immediately (the remount catch-up case in onNodesChange below).
  const flushNodeSizes = useCallback(() => {
    const currentlyUnmeasurable = new Set<string>();
    const changed = collectChangedNodeSizes(
      nodesRef.current.map((n) => n.id),
      (id) => {
        const size = measuredNodeSize(reactFlow, id);
        if (size === null) currentlyUnmeasurable.add(id);
        return size;
      },
      reportedSizesRef.current,
    );
    unmeasurableIdsRef.current = currentlyUnmeasurable;
    store.reportNodeSizes(changed);
  }, [reactFlow, store]);
  const reportNodeSizesSoon = useCallback(() => {
    if (nodeSizeTimerRef.current) clearTimeout(nodeSizeTimerRef.current);
    nodeSizeTimerRef.current = setTimeout(() => {
      nodeSizeTimerRef.current = null;
      flushNodeSizes();
    }, NODE_SIZE_REPORT_DEBOUNCE_MS);
  }, [flushNodeSizes]);

  /**
   * DRAG-SYNC REBUILD: the drag-position corrections this canvas applies -
   * smart-guide snapping and
   * the group-member cascade - now run as a React Flow CHANGE MIDDLEWARE
   * (experimental_useOnNodesChangeMiddleware), i.e. INSIDE React Flow's own
   * updateNodePositions, before it commits anything.
   *
   * Why this is an architecture change and not a tweak: these corrections
   * used to run in onNodesChange, which is downstream of React Flow's own
   * bookkeeping. The consequence was documented in this file's own drag-end
   * comment - "the drag-factor/smart-guide corrections applied per frame
   * never feed back into RF's drag state" - so on every frame of every
   * drag, React Flow's internal position for a node and the position this
   * app actually rendered it at were two different numbers. Node cards are
   * positioned from the app's corrected value while EDGE geometry is
   * computed by React Flow itself (getEdgePosition, off its own node
   * records), so the two ends of that disagreement are exactly the node and
   * the line attached to it. That is the connection-not-tracking-the-node
   * artifact, and no amount of downstream re-render tuning can close it,
   * because the two values are computed from different inputs.
   *
   * Running the corrections here makes the corrected position the ONLY
   * position: React Flow's own change pipeline carries it, so the node
   * transform and the edge endpoints are derived from one number in one
   * commit. Group-member changes are appended to the same batch for the
   * same reason they always were - a member must move in lockstep with its
   * group, and now that lockstep is inside React Flow's update rather than
   * bolted onto the side of it.
   */
  const dragCorrectionMiddleware = useCallback(
    (changes: NodeChange<SceneFlowNode>[]): NodeChange<SceneFlowNode>[] => {
      const currentNodes = nodesRef.current;
      // Rebuild the smart-guide size cache exactly ONCE per gesture, on its
      // first frame - draggingRef still holds the PREVIOUS call's value here
      // (onNodesChange below is what advances it), so this is true only when
      // nothing was already dragging coming into this batch. A multi-select
      // drag's first frame reports several dragging changes in ONE batch and
      // still rebuilds once, not once per change.
      const startingGesture = !draggingRef.current && changes.some((c) => c.type === "position" && c.dragging);
      if (startingGesture && scene.smartGuides) {
        dragSizeCacheRef.current = buildDragSizeCache(reactFlow, currentNodes);
      }
      const memberChanges: NodeChange<SceneFlowNode>[] = [];
      // R7.5b-3: guides re-derive every drag frame. DELIBERATE deviation for
      // multi-select drags (review-confirmed): legacy cleared guides
      // per-item, so only the LAST-processed item's guides survived each
      // frame - an artifact of Qt's per-item itemChange ordering, not a
      // design choice. Accumulating every co-mover's guides shows all live
      // alignments instead of an arbitrary one.
      const frameGuides: GuideLine[] = [];
      let sawGestureFrame = false;

      const corrected = changes.map((change) => {
        if (change.type !== "position" || !change.position) return change;
        // A gesture frame is any position change that is either flagged
        // dragging, or belongs to a node whose drag start this gesture has
        // already recorded - the latter catches React Flow's own drag-STOP
        // change, which must receive the identical correction so the value
        // React Flow settles on is the value the user actually saw. Passing
        // that one through raw is what used to make a released node jump off
        // its corrected position and then reconcile after the backend echo.
        if (!change.dragging && !dragStartRef.current.has(change.id)) return change;
        sawGestureFrame = true;
        // Gesture membership only - see dragStartRef's own comment. The
        // drag-speed factor deliberately does NOT touch node motion: the
        // legacy feature it ports scaled canvas PANNING, never item
        // movement (graphlink_view.py: "For controlling pan speed", whose
        // pan handler multiplied each mouse delta by the factor). The
        // straight port mis-wired it to node dragging; the factor now
        // applies in the wrapper's own pan handler below.
        dragStartRef.current.add(change.id);
        let finalPosition = { ...change.position };
        // R7.5b-3: smart-guide snap, as a LAYERED PASS on top of React
        // Flow's native grid-snap (which, when enabled, already ran inside
        // RF before this change was emitted) - the recorded design
        // decision, chosen over re-implementing grid-snap manually. Smart
        // guides win per-axis when both would apply, reproducing legacy
        // snap_position's own per-axis priority. Runs BEFORE
        // applyGroupDragDelta below so carried group members ride the
        // corrected delta. Legacy's !isSelected() candidate exclusion is
        // translated as !n.selected (every co-mover in a multi-select drag
        // reports its own dragging change with selected=true); a dragged
        // group's own members are additionally excluded - they move in
        // lockstep with the group, so "aligning" against them is always
        // trivially true and would freeze the drag (a gap legacy never had
        // to answer: this canvas carries members via synthetic deltas, not
        // Qt child-item parenting - per the recorded design decision, only
        // the group's own rect snaps and members ride the delta).
        // ADR-011 stage 11.3: reads dragSizeCacheRef (populated ONCE at
        // this gesture's own drag-start, above) instead of calling
        // measuredNodeSize directly here - see computeSmartGuideFrame's
        // own doc for why this is now a pure, cache-only lookup with zero
        // DOM access per frame.
        if (scene.smartGuides) {
          const frame = computeSmartGuideFrame(currentNodes, change.id, finalPosition, dragSizeCacheRef.current);
          finalPosition = frame.position;
          frameGuides.push(...frame.guides);
        }
        memberChanges.push(...applyGroupDragDelta(currentNodes, change.id, finalPosition));
        return { ...change, position: finalPosition };
      });

      if (!sawGestureFrame) return changes;
      pendingGuidesRef.current = frameGuides;

      // Group members ride in the SAME batch as the node that carries them,
      // so React Flow commits the group and its members together.
      return memberChanges.length > 0 ? [...corrected, ...memberChanges] : corrected;
    },
    [scene.smartGuides, reactFlow],
  );

  // Registers the correction above inside React Flow's own change pipeline.
  // The hook is experimental in @xyflow/react 12.11 - it is nonetheless the
  // library's only sanctioned point for transforming changes BEFORE they are
  // committed, which is precisely what this canvas needs (see the
  // middleware's own doc comment). If a future version renames it, the
  // fallback is to move the same function back into onNodesChange and
  // accept the position divergence it exists to remove.
  // react-hooks/refs cannot see through this hook: it only STORES the
  // function (in React Flow's middleware map) and the stored function is
  // invoked later from inside a pointer-driven store update, never during
  // render - so the ref reads inside it are ordinary event-time reads, which
  // is exactly what that rule exists to require.
  // eslint-disable-next-line react-hooks/refs
  experimental_useOnNodesChangeMiddleware<SceneFlowNode>(dragCorrectionMiddleware);

  const onNodesChange = useCallback(
    (changes: NodeChange<SceneFlowNode>[]) => {
      // Every change reaching this point has already been corrected by the
      // middleware above, so this handler no longer computes positions at
      // all - it applies the batch to local state and runs the side effects
      // a settled gesture owes the rest of the app.
      const currentNodes = nodesRef.current;
      const settledMoveIntents: Array<{ id: string; x: number; y: number }> = [];
      let sawDragging = false;
      let sawDragEnd = false;
      // React Flow's own "this node was (re)measured" signal - the trigger
      // for reporting sizes back to the backend's group-bounds math. Only
      // schedules the debounced flush; the flush itself re-reads every
      // node and sends just the diffs.
      //
      // REVIEW-FIX: if any measured id was unmeasurable as of the last
      // flush (unmeasurableIdsRef, populated by flushNodeSizes), this is a
      // node that just remounted after being off-viewport - see that ref's
      // own doc for why its content may have grown while unmounted, with
      // no dimensions change able to fire during that whole window. That
      // catch-up is flushed immediately, bypassing the debounce meant for
      // coalescing an actively-streaming node's continuous resizes - a
      // remount is a discrete one-off event, not a flurry, so there is no
      // reason to make the frame's now-stale bbox wait out a debounce
      // window it was never the cause of. Any pending debounced flush is
      // cleared first so it doesn't fire again a moment later.
      if (changes.some((change) => change.type === "dimensions")) {
        const remountedFromUnmeasurable = changes.some(
          (change) => change.type === "dimensions" && unmeasurableIdsRef.current.has(change.id),
        );
        if (remountedFromUnmeasurable) {
          if (nodeSizeTimerRef.current) {
            clearTimeout(nodeSizeTimerRef.current);
            nodeSizeTimerRef.current = null;
          }
          flushNodeSizes();
        } else {
          reportNodeSizesSoon();
        }
      }

      for (const change of changes) {
        if (change.type !== "position") continue;
        if (change.dragging) {
          draggingRef.current = true;
          sawDragging = true;
          continue;
        }
        if (!dragStartRef.current.has(change.id)) continue;
        // Drag end for a node this gesture actually carried.
        draggingRef.current = false;
        sawDragEnd = true;
        dragStartRef.current.delete(change.id);
        const settled = currentNodes.find((n) => n.id === change.id);
        if (!settled) continue;
        // R6.1 follow-up: collected, not committed here directly - see the
        // single store.moveNodes call below for why a group drag's whole
        // commit (the group's own node plus every cascaded member) must land
        // as ONE atomic batch, not N individual moveNode calls.
        settledMoveIntents.push({ id: change.id, x: settled.position.x, y: settled.position.y });
        if (groupDragKindOf(settled)) {
          for (const memberId of collectTransitiveMemberIds(currentNodes, settled)) {
            const member = currentNodes.find((n) => n.id === memberId);
            if (member) settledMoveIntents.push({ id: memberId, x: member.position.x, y: member.position.y });
          }
        }
      }

      // R6.1 follow-up: ONE atomic batch for the WHOLE drag-end (the dragged
      // node's own settled position plus every cascaded member), not the
      // group's own moveNode call followed by N separate member moveNode
      // calls. Each individual moveNode intent publishes its own scene
      // snapshot the instant it lands - calling it once per node in a group
      // drag meant the frontend rendered N genuinely inconsistent
      // intermediate states (some members caught up, some not) in rapid
      // succession right after release, and since a frame/container's bounds
      // correctly grow to enclose whatever the CURRENT member bbox is, those
      // intermediate states visibly stretched and resettled instead of just
      // being briefly stale - a real glitch on every group drag, not a
      // cosmetic footnote. moveNodes commits every position in one pass
      // server-side and publishes exactly once.
      if (settledMoveIntents.length > 0) {
        store.moveNodes(settledMoveIntents);
        // See pendingSettledIdsRef's own doc above - cleared once a genuinely
        // fresh scene arrives (CanvasInner's scene-sync effect), not here.
        for (const intent of settledMoveIntents) pendingSettledIdsRef.current.add(intent.id);
      }
      // Guides re-derive every drag frame (legacy cleared + re-added its
      // QGraphicsLineItems per recompute); drag end always clears. The
      // values come from the middleware's own most recent frame.
      if (sawDragging) {
        const frameGuides = pendingGuidesRef.current;
        setSmartGuideLines((current) => (current.length === 0 && frameGuides.length === 0 ? current : frameGuides));
      } else if (sawDragEnd) {
        pendingGuidesRef.current = [];
        setSmartGuideLines((current) => (current.length === 0 ? current : []));
      }
      // Suspend off-viewport culling while a drag is in flight - see
      // dragActive's own doc comment above. Same-value setState calls (every
      // frame after the first) bail out without a re-render.
      if (sawDragging) setDragActive(true);
      else if (sawDragEnd) setDragActive(false);
      // Built off nodesRef (not the setNodes updater form) so the mirror
      // advances in this same event: a drag can deliver several frames
      // before React commits, and each must build on the previous frame's
      // result - see nodesRef's own comment above.
      // React Flow has ALREADY applied this batch to its own store by the
      // time this handler runs (that is what uncontrolled node state buys:
      // the renderer is updated synchronously inside the pointer event, the
      // way working node editors do it, instead of waiting for React state
      // and a post-paint sync). All that remains here is keeping this
      // component's mirror in step for its own logic.
      nodesRef.current = applyNodeChanges(changes, currentNodes);
    },
    [store, reportNodeSizesSoon, flushNodeSizes],
  );

  const onDelete = useCallback(
    ({ nodes: deletedNodes, edges: deletedEdges }: { nodes: Node[]; edges: Edge[] }) => {
      // Chat nodes delete through their own reparent-preserving intent
      // (backend/canvas.py's delete_chat_node) so the Delete key matches the
      // context menu's "Delete Node" exactly - a plain cascade-delete would
      // orphan every child branch instead of splicing it back to the
      // grandparent. Every other kind still uses the generic cascade-delete.
      const chatNodeIds: string[] = [];
      const otherNodeIds: string[] = [];
      for (const deleted of deletedNodes) {
        const flowNode = nodesRef.current.find((n) => n.id === deleted.id);
        (flowNode?.type === "chat" ? chatNodeIds : otherNodeIds).push(deleted.id);
      }
      for (const id of chatNodeIds) store.deleteChatNode(id);
      store.removeNodes(otherNodeIds);
      // Skip edges an already-deleted node takes with it server-side
      // (cascade-delete or the chat reparent both manage their own edges).
      const dying = new Set(deletedNodes.map((n) => n.id));
      store.removeEdges(
        deletedEdges.filter((e) => !dying.has(e.source) && !dying.has(e.target)).map((e) => e.id),
      );
    },
    [store],
  );

  return { nodesRef, draggingRef, dragActive, smartGuideLines, onNodesChange, onDelete, pendingSettledIdsRef };
}
