/**
 * The canvas that draws every connection on the scene.
 *
 * WHY THIS REPLACES THE PREVIOUS APPROACH. Connections used to be rendered
 * by the flow library as SVG elements, one per link, positioned from the
 * library's own node records and reconciled by React. During a drag that
 * arrangement put the node card and the line attached to it on two
 * different update paths, and the line's path was observed on screen still
 * drawn for a position the node had already left - a gap that grew with
 * pointer speed and closed the moment movement stopped. Several attempts to
 * make those two paths agree (correcting positions earlier, moving state
 * ownership, writing the SVG path imperatively, re-writing it every frame)
 * all failed to change what the user saw.
 *
 * This removes the disagreement instead of trying to synchronise it. There
 * is now exactly one thing that draws connections: this component. Every
 * frame it reads the CURRENT node positions and the CURRENT viewport
 * transform and redraws, in immediate mode. There is no per-link element,
 * no retained scene graph, and nothing that React can reconcile a frame
 * later - so a link cannot be drawn from a position that is out of date,
 * because there is no stored position to be out of date. This is the shape
 * long-standing node editors use for exactly this reason.
 *
 * The component draws only. Interaction (hover, selection) is hit-tested
 * against the same geometry by the canvas's owner, using
 * connectionGeometry's isPointOnConnection, so what is drawn and what is
 * clickable can never disagree either.
 */

import { useCallback, useEffect, useRef } from "react";
import { Position, useStoreApi } from "@xyflow/react";
import {
  anchorPoint,
  connectionPath,
  drawDirection,
  traceConnection,
  type ConnectionPath,
  type HandleGeometry,
  type HandleSide,
} from "./connectionGeometry";

import { connectionDashes, type ConnectionRole } from "./connectionAppearance";

/** One link to draw, in the terms this canvas needs. */
export interface ConnectionSpec {
  id: string;
  source: string;
  target: string;
  orthogonal: boolean;
  role?: ConnectionRole;
}

export interface ConnectionCanvasProps {
  connections: readonly ConnectionSpec[];
  /** Link currently under the pointer, exempt from fading. */
  hoveredId: string | null;
  /** Link currently selected, drawn emphasised. */
  selectedId: string | null;
  /** Dim unrelated links while keeping the active routes visible. */
  fadeEnabled: boolean;
  /** Resting stroke colour, supplied by the theme. */
  stroke: string;
  /** Stroke colour for the selected link. */
  selectedStroke: string;
  /** Opacity applied to non-hovered links while fading is on. */
  fadedOpacity: number;
  /**
   * Receives the geometry this canvas last drew, so its owner can hit-test
   * against exactly what is on screen rather than recomputing it.
   */
  onGeometry?: (paths: Map<string, ConnectionPath>) => void;
}

/** Maps the flow library's handle position onto this module's own vocabulary. */
function sideOf(position: Position | undefined): HandleSide {
  switch (position) {
    case Position.Top:
      return "top";
    case Position.Right:
      return "right";
    case Position.Left:
      return "left";
    case Position.Bottom:
    default:
      return "bottom";
  }
}

interface HandleBoundLike {
  x: number;
  y: number;
  width: number;
  height: number;
  position: Position;
}

function toHandleGeometry(bound: HandleBoundLike): HandleGeometry {
  return { x: bound.x, y: bound.y, width: bound.width, height: bound.height, side: sideOf(bound.position) };
}

