import { NodeResizer, type Node, type NodeProps } from "@xyflow/react";
import { lazy, memo, Suspense, useEffect, useRef, useState } from "react";
import type { ChartDataRow } from "../../lib/bridge-core/generated/scene-state";
import {
  CHART_MAX_HEIGHT,
  CHART_MAX_WIDTH,
  CHART_MIN_HEIGHT,
  CHART_MIN_WIDTH,
  CHART_RESIZE_DEBOUNCE_MS,
} from "./canvasConstants";
import { NodeHeading } from "./NodeHeading";
import TableOutput, { type TableOutputBinding } from "./TableOutput";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";
import type { PublicationExportOptions, PublicationFormat, PublicationSize } from "./chartSpec";
import { downloadPublicationChartExport } from "./charts/publicationExport";

// ADR-013 stage 13.2: the interactive renderer lives in its own lazily
// loaded chunk (web_ui/src/app/canvas/charts/) - a plain SVG/React
// implementation with no new charting dependency, code-split the same way
// ChatLibraryDialog/HelpDialog/SettingsDialog already are. Defined once at
// module scope (not inside the component) so every chart node on the canvas
// shares the same lazy promise instead of each mounting its own chunk load.
const LazyChartOverview = lazy(() => import("./charts/ChartOverview"));
const LazyChartRenderer = lazy(() => import("./charts/ChartRenderer"));

/**
 * The chart node (Qt-removal plan R6.2, re-rendered client-side as of
 * ADR-013 stage 13.2) - graphlink_canvas_chart_item.py's React successor: a
 * card wrapping an interactive SVG chart (bar/line/pie/histogram/sankey/scatter/boxplot/heatmap)
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
 * The chart node's publication export opens an authenticated scene intent
 * for physically sized SVG/PDF/PNG and saves a local Blob. The old HTTP
 * chart exporter remains available in charts/legacyExport.ts for callers
 * needing legacy fixed-size PNG/SVG. Interactive SVG remains client-side.
 */

