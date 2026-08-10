/**
 * ADR-013 stage 13.2: the shared zoom/pan behavior every chart type gets for
 * free by wrapping its content in the transformed <g> this hook drives.
 * Deliberately a whole-content viewport transform (scale + translate applied
 * to everything - axes included) rather than a D3-style domain-rescaling
 * zoom: the latter would need each chart type to recompute its own scales
 * live, which is real added complexity for a canvas where the user already
 * has an equivalent "zoom the whole thing" gesture on the outer xyflow
 * canvas itself - this mirrors that same mental model at the chart level
 * instead of introducing a second, different one.
 */

import { useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent as ReactWheelEvent } from "react";

export interface ZoomState {
  scale: number;
  x: number;
  y: number;
}

const IDENTITY: ZoomState = { scale: 1, x: 0, y: 0 };
const MIN_SCALE = 1;
const MAX_SCALE = 6;
const WHEEL_ZOOM_FACTOR = 1.15;

interface DragState {
  pointerId: number;
  startX: number;
  startY: number;
  originX: number;
  originY: number;
}

export interface ZoomPaneHandlers {
  onWheel: (event: ReactWheelEvent<SVGSVGElement>) => void;
  onPointerDown: (event: ReactPointerEvent<SVGSVGElement>) => void;
  onPointerMove: (event: ReactPointerEvent<SVGSVGElement>) => void;
  onPointerUp: (event: ReactPointerEvent<SVGSVGElement>) => void;
  onPointerCancel: (event: ReactPointerEvent<SVGSVGElement>) => void;
}

export interface ZoomPane {
  zoom: ZoomState;
  isZoomed: boolean;
  handlers: ZoomPaneHandlers;
  reset: () => void;
}

export function useZoomPane(): ZoomPane {
  const [zoom, setZoom] = useState<ZoomState>(IDENTITY);
  const dragRef = useRef<DragState | null>(null);

  const onWheel = (event: ReactWheelEvent<SVGSVGElement>) => {
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const pointerX = event.clientX - rect.left;
    const pointerY = event.clientY - rect.top;
    setZoom((prev) => {
      const factor = event.deltaY < 0 ? WHEEL_ZOOM_FACTOR : 1 / WHEEL_ZOOM_FACTOR;
      const nextScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev.scale * factor));
      if (nextScale === prev.scale) return prev;
      // Keep the point under the cursor fixed across the zoom step: that
      // point's position in un-scaled content space must be identical
      // before and after, so solve for the new translate from that.
      const contentX = (pointerX - prev.x) / prev.scale;
      const contentY = (pointerY - prev.y) / prev.scale;
      return { scale: nextScale, x: pointerX - contentX * nextScale, y: pointerY - contentY * nextScale };
    });
  };

  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (zoom.scale <= MIN_SCALE) return;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: zoom.x,
      originY: zoom.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setZoom((prev) => ({
      ...prev,
      x: drag.originX + (event.clientX - drag.startX),
      y: drag.originY + (event.clientY - drag.startY),
    }));
  };

  const endDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null;
  };

  const reset = () => setZoom(IDENTITY);

  return {
    zoom,
    isZoomed: zoom.scale !== IDENTITY.scale || zoom.x !== IDENTITY.x || zoom.y !== IDENTITY.y,
    handlers: { onWheel, onPointerDown, onPointerMove, onPointerUp: endDrag, onPointerCancel: endDrag },
    reset,
  };
}