export function ConnectionCanvas({
  connections,
  hoveredId,
  selectedId,
  fadeEnabled,
  stroke,
  selectedStroke,
  fadedOpacity,
  onGeometry,
}: ConnectionCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const storeApi = useStoreApi();
  const frameRef = useRef<number | null>(null);
  // The draw inputs are read inside the frame loop rather than captured in
  // its closure, so the loop never has to be torn down and rebuilt when a
  // colour or a hover changes.
  const propsRef = useRef({ connections, hoveredId, selectedId, fadeEnabled, stroke, selectedStroke, fadedOpacity, onGeometry });
  useEffect(() => {
    propsRef.current = { connections, hoveredId, selectedId, fadeEnabled, stroke, selectedStroke, fadedOpacity, onGeometry };
  }, [connections, hoveredId, selectedId, fadeEnabled, stroke, selectedStroke, fadedOpacity, onGeometry]);

  const draw = useCallback((): boolean => {
    const canvas = canvasRef.current;
    if (!canvas) return true;
    // A null context means this environment has no 2D canvas at all (jsdom
    // under test, for one). Report it so the frame loop stops rather than
    // asking again sixty times a second for the lifetime of the component.
    const ctx = canvas.getContext("2d");
    if (!ctx) return false;
    const state = storeApi.getState();
    const { width, height, transform, nodeLookup } = state;
    if (!width || !height) return true;

    // Match the backing store to the display size and pixel density, so
    // lines stay crisp on high-density screens and after a window resize.
    const ratio = window.devicePixelRatio || 1;
    const backingWidth = Math.round(width * ratio);
    const backingHeight = Math.round(height * ratio);
    if (canvas.width !== backingWidth || canvas.height !== backingHeight) {
      canvas.width = backingWidth;
      canvas.height = backingHeight;
    }
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const [panX, panY, zoom] = transform;
    // Draw in screen space: the viewport transform is applied here rather
    // than by scaling the canvas itself, which keeps strokes a constant
    // width on screen and free of scaling artefacts at any zoom.
    ctx.setTransform(ratio * zoom, 0, 0, ratio * zoom, ratio * panX, ratio * panY);
    // Stroke width is set per connection below (selected links are drawn
    // heavier), so none is set here.
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    const {
      connections: specs,
      hoveredId: hovered,
      selectedId: selected,
      fadeEnabled: fade,
      stroke: baseStroke,
      selectedStroke: activeStroke,
      fadedOpacity: dimmed,
      onGeometry: report,
    } = propsRef.current;

    const geometry = new Map<string, ConnectionPath>();
    type Highlight = "neighbor" | "hovered" | "selected";
    type HighlightedLink = { path: ConnectionPath; role: ConnectionRole };
    const foreground: Record<Highlight, HighlightedLink[]> = { neighbor: [], hovered: [], selected: [] };
    const dashes = new Map<ConnectionRole, number[]>();
    function paint(context: CanvasRenderingContext2D, path: ConnectionPath, role: ConnectionRole, highlight?: Highlight) {
      let dash = dashes.get(role);
      if (!dash) {
        dash = connectionDashes(role, zoom);
        dashes.set(role, dash);
      }
      context.setLineDash(dash);
      context.strokeStyle = highlight ? activeStroke : baseStroke;
      context.globalAlpha = highlight === "neighbor" ? 0.78 : fade && !highlight ? dimmed : 1;
      const width = highlight === "selected" ? 3 : highlight ? 2 : 1.5;
      context.lineWidth = width / zoom;
      traceConnection(context, path);
      if (highlight) {
        context.setLineDash([]);
        context.lineWidth = 1.8 / zoom;
        drawDirection(context, path, zoom);
      }
    }
    for (const spec of specs) {
      const sourceNode = nodeLookup.get(spec.source);
      const targetNode = nodeLookup.get(spec.target);
      if (!sourceNode || !targetNode) continue;
      const sourceBound = sourceNode.internals.handleBounds?.source?.[0];
      const targetBound = targetNode.internals.handleBounds?.target?.[0];
      if (!sourceBound || !targetBound) continue;

      const sourceHandle = toHandleGeometry(sourceBound as HandleBoundLike);
      const targetHandle = toHandleGeometry(targetBound as HandleBoundLike);
      const from = anchorPoint(
        sourceNode.internals.positionAbsolute.x,
        sourceNode.internals.positionAbsolute.y,
        sourceHandle,
      );
      const to = anchorPoint(
        targetNode.internals.positionAbsolute.x,
        targetNode.internals.positionAbsolute.y,
        targetHandle,
      );
      const path = connectionPath(from, sourceHandle.side, to, targetHandle.side, spec.orthogonal);
      geometry.set(spec.id, path);

      if (spec.id === selected) foreground.selected.push({ path, role: spec.role });
      else if (spec.id === hovered) foreground.hovered.push({ path, role: spec.role });
      else if (sourceNode.selected || targetNode.selected) foreground.neighbor.push({ path, role: spec.role });
      else paint(ctx, path, spec.role);
    }
    // At crossings, draw the path the user is tracing above resting links.
    for (const highlight of ["neighbor", "hovered", "selected"] as const) {
      for (const link of foreground[highlight]) paint(ctx, link.path, link.role, highlight);
    }
    ctx.globalAlpha = 1;
    report?.(geometry);
    return true;
  }, [storeApi]);

  useEffect(() => {
    // Redrawing every frame is deliberate. It is what guarantees a link can
    // never be shown at a position the node has already left, and the cost
    // is a few dozen curves - far less than reconciling an element per link.
    const tick = () => {
      if (!draw()) {
        frameRef.current = null;
        return;
      }
      frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    };
  }, [draw]);

  return (
    <canvas
      ref={canvasRef}
      className="scene-connection-canvas"
      // Purely presentational: every pointer interaction is hit-tested by
      // this canvas's owner against the geometry it reports, so the canvas
      // itself must never intercept events destined for a node card.
      style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: -1 }}
      aria-hidden="true"
    />
  );
}
