import { Handle, NodeResizer, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import type { ChartDataRow } from "../../lib/bridge-core/generated/scene-state";
import { withAuthToken } from "../../lib/auth/token";
import {
  CHART_MAX_HEIGHT,
  CHART_MAX_WIDTH,
  CHART_MIN_HEIGHT,
  CHART_MIN_WIDTH,
  CHART_RESIZE_DEBOUNCE_MS,
  LOD_ZOOM_THRESHOLD,
} from "./canvasConstants";

/**
 * The chart node (Qt-removal plan R6.2) - graphlink_canvas_chart_item.py's
 * React successor: a card wrapping a backend-rendered PNG (bar/line/pie/
 * histogram/sankey). Push-only, same as every other content card - the
 * chart DATA arrives already canonicalized via the scene document; this
 * view never touches matplotlib or chart math itself (see
 * graphlink_chart_rendering.py for that). Unlike the legacy QPainter chart
 * item, there is no "card chrome painted on top of the chart image" here at
 * all: the rounded-rect/header/badge/resize-handle chrome legacy drew
 * itself is now just this ordinary React/CSS card wrapping a plain <img> -
 * matplotlib already renders the bars/lines/pie/etc into that image.
 *
 * Sizing works exactly like GroupNodeView.tsx's R6.1 frame/container: width/
 * height are set on the FLOW NODE OBJECT itself (SceneCanvas.tsx's
 * toFlowNodes), the documented xyflow mechanism for <NodeResizer/> to drive
 * the node wrapper's own size directly - this component's root div fills
 * that wrapper (100%/100%) rather than carrying its own pixel width/height.
 * Unlike a frame/container though, chartWidth/chartHeight ALSO ride inside
 * `data` (part of the 9-field wire contract every other chart field rides
 * through) - the flow-node-level width/height exists purely for the
 * NodeResizer controlled-mode technique, not as the single source of truth.
 *
 * Resize -> re-render: every resize needs a REAL matplotlib re-layout at the
 * settled size (see backend/canvas.py's resize_chart), not a cheap CSS
 * stretch of the existing PNG - so onResizeEnd is debounced
 * (CHART_RESIZE_DEBOUNCE_MS, see makeDebouncedChartResize below) before
 * firing resizeChart, guarding against a user chaining several quick resize
 * gestures in a row each hitting the network. Aspect-lock uses xyflow's own
 * NodeResizer `keepAspectRatio` prop rather than any hand-rolled ratio math -
 * the backend's own resize_chart still re-derives/clamps against MIN/MAX
 * server-side regardless of what the client sends (this component's MIN/MAX
 * are just a UI-side floor/ceiling matching legacy's own bounds, not the
 * authoritative clamp - same posture as GROUP_RESIZE_MIN_WIDTH/HEIGHT).
 *
 * chartError never hides or blocks the card (the backend's own
 * never-hard-fail contract guarantees SOME renderable PNG always exists,
 * even a placeholder one) - it only adds a small inline warning badge
 * carrying the error text in its title, next to the type badge.
 *
 * The asset URL reuses the exact `/api/assets/{assetId}` convention
 * ImageNodeView.tsx established, with a `?v=chartAssetVersion` cache-buster
 * appended so a resize's freshly re-rendered bytes (same assetId, new bytes -
 * see backend/canvas.py's resize_chart) actually reload instead of showing a
 * browser-cached stale image. Export hits a DISTINCT endpoint (a real
 * 3x-resolution re-render, not the cached display asset) - opened directly
 * in a new tab, the simpler of the two download patterns this codebase
 * already uses (ImageNodeView.tsx's fetch->blob->object-URL dance is only
 * needed there because that same code path also backs Copy Image; chart
 * export has no such second consumer, so a plain link is enough).
 */

export interface ChartNodeData extends Record<string, unknown> {
  chartType: string;
  chartData: ChartDataRow;
  chartError: string;
  chartAssetId: string;
  chartAssetVersion: number;
  chartWidth: number;
  chartHeight: number;
  chartAspectLocked: boolean;
  chartSourceNodeId: string;
  onToggleAspectLock: () => void;
  onResize: (width: number, height: number) => void;
}

export type ChartFlowNode = Node<ChartNodeData, "chart">;

/** Same one-place-builds-the-URL discipline as ImageNodeView.tsx's own
 * assetUrl() - the <img> render below is the only call site, so nothing else
 * can disagree with it about the endpoint shape. The `v` query param is
 * chartAssetVersion, not something this endpoint itself enforces - it exists
 * purely so the BROWSER treats a re-rendered chart (same assetId, new bytes)
 * as a different URL and actually refetches instead of serving a cached
 * response. */
export function chartAssetUrl(chartAssetId: string, version: number): string {
  // ADR-004 stage 4.1: the capability token appends AFTER the `v` param
  // (withAuthToken picks "&" because a query string already exists), same
  // browser-image-loader-cannot-send-headers reason as ImageNodeView's own
  // assetUrl. A no-op when no token is present.
  return withAuthToken(`/api/assets/${chartAssetId}?v=${version}`);
}

/** The dedicated export endpoint (a real 3x-resolution re-render - see
 * graphlink_chart_rendering.py's dpi_scale - never the cached display
 * asset), matching this increment's contract exactly. `session=default`
 * mirrors this app's single-session-per-window assumption everywhere else
 * (see lib/ws/transport.ts's own defaultWsUrl default parameter). */
export function chartExportUrl(nodeId: string): string {
  // ADR-004 stage 4.1: this one is a plain <a href> the user clicks, so it
  // has no way to carry a header either - same query-param treatment.
  return withAuthToken(`/api/assets/chart/${nodeId}/export?session=default`);
}

function chartTypeBadgeLabel(chartType: string): string {
  return chartType ? chartType.charAt(0).toUpperCase() + chartType.slice(1) : "Chart";
}

/** The debounce wrapper described in this file's own module doc, exported
 * standalone for direct unit testing without mounting <NodeResizer/> at all -
 * same posture as SceneCanvas.tsx's scaleDragPosition/applyGroupDragDelta.
 * `timerRef` is the caller's own mutable box (a component instance's
 * useRef), so this stays a plain function rather than owning any React state
 * itself; calling the returned function again before `debounceMs` elapses
 * cancels the pending call and restarts the wait, so only the LAST
 * width/height pair from a burst of calls ever reaches `onResize`. */
export function makeDebouncedChartResize(
  timerRef: { current: ReturnType<typeof setTimeout> | null },
  onResize: (width: number, height: number) => void,
  debounceMs: number = CHART_RESIZE_DEBOUNCE_MS,
): (width: number, height: number) => void {
  return (width, height) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      onResize(width, height);
    }, debounceMs);
  };
}

