import { useCallback, useEffect, useRef } from "react";
import type { MutableRefObject, RefObject } from "react";
import { useReactFlow, useStoreApi } from "@xyflow/react";
import type { SceneState } from "../../lib/bridge-core/generated/scene-state";
import type { SceneStore } from "./sceneStore";
import { makeDebouncedViewportReport } from "./SceneCanvas";

/**
 * Factor-scaled canvas panning extraction - the drag-speed setting's REAL
 * job, pulled out of CanvasInner as its own hook (matching this
 * directory's small-shared-hook convention; see useLodVisibility.ts/
 * useStreamBuffer.ts). Zero behavior change: same refs, same handlers,
 * same two effects, same dependency arrays.
 *
 * The legacy view multiplied each pan delta by the factor
 * (graphlink_view.py: "self._drag_factor = 1.0  # For controlling pan
 * speed." and `delta *= self._drag_factor` in its pan handler); the
 * straight port mis-wired the factor to node motion instead, which read
 * as the setting doing nothing. React Flow's own panOnDrag has no speed
 * input, so it is disabled on the wrapper element (see the <ReactFlow>
 * element's own panOnDrag prop) and the gesture is owned here, applying
 * the exact legacy contract: viewport moves by pointer-delta times
 * factor, incrementally per event.
 *
 * `canvasWrapperRef`, `sceneRef`, `draggingRef` are all owned by
 * CanvasInner (or, for draggingRef, by useNodeDragAndSizeSync) and passed
 * in rather than duplicated - the wrapper ref is also the pan target and
 * the JSX's own `ref=`, sceneRef is the live drag-factor mirror read by
 * many other things, and draggingRef is the single source of truth for
 * "is a node gesture in flight" that a pointer-hover hit test must not
 * fight with. `setHoveredEdgeId`/`setSelectedConnectionId` are CanvasInner's
 * own connection-hover/selection state setters - this subsystem's mouse-
 * down handler is what resolves click-on-a-connection, and its mouse-move
 * handler is what resolves hover, so both setters have to reach in from
 * here rather than being duplicated. `viewportTimerRef` is shared with
 * useViewportReporting.ts's onMove debounce (see that hook's own doc) so a
 * pan's pointer-up report and a zoom's onMove report can never race each
 * other over the same timer.
 *
 * Purely a side-effecting subsystem: nothing here is consumed by
 * CanvasInner's JSX (panOnDrag={false} on <ReactFlow> is what cedes the
 * gesture to this hook, not a prop wired to anything it returns), so this
 * returns void.
 */
export function useCanvasPan(
  canvasWrapperRef: RefObject<HTMLDivElement | null>,
  // MutableRefObject, not RefObject: sceneRef.current is read directly
  // below (`sceneRef.current.dragFactor`) with no null check, matching the
  // original code's guarantee that it is seeded from a real SceneState and
  // never reassigned to null - see CanvasInner's own sceneRef comment.
  sceneRef: MutableRefObject<SceneState>,
  storeApi: ReturnType<typeof useStoreApi>,
  reactFlow: ReturnType<typeof useReactFlow>,
  store: SceneStore,
  connectionAt: (clientX: number, clientY: number) => string | null,
  fadeConnectionsEnabled: boolean,
  viewportTimerRef: RefObject<ReturnType<typeof setTimeout> | null>,
  draggingRef: RefObject<boolean>,
  setHoveredEdgeId: (updater: (current: string | null) => string | null) => void,
  setSelectedConnectionId: (id: string | null) => void,
): void {
  // Hover is only meaningful while the fade-connections lens is on; testing
  // otherwise would cost a pointer-move hit test for no visible effect.
  const onCanvasMouseMove = useCallback(
    (event: PointerEvent) => {
      if (!fadeConnectionsEnabled) return;
      if (draggingRef.current) return;
      const id = connectionAt(event.clientX, event.clientY);
      setHoveredEdgeId((current) => (current === id ? current : id));
    },
    [connectionAt, fadeConnectionsEnabled, draggingRef, setHoveredEdgeId],
  );

  const panStateRef = useRef<{ lastX: number; lastY: number } | null>(null);
  const onCanvasMouseDown = useCallback(
    (event: PointerEvent) => {
      if ((event.target as HTMLElement).closest(".react-flow__node")) return;
      const id = connectionAt(event.clientX, event.clientY);
      setSelectedConnectionId(id);
      // Begin a pan on a background press (left or middle button), unless
      // Shift is held - that remains React Flow's selection-box gesture.
      const onPane = (event.target as HTMLElement).closest(".react-flow__pane");
      if (onPane && !event.shiftKey && (event.button === 0 || event.button === 1) && id === null) {
        panStateRef.current = { lastX: event.clientX, lastY: event.clientY };
        canvasWrapperRef.current?.classList.add("panning");
      }
    },
    [connectionAt, canvasWrapperRef, setSelectedConnectionId],
  );
  useEffect(() => {
    const onWindowMouseMove = (event: PointerEvent) => {
      const pan = panStateRef.current;
      if (!pan) return;
      const factor = sceneRef.current.dragFactor;
      const dx = (event.clientX - pan.lastX) * factor;
      const dy = (event.clientY - pan.lastY) * factor;
      pan.lastX = event.clientX;
      pan.lastY = event.clientY;
      const { transform } = storeApi.getState();
      reactFlow.setViewport({ x: transform[0] + dx, y: transform[1] + dy, zoom: transform[2] });
    };
    const onWindowMouseUp = () => {
      if (!panStateRef.current) return;
      panStateRef.current = null;
      canvasWrapperRef.current?.classList.remove("panning");
      // Persist the settled viewport the same way onMove does for zooming -
      // programmatic setViewport does not raise React Flow's own onMove.
      const [x, y, zoom] = storeApi.getState().transform;
      makeDebouncedViewportReport(viewportTimerRef, (zoomFactor, scrollX, scrollY) =>
        store.setViewState(zoomFactor, scrollX, scrollY),
      )(zoom, x, y);
    };
    // Pointer events, not mouse events: React Flow's own pan (disabled
    // here so the speed factor can apply) was pointer-based, so handling
    // only mouse would have left touch and pen unable to pan at all.
    window.addEventListener("pointermove", onWindowMouseMove);
    window.addEventListener("pointerup", onWindowMouseUp);
    window.addEventListener("pointercancel", onWindowMouseUp);
    return () => {
      window.removeEventListener("pointermove", onWindowMouseMove);
      window.removeEventListener("pointerup", onWindowMouseUp);
      window.removeEventListener("pointercancel", onWindowMouseUp);
    };
  }, [reactFlow, store, storeApi, sceneRef, canvasWrapperRef, viewportTimerRef]);

  // Attached to the wrapper element directly rather than through JSX props:
  // these are pointer affordances on a drawing surface, not interactions on
  // a semantic control, and binding them here keeps the element free of
  // handler props that would misrepresent it to assistive technology.
  useEffect(() => {
    const el = canvasWrapperRef.current;
    if (!el) return;
    const move = (event: PointerEvent) => onCanvasMouseMove(event);
    const down = (event: PointerEvent) => onCanvasMouseDown(event);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerdown", down);
    return () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerdown", down);
    };
  }, [onCanvasMouseMove, onCanvasMouseDown, canvasWrapperRef]);
}
