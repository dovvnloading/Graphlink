/**
 * ADR-013 stage 13.2: the interactive client-side chart renderer's entry
 * point - the module ChartNodeView.tsx React.lazy()-imports, replacing the
 * static backend-rendered <img>. Owns the plumbing every chart type shares
 * (measuring, theme, zoom/pan, the hover tooltip) and dispatches to the
 * per-type marks component for the actual drawing.
 *
 * Re-validates chartData through canonicalizeChartSpec (this stage's own
 * chartSpec.ts, from stage 13.1) before rendering anything - the wire
 * already carries backend-canonicalized data, but this is the one place a
 * hand-authored or as-yet-unvalidated spec (stage 13.1's own exit
 * criterion) gets checked before it reaches SVG geometry math that assumes
 * a valid shape. On failure this renders the SAME kind of inline
 * placeholder graphlink_chart_rendering.py's own never-blank contract
 * guarantees for a bad render server-side, never a hard crash.
 */

import { useEffect, useMemo, useState } from "react";
import { IconButton } from "../../ui/IconButton";
import { ToggleButton } from "../../ui/ToggleButton";
import { supportsChartAnalysis } from "./chartViewport";
import { ChartAnalysisPane } from "./ChartAnalysisPane";
import type { ChartDataRow } from "../../../lib/bridge-core/generated/scene-state";
import { canonicalizeChartSpec, canonicalizeChartAnalysis, ChartSpecError, usesScientificMarks, type ChartAnalysis } from "../chartSpec";
import { useChartTheme, useElementSize } from "./chartHooks";
import { useZoomPane } from "./useZoomPane";
import { ChartTooltip, type TooltipContent } from "./ChartTooltip";
import { BarChartMarks } from "./BarChartMarks";
import { LineChartMarks } from "./LineChartMarks";
import { ScientificChartMarks } from "./ScientificChartMarks";
import { BoxplotChartMarks } from "./BoxplotChartMarks";
import { HeatmapChartMarks } from "./HeatmapChartMarks";
import { PieChartMarks } from "./PieChartMarks";
import { HistogramChartMarks } from "./HistogramChartMarks";
import { SankeyChartMarks } from "./SankeyChartMarks";

export interface ChartRendererProps {
  chartType: string;
  chartData: ChartDataRow;
  onAnalysisChange?: (analysis: ChartAnalysis) => void | Promise<void>;
}

