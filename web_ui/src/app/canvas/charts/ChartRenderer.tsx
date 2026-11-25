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
import type { ChartDataRow, ChartCompositeSpecRow } from "../../../lib/bridge-core/generated/scene-state";
import { canonicalizeChartSpec, canonicalizeChartAnalysis, ChartSpecError, usesScientificMarks, type ChartAnalysis } from "../chartSpec";
import { useChartTheme, useElementSize } from "./chartHooks";
import { useZoomPane } from "./useZoomPane";
import { ChartTooltip, type TooltipContent } from "./ChartTooltip";
import { BarChartMarks } from "./BarChartMarks";
import { LineChartMarks } from "./LineChartMarks";
import { ScientificChartMarks } from "./ScientificChartMarks";
import { BoxplotChartMarks } from "./BoxplotChartMarks";
import { HeatmapChartMarks } from "./HeatmapChartMarks";
import { CompositeChartMarks } from "./CompositeChartMarks";
import { CompositeChartEditor } from "./CompositeChartEditor";
import type { CompositeDesign } from "./compositeDesign";
import { Button } from "../../ui/Button";
import { PieChartMarks } from "./PieChartMarks";
import { HistogramChartMarks } from "./HistogramChartMarks";
import { SankeyChartMarks } from "./SankeyChartMarks";

export interface ChartRendererProps {
  chartType: string;
  chartData: ChartDataRow;
  onAnalysisChange?: (analysis: ChartAnalysis) => void | Promise<void>;
  onCompositeDesignChange?: (design: CompositeDesign, expected: CompositeDesign) => Promise<void>;
  onBranchSelection?: (indices: number[], expected: ChartCompositeSpecRow) => Promise<void>;
}

function ChartRenderer({ chartType, chartData, onAnalysisChange, onCompositeDesignChange, onBranchSelection }: ChartRendererProps) {
  const [wrapperRef, size] = useElementSize<HTMLDivElement>();
  const theme = useChartTheme(wrapperRef);
  const [interactive, setInteractive] = useState(false);
  const [compositeEditorOpen, setCompositeEditorOpen] = useState(false);
  const [compositePreview, setCompositePreview] = useState<{ source: ChartDataRow; value: ChartDataRow } | null>(null);
  const [brushMode, setBrushMode] = useState(false);
  const [rowSelection, setRowSelection] = useState<{ source: ChartDataRow; indices: number[] } | null>(null);
  const [selectionError, setSelectionError] = useState("");
  const [creatingSubset, setCreatingSubset] = useState(false);
  const selectedIndices = rowSelection?.source === chartData ? rowSelection.indices : [];
  const fullRowCount = chartData.composite
    ? Object.values(chartData.composite.numbers)[0]?.length ?? 0 : 0;
  const canBranchSelection = selectedIndices.length >= 2 && selectedIndices.length < fullRowCount;
  const { zoom, isZoomed, handlers, reset } = useZoomPane(interactive, () => setInteractive(true));
  const plotHeight = Math.max(1, size.height
    - (chartType === "composite" && compositeEditorOpen ? 215 : 32)
    - (chartType === "composite" ? 34 : 0));
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

  const baseComposite = canonical.data;
  const compositeDisplay = compositePreview?.source === baseComposite
    ? compositePreview.value : baseComposite;
  const marksProps = {
    chartData: chartType === "heatmap" ? heatmapData
      : chartType === "composite" ? compositeDisplay : canonical.data,
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
        {chartType === "composite" && <Button size="sm" variant="ghost" onClick={() => {
          setCompositeEditorOpen(!compositeEditorOpen);
          setCompositePreview(null);
        }}>{compositeEditorOpen ? "Close layer editor" : "Edit layers…"}</Button>}
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
      {chartType === "composite" && <div className="chart-composite-selection-tools nodrag nowheel nopan">
        <ToggleButton size="sm" icon="crosshair" label="Brush source rows" pressed={brushMode}
          onPressedChange={pressed => {
            setBrushMode(pressed);
            setHover(null);
            setSelectionError("");
          }} />
        <span role="status" aria-live="polite">
          {selectedIndices.length
            ? selectedIndices.length + " of " + fullRowCount + " source rows selected"
            : "Drag in a facet to select rows (Enter selects a whole facet)"}
        </span>
        <Button size="sm" variant="ghost" disabled={!selectedIndices.length || creatingSubset}
          onClick={() => { setRowSelection(null); setSelectionError(""); }}>Clear selection</Button>
        <Button size="sm" disabled={!canBranchSelection || !onBranchSelection || creatingSubset}
          onClick={() => {
            if (!canBranchSelection || !baseComposite.composite || !onBranchSelection) return;
            setCreatingSubset(true);
            setSelectionError("");
            void Promise.resolve(onBranchSelection([...selectedIndices], baseComposite.composite)).then(() => {
              setRowSelection(null);
              setBrushMode(false);
            }).catch((reason: unknown) => {
              setSelectionError(reason instanceof Error ? reason.message : "Could not create selection chart");
            }).finally(() => setCreatingSubset(false));
          }}>{creatingSubset ? "Creating…" : "Branch selected rows"}</Button>
        {selectionError && <span role="alert">{selectionError}</span>}
      </div>}
      {chartType === "composite" && compositeEditorOpen && baseComposite.composite &&
        <CompositeChartEditor chartData={baseComposite}
          onPreview={value => setCompositePreview(value ? { source: baseComposite, value } : null)}
          onSave={(design, expected) => onCompositeDesignChange
            ? onCompositeDesignChange(design, expected)
            : Promise.reject(new Error("Composite design editing is unavailable"))}
          onClose={() => { setCompositeEditorOpen(false); setCompositePreview(null); }} />}
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
            {chartType === "composite" && <CompositeChartMarks {...marksProps}
              brushEnabled={brushMode} selectedIndices={selectedIndices}
              onSelectionChange={indices => {
                setRowSelection({ source: chartData, indices });
                setSelectionError("");
              }} />}
          </g>
        </svg>
      )}
      <ChartTooltip content={hover} theme={theme} />
    </div>
  );
}

export default ChartRenderer;