export interface ChartNodeData extends Record<string, unknown> {
  chartType: string;
  tableOutput?: TableOutputBinding;
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

function chartTypeBadgeLabel(chartType: string): string {
  if (chartType === "boxplot") return "Box plot";
  if (chartType === "heatmap") return "Heatmap";
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
    a.tableOutput === b.tableOutput &&
    a.chartWidth === b.chartWidth &&
    a.chartHeight === b.chartHeight &&
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
  const overview = useLodVisibility();
  const resizeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [publicationOpen, setPublicationOpen] = useState(false);
  const [publicationFormat, setPublicationFormat] = useState<PublicationFormat>("svg");
  const [publicationSize, setPublicationSize] = useState<PublicationSize>("single");
  const [publicationBusy, setPublicationBusy] = useState(false);
  const [publicationError, setPublicationError] = useState("");

  async function exportPublication(form: HTMLFormElement) {
    if (publicationBusy) return;
    const fields = new FormData(form);
    const settings: PublicationExportOptions = {
      format: publicationFormat, size: publicationSize,
      ...(publicationSize === "custom" ? {
        widthMm: Number(fields.get("widthMm")),
        heightMm: Number(fields.get("heightMm")),
      } : {}),
      dpi: Number(fields.get("dpi")) as 300 | 600,
      colorblindSafe: fields.has("colorblindSafe"),
      grayscaleSafe: fields.has("grayscaleSafe"),
    };
    setPublicationBusy(true);
    setPublicationError("");
    try {
      await downloadPublicationChartExport(id, settings, data.chartHeight / data.chartWidth);
    } catch (error) {
      setPublicationError(error instanceof Error ? error.message : "Publication export failed");
    } finally {
      setPublicationBusy(false);
    }
  }

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
      tablePort={data.tableOutput?.node.tableInputSourceId ? "target" : undefined}
      footer={!overview && data.tableOutput && <TableOutput {...data.tableOutput} />}
      kindClassName="chart-node"
      selected={!!selected}
      collapsed={false}
      style={{ width: "100%", height: "100%" }}
      resizer={
        <NodeResizer
          nodeId={id}
          isVisible={selected && !overview}
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
        <NodeHeading glyph="chart" className="chart-node-title">
          <span className="chart-node-name">{title}</span>
          <span className="chart-node-badge">{chartTypeBadgeLabel(data.chartType)}</span>
          {data.chartError && (
            <span className="chart-node-error-badge" title={data.chartError} aria-label="Chart generation warning">
              ⚠
            </span>
          )}
        </NodeHeading>
      }
      bodyClassName="chart-node-content"
    >
      <Suspense fallback={<div className="chart-node-placeholder">Loading chart…</div>}>
        {overview
          ? <LazyChartOverview chartType={data.chartType} chartData={data.chartData} />
          : <LazyChartRenderer chartType={data.chartType} chartData={data.chartData} />}
      </Suspense>
      {!overview && <div className="chart-node-toolbar nodrag" style={{ flexWrap: "wrap" }}>
        <button
          type="button"
          className="chart-node-btn"
          onClick={data.onToggleAspectLock}
          aria-pressed={data.chartAspectLocked}
        >
          {data.chartAspectLocked ? "Unlock Aspect" : "Lock Aspect"}
        </button>
        {/* The unified export control replaces duplicate format shortcuts.
            It keeps download credentials on the authenticated connection. */}
        <button
          type="button"
          className="chart-node-btn"
          aria-expanded={publicationOpen}
          aria-controls={`chart-publication-${id}`}
          onClick={() => setPublicationOpen(!publicationOpen)}
        >
          Publication export…
        </button>
        {publicationOpen && (
          <form
            id={`chart-publication-${id}`} role="group" aria-label="Publication export settings"
            className="chart-node-publication-options nodrag nowheel"
            style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", padding: 8,
                     flexBasis: "100%", width: "100%", maxHeight: 190, overflowY: "auto" }}
            onSubmit={(event) => { event.preventDefault(); void exportPublication(event.currentTarget); }}
          >
            <label>Format{" "}
              <select aria-label="Publication format" value={publicationFormat}
                onChange={(event) => setPublicationFormat(event.target.value as PublicationFormat)}>
                <option value="svg">SVG (vector)</option>
                <option value="pdf">PDF (vector)</option>
                <option value="png">PNG</option>
              </select>
            </label>
            <label>Size{" "}
              <select aria-label="Publication size" value={publicationSize}
                onChange={(event) => setPublicationSize(event.target.value as PublicationSize)}>
                <option value="single">Single column (85 mm)</option>
                <option value="double">Double column (180 mm)</option>
                <option value="custom">Custom (mm)</option>
              </select>
            </label>
            {publicationSize === "custom" && (
              <>
                <label>Width (mm){" "}
                  <input aria-label="Custom width in millimeters" name="widthMm" type="number"
                    min={35} max={300} step={1} defaultValue={120} style={{ width: 68 }} />
                </label>
                <label>Height (mm){" "}
                  <input aria-label="Custom height in millimeters" name="heightMm" type="number"
                    min={30} max={300} step={1} defaultValue={80} style={{ width: 68 }} />
                </label>
              </>
            )}
            <label>DPI{" "}
              <select aria-label="Publication DPI" name="dpi" defaultValue="300">
                <option value="300">300</option>
                <option value="600">600</option>
              </select>
            </label>
            <label>
              <input type="checkbox" name="colorblindSafe" />Colorblind-safe palette
            </label>
            <label>
              <input type="checkbox" name="grayscaleSafe" />Grayscale-safe hatches, markers and dashes
            </label>
            <button type="submit" className="chart-node-btn" disabled={publicationBusy}>
              {publicationBusy ? "Exporting…" : `Download ${publicationFormat.toUpperCase()}`}
            </button>
            {publicationError && <span role="alert">{publicationError}</span>}
          </form>
        )}
      </div>}
    </NodeShell>
  );
}, chartNodePropsAreEqual);
