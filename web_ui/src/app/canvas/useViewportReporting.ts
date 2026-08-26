import { useCallback, useEffect, useRef } from "react";
import type { RefObject } from "react";
import type { OnMove } from "@xyflow/react";
import type { SceneStore } from "./sceneStore";
import { makeDebouncedViewportReport } from "./SceneCanvas";

/**
 * R6.3 extraction: viewport (pan/zoom) reporting, pulled out of CanvasInner
 * as its own hook - matching this directory's small-shared-hook convention
 * (see useLodVisibility.ts/useStreamBuffer.ts). Zero behavior change: same
 * timer ref, same cleanup effect, same debounced report call.
 *
 * See makeDebouncedViewportReport's own doc (SceneCanvas.tsx) for why the
 * debounce is load-bearing, not just a burst guard: onMove fires on every
 * frame of a pan/zoom gesture (never just once at the end, unlike
 * NodeResizer's onResizeEnd), so without it setViewState would fire a WS
 * intent every animation frame of every pan/zoom gesture.
 *
 * Returns `viewportTimerRef` alongside `onMove` (not just the callback) -
 * useCanvasPan.ts's own factor-scaled panning needs the SAME timer to
 * persist the settled viewport on pointer-up (programmatic setViewport
 * does not raise React Flow's own onMove), and must share this ref rather
 * than duplicate it so the two gestures' debounces cannot race each other.
 */
export function useViewportReporting(store: SceneStore): {
  onMove: OnMove;
  viewportTimerRef: RefObject<ReturnType<typeof setTimeout> | null>;
} {
  const viewportTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (viewportTimerRef.current) clearTimeout(viewportTimerRef.current);
    },
    [],
  );
  const onMove: OnMove = useCallback(
    (_event, viewport) => {
      makeDebouncedViewportReport(viewportTimerRef, (zoomFactor, scrollX, scrollY) =>
        store.setViewState(zoomFactor, scrollX, scrollY),
      )(viewport.zoom, viewport.x, viewport.y);
    },
    [store],
  );

  return { onMove, viewportTimerRef };
}
