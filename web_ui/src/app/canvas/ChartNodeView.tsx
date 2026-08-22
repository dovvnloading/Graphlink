import { NodeResizer, type Node, type NodeProps } from "@xyflow/react";
import { lazy, memo, Suspense, useEffect, useRef } from "react";
import type { ChartDataRow } from "../../lib/bridge-core/generated/scene-state";
import { authHeaders } from "../../lib/auth/token";
import {
  CHART_MAX_HEIGHT,
  CHART_MAX_WIDTH,
  CHART_MIN_HEIGHT,
  CHART_MIN_WIDTH,
  CHART_RESIZE_DEBOUNCE_MS,
} from "./canvasConstants";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

// ADR-013 stage 13.2: the interactive renderer lives in its own lazily
// loaded chunk (web_ui/src/app/canvas/charts/) - a plain SVG/React
// implementation with no new charting dependency, code-split the same way
// ChatLibraryDialog/HelpDialog/SettingsDialog already are. Defined once at
// module scope (not inside the component) so every chart node on the canvas
// shares the same lazy promise instead of each mounting its own chunk load.
const LazyChartRenderer = lazy(() => import("./charts/ChartRenderer"));

/**
 * The chart node (Qt-removal plan R6.2, re-rendered client-side as of
 * ADR-013 stage 13.2) - graphlink_canvas_chart_item.py's React successor: a
 * card wrapping an interactive SVG chart (bar/line/pie/histogram/sankey)
 * drawn directly from `chartData` in the browser. Unlike the legacy
 * QPainter chart item, there is no "card chrome painted on top of the chart
 * image" here at all: the rounded-rect/header/badge/resize-handle chrome
 * legacy drew itself is now just this ordinary React/CSS card; the chart
 * ITSELF used to be a backend-rendered PNG (R6.2's original cut) and is now
 * charts/ChartRenderer.tsx's own SVG, re-validated client-side via
 * chartSpec.ts (stage 13.1) rather than trusted blindly off the wire.
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
 * ChartRenderer itself doesn't read either one - it measures its own
 * rendered box directly (see charts/chartHooks.ts's useElementSize).
 *
 * Resize -> re-render: the interactive renderer re-lays-out INSTANTLY at
 * any size (no network round-trip - stage 13.2's whole point); onResizeEnd
 * is still debounced (CHART_RESIZE_DEBOUNCE_MS, see makeDebouncedChartResize
 * below) before firing resizeChart purely to avoid hitting the network on
 * every intermediate frame of a drag - ADR-013 stage 13.4 retired the
 * backend's own display-PNG re-render entirely (nothing has read it since
 * this renderer shipped), so resizeChart today persists only the settled
 * chartWidth/chartHeight. Aspect-lock uses xyflow's own NodeResizer
 * `keepAspectRatio` prop rather than any hand-rolled ratio math - the
 * backend's own resize_chart still re-derives/clamps against MIN/MAX
 * server-side regardless of what the client sends (this component's MIN/MAX
 * are just a UI-side floor/ceiling matching legacy's own bounds, not the
 * authoritative clamp - same posture as GROUP_RESIZE_MIN_WIDTH/HEIGHT).
 *
 * chartError never hides or blocks the card (the backend's own
 * never-hard-fail contract guarantees a renderable chart always exists) -
 * it only adds a small inline warning badge carrying the error text in its
 * title, next to the type badge. A chartData shape ChartRenderer itself
 * can't render (fails its own canonicalizeChartSpec re-check) shows its own
 * inline placeholder instead of crashing - see ChartRenderer.tsx.
 *
 * Export/PNG and Export/SVG both hit the same dedicated export endpoint (a
 * real matplotlib re-render server-side - PNG at 3x resolution, SVG as
 * vector - not anything this renderer draws), fetched with the capability
 * token in a header and saved from a blob. They are buttons rather than
 * new-tab links precisely because of that token - see chartExportUrl.
 * That stays server-side deliberately: a downloadable, print-quality
 * raster/vector asset is a different job than "draw this interactively."
 * ADR-013 stage 13.4 moved that render off the event loop
 * (asyncio.to_thread, backend/assets.py) and added the SVG option.
 */

export interface ChartNodeData extends Record<string, unknown> {
  chartType: string;
  chartData: ChartDataRow;
  chartError: string;
  chartWidth: number;
  chartHeight: number;
  chartAspectLocked: boolean;
  chartSourceNodeId: string;
  onToggleAspectLock: () => void;
  onResize: (width: number, height: number) => void;
}

export type ChartFlowNode = Node<ChartNodeData, "chart">;

/** The dedicated export endpoint - a real matplotlib re-render server-side
 * (PNG at 3x resolution via graphlink_chart_rendering.py's dpi_scale, or
 * SVG as vector - see render_chart_png/render_chart_svg), never anything
 * cached. `session=default` mirrors this app's single-session-per-window
 * assumption everywhere else (see lib/ws/transport.ts's own defaultWsUrl
 * default parameter). */
export function chartExportUrl(nodeId: string, format: "png" | "svg" = "png"): string {
  // Deliberately NO token in this URL. It used to be withAuthToken(...) and
  // to be used as an <a href target="_blank">, which leaked the live
  // capability token off the machine: in the pywebview/WebView2 shell a
  // target=_blank click raises NewWindowRequested, whose default handler
  // opens the fully-resolved URL in the OS default browser - so the token
  // landed in that browser's address bar, history and download manager, and
  // was copyable straight out of the link's context menu. A synced browser
  // history uploads it. That token authorizes every /api intent, including
  // the code-execution approval gate, so this was the one place the "the
  // token never leaves this window" property (see lib/auth/token.ts) broke.
  //
  // The fetch below sends it as an Authorization HEADER instead, and the
  // bytes are saved from a blob - nothing user-visible carries the secret.
  return `/api/assets/chart/${nodeId}/export?session=default&fmt=${format}`;
}