export function ChartNodeView({ id, data, selected }: NodeProps<ChartFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const collapsed = zoom < LOD_ZOOM_THRESHOLD;
  const [imageFailed, setImageFailed] = useState(false);
  const resizeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
    },
    [],
  );

  const rawTitle = data.chartData.title;
  const title = typeof rawTitle === "string" && rawTitle.trim() ? rawTitle : "Chart";

  return (
    <div
      className={`scene-node chart-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      style={{ width: "100%", height: "100%" }}
    >
      <NodeResizer
        nodeId={id}
        isVisible={selected && !collapsed}
        minWidth={CHART_MIN_WIDTH}
        minHeight={CHART_MIN_HEIGHT}
        maxWidth={CHART_MAX_WIDTH}
        maxHeight={CHART_MAX_HEIGHT}
        keepAspectRatio={data.chartAspectLocked}
        onResizeEnd={(_event, params) => {
          makeDebouncedChartResize(resizeTimerRef, data.onResize)(params.width, params.height);
        }}
      />
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title chart-node-title">
        <span className="chart-node-name">{title}</span>
        <span className="chart-node-badge">{chartTypeBadgeLabel(data.chartType)}</span>
        {data.chartError && (
          <span className="chart-node-error-badge" title={data.chartError} aria-label="Chart generation warning">
            ⚠
          </span>
        )}
      </div>
      {!collapsed && (
        <div className="scene-node-body chart-node-content">
          {imageFailed ? (
            <div className="chart-node-placeholder">Chart unavailable</div>
          ) : (
            <img
              className="chart-node-img"
              src={chartAssetUrl(data.chartAssetId, data.chartAssetVersion)}
              alt={title}
              onError={() => setImageFailed(true)}
            />
          )}
          <div className="chart-node-toolbar nodrag">
            <button
              type="button"
              className="chart-node-btn"
              onClick={data.onToggleAspectLock}
              aria-pressed={data.chartAspectLocked}
            >
              {data.chartAspectLocked ? "Unlock Aspect" : "Lock Aspect"}
            </button>
            <a
              className="chart-node-btn chart-node-export-link"
              href={chartExportUrl(id)}
              target="_blank"
              rel="noreferrer"
            >
              Export
            </a>
          </div>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
    </div>
  );
}