function ChartRenderer({ chartType, chartData, onAnalysisChange }: ChartRendererProps) {
  const [wrapperRef, size] = useElementSize<HTMLDivElement>();
  const theme = useChartTheme(wrapperRef);
  const [interactive, setInteractive] = useState(false);
  const { zoom, isZoomed, handlers, reset } = useZoomPane(interactive, () => setInteractive(true));
  const plotHeight = Math.max(1, size.height - 32);
  useEffect(() => {
    if (!interactive) return;
    const outside = (event: globalThis.PointerEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) setInteractive(false);
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setInteractive(false);
      if (wrapperRef.current?.contains(document.activeElement)) (document.activeElement as HTMLElement)?.blur();
      event.stopPropagation();
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", escape, true);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", escape, true);
    };
  }, [interactive, wrapperRef]);
  const [hover, setHover] = useState<TooltipContent | null>(null);
  const [heatmapPending, setHeatmapPending] = useState<{ source: ChartDataRow; value: ChartAnalysis } | null>(null);
  const [heatmapError, setHeatmapError] = useState("");

  const canonical = useMemo(() => {
    try {
      return { data: canonicalizeChartSpec(chartData, chartType), error: null as string | null };
    } catch (error) {
      return { data: null, error: error instanceof ChartSpecError ? error.message : "Chart data is invalid" };
    }
  }, [chartData, chartType]);

  if (canonical.error || !canonical.data) {
    return (
      <div className="chart-canvas-wrapper chart-canvas-placeholder" ref={wrapperRef}>
        <span>{canonical.error ?? "Chart unavailable"}</span>
      </div>
    );
  }

  const heatmapAnalysis = canonical.data.analysis;
  const heatmapData = heatmapPending?.source === canonical.data
    ? { ...canonical.data, analysis: heatmapPending.value } : canonical.data;
  const heatmapClustering = heatmapData.analysis?.clustering;
  function changeHeatmapMode(next: "rows" | "columns" | "both" | undefined) {
    if (!canonical.data) return;
    try {
      const analysis = { lockX: false, lockY: false, annotations: [] as [], ...(next ? { clustering: next } : {}) };
      const normalized = canonicalizeChartAnalysis(analysis, "heatmap", "linear", "linear", canonical.data);
      setHeatmapError("");
      setHeatmapPending({ source: canonical.data, value: normalized });
      Promise.resolve(onAnalysisChange?.(normalized)).catch((error: unknown) => {
        setHeatmapPending(null);
        setHeatmapError(error instanceof Error ? error.message : "Could not save heatmap clustering");
      });
    } catch (error) {
      setHeatmapError(error instanceof Error ? error.message : "Invalid clustering mode");
    }
  }

  // Domain-based interactions for numerical analysis, preserving the default
  // marks and non-analysis chart zoom implementations without SVG scaling.
  if (supportsChartAnalysis(chartType)) {
    return (
      <div className="chart-canvas-wrapper" ref={wrapperRef} data-interactive={interactive}
        onPointerDownCapture={() => setInteractive(true)} onFocusCapture={() => setInteractive(true)}>
        {size.width > 0 && size.height > 0 && (
          <ChartAnalysisPane
            chartType={chartType} chartData={canonical.data}
            width={size.width} height={size.height} theme={theme}
            wrapperRef={wrapperRef} onAnalysisChange={onAnalysisChange}
            interactive={interactive} onActivate={() => setInteractive(true)}
          />
        )}
      </div>
    );
  }

  const marksProps = {
    chartData: chartType === "heatmap" ? heatmapData : canonical.data,
    theme,
    width: size.width,
    height: plotHeight,
    onHover: setHover,
    wrapperRef,
  };

  return (
    <div className="chart-canvas-wrapper" ref={wrapperRef} data-interactive={interactive}
      onPointerDownCapture={() => setInteractive(true)} onFocusCapture={() => setInteractive(true)}>
      <div className="chart-legacy-toolbar nodrag">
        <IconButton icon="fit-view" label="Reset zoom" shortcut="Home" size="sm" disabled={!isZoomed} onClick={reset} />
        {chartType === "heatmap" && <>
          <ToggleButton size="sm" label="Cluster rows" pressed={heatmapClustering === "rows" || heatmapClustering === "both"}
            onPressedChange={(pressed) => changeHeatmapMode(pressed
              ? heatmapClustering === "columns" ? "both" : "rows"
              : heatmapClustering === "both" ? "columns" : undefined)} />
          <ToggleButton size="sm" label="Cluster columns" pressed={heatmapClustering === "columns" || heatmapClustering === "both"}
            onPressedChange={(pressed) => changeHeatmapMode(pressed
              ? heatmapClustering === "rows" ? "both" : "columns"
              : heatmapClustering === "both" ? "rows" : undefined)} />
          {heatmapError && <span role="alert">{heatmapError}</span>}
        </>}
      </div>
      {size.width > 0 && size.height > 0 && (
        <svg
          className={`chart-canvas-svg nodrag nopan${interactive ? " nowheel" : ""}`}
          tabIndex={0} role="application" aria-label={`Interactive ${chartType} chart`}
          width={size.width}
          height={plotHeight}
          onPointerDown={handlers.onPointerDown}
          onPointerMove={handlers.onPointerMove}
          onPointerUp={handlers.onPointerUp}
          onPointerCancel={handlers.onPointerCancel}
          onWheel={handlers.onWheel}
          style={{ cursor: zoom.scale > 1 ? "grab" : "default" }}
        >
          <g transform={`translate(${zoom.x} ${zoom.y}) scale(${zoom.scale})`}>
            {chartType === "bar" && <BarChartMarks {...marksProps} />}
            {chartType === "line" && (usesScientificMarks(canonical.data, chartType)
              ? <ScientificChartMarks {...marksProps} chartType={chartType} />
              : <LineChartMarks {...marksProps} />)}
            {chartType === "scatter" && <ScientificChartMarks {...marksProps} chartType={chartType} />}
            {chartType === "pie" && <PieChartMarks {...marksProps} />}
            {chartType === "histogram" && <HistogramChartMarks {...marksProps} />}
            {chartType === "sankey" && <SankeyChartMarks {...marksProps} />}
            {chartType === "boxplot" && <BoxplotChartMarks {...marksProps} />}
            {chartType === "heatmap" && <HeatmapChartMarks {...marksProps} />}
          </g>
        </svg>
      )}
      <ChartTooltip content={hover} theme={theme} />
    </div>
  );
}

export default ChartRenderer;