/** Fetches a chart export with the capability token in a header and saves the
 * resulting bytes as a file. Mirrors downloadTextFile.ts's blob -> object URL
 * -> temporary anchor pattern (and ImageNodeView's own image export before
 * it); the only difference is that the bytes come from an authenticated
 * request rather than from memory. */
export async function downloadChartExport(nodeId: string, format: "png" | "svg"): Promise<void> {
  const response = await fetch(chartExportUrl(nodeId, format), { headers: authHeaders() });
  if (!response.ok) throw new Error(`Chart export failed (${response.status})`);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  // The server names the file from the chart's own title
  // (backend/assets.py's _sanitize_chart_filename) and sends it in
  // Content-Disposition - the same header the old navigated link relied on,
  // so honoring it here keeps the downloaded filename identical.
  anchor.download = filenameFromContentDisposition(response.headers.get("content-disposition"))
    || `chart.${format}`;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}

/** Pulls `filename="..."` out of a Content-Disposition header. Exported for
 * direct unit testing. Returns "" when the header is absent or unparseable,
 * letting the caller fall back to its own default. */
export function filenameFromContentDisposition(header: string | null): string {
  if (!header) return "";
  const match = /filename\s*=\s*"([^"]+)"/i.exec(header) ?? /filename\s*=\s*([^;]+)/i.exec(header);
  return match ? match[1].trim() : "";
}

function chartTypeBadgeLabel(chartType: string): string {
  return chartType ? chartType.charAt(0).toUpperCase() + chartType.slice(1) : "Chart";
}

/** The debounce wrapper described in this file's own module doc, exported
 * standalone for direct unit testing without mounting <NodeResizer/> at all -
 * same posture as SceneCanvas.tsx's applyGroupDragDelta.
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

/** ADR-011 stage 11.1 comparator, widened under ADR-013 stage 13.2: id (read
 * into the resizer's nodeId and chartExportUrl), selected, and only the
 * ChartNodeData fields this component actually reads. chartWidth/
 * chartHeight/chartSourceNodeId ride on the wire (see this file's own
 * module doc) but this component never reads them directly - the node's
 * own width/height comes through the flow node object itself, not `data` -
 * so they're deliberately excluded rather than compared for no reason.
 * chartAssetId/chartAssetVersion (the backend-rendered display PNG's own
 * key/version) rode ChartNodeData until ADR-013 stage 13.4 retired them
 * outright - the interactive renderer draws from `chartData` alone, so
 * nothing had read them since stage 13.2 shipped. chartData IS compared by
 * reference rather than scoped to
 * `.title` - the renderer reads the whole shape, and SceneCanvas.tsx's own
 * toFlowNodes cache (keyed by the raw scene-node object) guarantees a fresh
 * chartData reference only appears when the underlying node actually
 * changed, so a plain `===` here is both correct and cheap (no risk of
 * forcing a re-render off a value-identical-but-freshly-rebuilt object). */
export function chartNodePropsAreEqual(prev: NodeProps<ChartFlowNode>, next: NodeProps<ChartFlowNode>): boolean {
  if (prev.id !== next.id || prev.selected !== next.selected) return false;
  const a = prev.data;
  const b = next.data;
  return (
    a.chartType === b.chartType &&
    a.chartError === b.chartError &&
    a.chartAspectLocked === b.chartAspectLocked &&
    a.onToggleAspectLock === b.onToggleAspectLock &&
    a.onResize === b.onResize &&
    a.chartData === b.chartData
  );
}

export const ChartNodeView = memo(function ChartNodeView({
  id,
  data,
  selected,
}: NodeProps<ChartFlowNode>) {
  const collapsed = useLodVisibility();
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
    <NodeShell
      kindClassName="chart-node"
      selected={!!selected}
      collapsed={collapsed}
      style={{ width: "100%", height: "100%" }}
      resizer={
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
      }
      header={
        <div className="scene-node-title chart-node-title">
          <span className="chart-node-name">{title}</span>
          <span className="chart-node-badge">{chartTypeBadgeLabel(data.chartType)}</span>
          {data.chartError && (
            <span className="chart-node-error-badge" title={data.chartError} aria-label="Chart generation warning">
              ⚠
            </span>
          )}
        </div>
      }
      bodyClassName="chart-node-content"
    >
      <Suspense fallback={<div className="chart-node-placeholder">Loading chart…</div>}>
        <LazyChartRenderer chartType={data.chartType} chartData={data.chartData} />
      </Suspense>
      <div className="chart-node-toolbar nodrag">
        <button
          type="button"
          className="chart-node-btn"
          onClick={data.onToggleAspectLock}
          aria-pressed={data.chartAspectLocked}
        >
          {data.chartAspectLocked ? "Unlock Aspect" : "Lock Aspect"}
        </button>
        {/* Buttons, not <a target="_blank"> links: the export request has to
            carry the capability token, and a navigated link would hand it to
            the OS default browser - see chartExportUrl's own comment. */}
        <button
          type="button"
          className="chart-node-btn chart-node-export-link"
          onClick={() => { void downloadChartExport(id, "png"); }}
        >
          Export PNG
        </button>
        <button
          type="button"
          className="chart-node-btn chart-node-export-link"
          onClick={() => { void downloadChartExport(id, "svg"); }}
        >
          Export SVG
        </button>
      </div>
    </NodeShell>
  );
}, chartNodePropsAreEqual);
