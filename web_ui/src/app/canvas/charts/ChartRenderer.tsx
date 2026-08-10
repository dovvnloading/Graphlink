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

import { useMemo, useState } from "react";
import type { ChartDataRow } from "../../../lib/bridge-core/generated/scene-state";
import { canonicalizeChartSpec, ChartSpecError } from "../chartSpec";
import { useChartTheme, useElementSize } from "./chartHooks";
import { useZoomPane } from "./useZoomPane";
import { ChartTooltip, type TooltipContent } from "./ChartTooltip";
import { BarChartMarks } from "./BarChartMarks";
import { LineChartMarks } from "./LineChartMarks";
import { PieChartMarks } from "./PieChartMarks";
import { HistogramChartMarks } from "./HistogramChartMarks";
import { SankeyChartMarks } from "./SankeyChartMarks";

export interface ChartRendererProps {
  chartType: string;
  chartData: ChartDataRow;
}

function ChartRenderer({ chartType, chartData }: ChartRendererProps) {
  const [wrapperRef, size] = useElementSize<HTMLDivElement>();
  const theme = useChartTheme(wrapperRef);
  const { zoom, isZoomed, handlers, reset } = useZoomPane();
  const [hover, setHover] = useState<TooltipContent | null>(null);

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

  const marksProps = {
    chartData: canonical.data,
    theme,
    width: size.width,
    height: size.height,
    onHover: setHover,
    wrapperRef,
  };

  return (
    <div className="chart-canvas-wrapper" ref={wrapperRef}>
      {size.width > 0 && size.height > 0 && (
        <svg
          className="chart-canvas-svg nodrag nowheel nopan"
          width={size.width}
          height={size.height}
          onPointerDown={handlers.onPointerDown}
          onPointerMove={handlers.onPointerMove}
          onPointerUp={handlers.onPointerUp}
          onPointerCancel={handlers.onPointerCancel}
          onWheel={handlers.onWheel}
          style={{ cursor: zoom.scale > 1 ? "grab" : "default" }}
        >
          <g transform={`translate(${zoom.x} ${zoom.y}) scale(${zoom.scale})`}>
            {chartType === "bar" && <BarChartMarks {...marksProps} />}
            {chartType === "line" && <LineChartMarks {...marksProps} />}
            {chartType === "pie" && <PieChartMarks {...marksProps} />}
            {chartType === "histogram" && <HistogramChartMarks {...marksProps} />}
            {chartType === "sankey" && <SankeyChartMarks {...marksProps} />}
          </g>
        </svg>
      )}
      {isZoomed && (
        <button type="button" className="chart-zoom-reset nodrag" onClick={reset}>
          Reset zoom
        </button>
      )}
      <ChartTooltip content={hover} theme={theme} />
    </div>
  );
}

export default ChartRenderer;
