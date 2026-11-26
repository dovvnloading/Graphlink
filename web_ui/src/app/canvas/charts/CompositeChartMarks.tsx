/** V2 composite marks: grouped columns, layered geometry and small multiples.
 * Live and overview SVG use exactly the same validated specification. */
import { useId, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { ChartMarksProps } from "./BarChartMarks";
import { CartesianAxes, type XTick } from "./CartesianAxes";
import { createLinearScale, createLog10Scale, formatLogTick, formatScientificTick,
  log10Ticks, scientificLinearTicks } from "./chartScales";
import { tooltipPositionFromEvent } from "./chartLayout";
import { compositeColorKey, compositeColorKeys, compositeDomains, compositeFacets, compositeGroups, compositeSample } from "./compositeGeometry";
import { brushPixelToData, compositeBrushBounds, compositeHighlightBudget, compositeRowsInRect,
  type CompositeBrushPoint } from "./compositeSelection";

// Eight distinct slots, mirrored by the standard composite publication palette.
// Keep other chart types on the existing theme palette.
const COMPOSITE_COLORS = ["#3f8f5c", "#3f7dc9", "#8a5fd1", "#d98a3d",
  "#cf5354", "#d9b23d", "#168b91", "#bc568e"];

export function CompositeChartMarks({ chartData, theme, width, height, onHover, wrapperRef,
  compact = false, brushEnabled = false, selectedIndices = [], onSelectionChange,
}: ChartMarksProps & {
  compact?: boolean;
  brushEnabled?: boolean;
  selectedIndices?: number[];
  onSelectionChange?: (indices: number[]) => void;
}) {
  const clipPrefix = useId();
  const [brushing, setBrushing] = useState<{
    facet: string | null; from: CompositeBrushPoint; to: CompositeBrushPoint;
  } | null>(null);
  const spec = chartData.composite;
  if (!spec || width <= 0 || height <= 0) return null;
  const facets = compositeFacets(spec);
  const keys = compositeColorKeys(spec);
  const selected = new Set(selectedIndices);
  const hasSelection = selected.size > 0;
  const colorFor = (key: string) => COMPOSITE_COLORS[keys.findIndex(entry => entry.key === key)];
  const showLegend = !compact && (spec.layers.length > 1 || spec.layers.some(layer => layer.colorBy));
  const legendColumns = width < 480 ? 2 : 4;
  const legendHeight = showLegend ? Math.ceil(keys.length / legendColumns) * 15 + 5 : 0;
  const domain = compositeDomains(chartData);
  const cols = facets.length === 1 ? 1 : 2;
  const rows = Math.ceil(facets.length / cols);
  const gap = compact ? 4 : 12;
  const panelW = Math.max(1, (width - gap * (cols - 1)) / cols);
  const panelH = Math.max(1, (height - legendHeight - gap * (rows - 1)) / rows);
  const left = compact ? 3 : 62;
  const right = compact ? 3 : 14;
  const top = compact ? 3 : facets.length === 1 ? 14 : 27;
  const bottom = compact ? 3 : 53;
  const innerW = Math.max(1, panelW - left - right);
  const innerH = Math.max(1, panelH - top - bottom);
  const scale = (axis: "x" | "y", range: [number, number]) =>
    chartData[axis === "x" ? "xScale" : "yScale"] === "log10"
      ? createLog10Scale(domain[axis], range) : createLinearScale(domain[axis], range);
  const x = scale("x", [0, innerW]);
  const y = scale("y", [innerH, 0]);
  const ticks = (axis: "x" | "y") => {
    const values = domain[axis], kind = chartData[axis === "x" ? "xScale" : "yScale"];
    return (kind === "log10" ? log10Ticks(...values, 4) : scientificLinearTicks(...values, 4))
      .filter(value => value >= values[0] && value <= values[1]);
  };
  const xTicks: XTick[] = ticks("x").map(value => ({
    position: x(value),
    lines: [chartData.xScale === "log10" ? formatLogTick(value) : formatScientificTick(value)],
  }));
  const yTicks = ticks("y");
  const groupCount = facets.reduce((sum, facet) => sum
    + spec.layers.reduce((total, layer) => total + compositeGroups(spec, layer, facet).length, 0), 0);
  const marksBudget = Math.max(2, Math.floor((compact ? 180 : 1200) / Math.max(1, groupCount)));
  const escapeName = (facet: string | null) => facet ?? "all";
  return (
    <g className={compact ? "chart-composite-overview" : "chart-composite-marks"}>
      {facets.map((facet, panel) => {
        const px = (panel % cols) * (panelW + gap);
        const py = legendHeight + Math.floor(panel / cols) * (panelH + gap);
        const clipId = clipPrefix + "-" + panel;
        const point = (event: ReactPointerEvent<SVGRectElement>): CompositeBrushPoint => {
          const bounds = event.currentTarget.getBoundingClientRect();
          // The client rect already includes outer xyflow and chart zoom.
          const sx = bounds.width > 0 ? innerW / bounds.width : 1;
          const sy = bounds.height > 0 ? innerH / bounds.height : 1;
          return {
            x: Math.max(0, Math.min(innerW, (event.clientX - bounds.left) * sx)),
            y: Math.max(0, Math.min(innerH, (event.clientY - bounds.top) * sy)),
          };
        };
        const finishBrush = (from: CompositeBrushPoint, to: CompositeBrushPoint) => {
          if (Math.hypot(to.x - from.x, to.y - from.y) < 4) return;
          const first = brushPixelToData(from, innerW, innerH, domain.x, domain.y,
            chartData.xScale === "log10", chartData.yScale === "log10");
          const second = brushPixelToData(to, innerW, innerH, domain.x, domain.y,
            chartData.xScale === "log10", chartData.yScale === "log10");
          onSelectionChange?.(compositeRowsInRect(spec, facet, compositeBrushBounds(first, second)));
        };
        return <g key={escapeName(facet)} data-facet={escapeName(facet)} transform={`translate(${px} ${py})`}>
          {facet !== null && !compact && <text x={panelW / 2} y={15} fontSize={11}
            textAnchor="middle" fill={theme.text}>{spec.facetBy + ": " + facet}</text>}
          <g transform={`translate(${left} ${top})`}>
            {!compact && <CartesianAxes theme={theme} innerWidth={innerW} innerHeight={innerH}
              xTicks={xTicks} yTicks={yTicks} yScale={y}
              yTickFormat={chartData.yScale === "log10" ? formatLogTick : formatScientificTick}
              xAxisLabel={(chartData.xAxis ?? "X") + (chartData.xUnit ? " (" + chartData.xUnit + ")" : "")}
              yAxisLabel={(chartData.yAxis ?? "Y") + (chartData.yUnit ? " (" + chartData.yUnit + ")" : "")}
              rotateXTicks={false} />}
            <defs><clipPath id={clipId}><rect x={0} y={0} width={innerW} height={innerH} /></clipPath></defs>
            <g clipPath={`url(#${clipId})`}>
              {spec.layers.map((layer, layerIndex) =>
                compositeGroups(spec, layer, facet).map(record => {
                  const color = colorFor(compositeColorKey(layer, layerIndex, record.group));
                  const indices = compositeSample(spec, layer, record.indices, marksBudget);
                  const xx = indices.map(i => x(spec.numbers[layer.x][i]));
                  const yy = indices.map(i => y(spec.numbers[layer.y][i]));
                  const vertices = indices.map((_, i) => xx[i] + "," + yy[i]).join(" ");
                  const base = layer.mark === "area" ? y(0) : null;
                  const lower = layer.mark === "band"
                    ? indices.slice().reverse().map(i => x(spec.numbers[layer.x][i]) + "," + y(spec.numbers[layer.y2!][i])).join(" ")
                    : "";
                  const name = (layer.label ?? layer.y) + (record.group === null ? "" : " · " + layer.colorBy + ": " + record.group);
                  const id = layerIndex + ":" + record.key;
                  // Linked highlights come from the exact selected source rows,
                  // so a brushed observation stays visible even when the
                  // ordinary display sampler dropped it. Point layers skip
                  // rows already drawn as a selected point glyph.
                  const drawn = layer.mark === "point" ? new Set(indices) : null;
                  const highlighted = compositeSample(spec, layer,
                    record.indices.filter(index => selected.has(index) && !drawn?.has(index)),
                    compositeHighlightBudget(compact));
                  return <g key={id} data-layer={layer.mark} data-group={record.group ?? ""} data-color-field={layer.colorBy ?? ""}>
                    {layer.mark === "band" && <polygon className="chart-composite-band"
                      points={vertices + " " + lower} fill={color} fillOpacity={0.24} stroke={color}
                      strokeWidth={0.8} pointerEvents="none" />}
                    {layer.mark === "area" && <polygon className="chart-composite-area"
                      points={vertices + " " + xx[xx.length - 1] + "," + base + " " + xx[0] + "," + base}
                      fill={color} fillOpacity={0.24} stroke={color} strokeWidth={0.8} pointerEvents="none" />}
                    {layer.mark === "line" && <polyline className="chart-composite-line"
                      points={vertices} fill="none" stroke={color} strokeWidth={compact ? 1.2 : 1.9}
                      strokeLinejoin="round" strokeLinecap="round" pointerEvents="none" />}
                    {layer.mark === "point" && indices.map((index, j) =>
                      <circle key={index} className="chart-composite-point" data-index={index}
                        cx={xx[j]} cy={yy[j]} r={compact ? 1.6 : hasSelection && selected.has(index) ? 4.5 : 3.2}
                        data-selected={hasSelection ? (selected.has(index) ? "true" : "false") : undefined}
                        fill={color} fillOpacity={hasSelection && !selected.has(index) ? 0.16 : 1}
                        stroke={hasSelection && selected.has(index) ? theme.text : theme.surface}
                        strokeWidth={compact ? 0 : hasSelection && selected.has(index) ? 1.6 : 0.6}
                        onPointerMove={compact ? undefined : event => onHover({
                          title: name, rows: [
                            { label: "Source row", value: String(index + 1) },
                            { label: layer.x, value: String(spec.numbers[layer.x][index]) },
                            { label: layer.y, value: String(spec.numbers[layer.y][index]) },
                            ...(record.group ? [{ label: layer.colorBy ?? "Series", value: record.group }] : []),
                          ],
                          ...tooltipPositionFromEvent(event, wrapperRef),
                        })}
                        onPointerLeave={compact ? undefined : () => onHover(null)} />)}
                    {hasSelection && highlighted.map(index =>
                      <circle key={"selected-" + index} className="chart-composite-linked-highlight"
                        data-index={index} data-selected="true"
                        cx={x(spec.numbers[layer.x][index])} cy={y(spec.numbers[layer.y][index])}
                        r={compact ? 2.1 : 4.2} fill={color} stroke={theme.text}
                        strokeWidth={compact ? 0 : 1.7} pointerEvents="none" />)}
                    {!compact && !brushEnabled && layer.mark === "line" && indices.map((index, j) =>
                      <circle key={index} cx={xx[j]} cy={yy[j]} r={4} fill="transparent"
                        onPointerMove={event => onHover({
                          title: name, rows: [
                            { label: "Source row", value: String(index + 1) },
                            { label: layer.x, value: String(spec.numbers[layer.x][index]) },
                            { label: layer.y, value: String(spec.numbers[layer.y][index]) },
                          ], ...tooltipPositionFromEvent(event, wrapperRef),
                        })}
                        onPointerLeave={() => onHover(null)} />)}
                  </g>;
                }))}
            </g>
            {!compact && brushEnabled && <g className="chart-composite-brush">
              <rect className="chart-composite-brush-surface" x={0} y={0} width={innerW} height={innerH}
                fill="transparent" pointerEvents="all" role="button" tabIndex={0}
                aria-label={"Brush rows in " + (facet === null ? "figure" : facet)}
                style={{ cursor: "crosshair", touchAction: "none" }}
                onPointerDown={event => {
                  event.stopPropagation();
                  event.preventDefault();
                  const from = point(event);
                  setBrushing({ facet, from, to: from });
                  event.currentTarget.setPointerCapture?.(event.pointerId);
                }}
                onPointerMove={event => {
                  if (!brushing || brushing.facet !== facet) return;
                  event.stopPropagation();
                  setBrushing({ ...brushing, to: point(event) });
                }}
                onPointerUp={event => {
                  if (!brushing || brushing.facet !== facet) return;
                  event.stopPropagation();
                  finishBrush(brushing.from, point(event));
                  setBrushing(null);
                  event.currentTarget.releasePointerCapture?.(event.pointerId);
                }}
                onPointerCancel={() => setBrushing(null)}
                onKeyDown={event => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    event.stopPropagation();
                    onSelectionChange?.(compositeRowsInRect(spec, facet, {
                      x: domain.x, y: domain.y,
                    }));
                  } else if (event.key === "Escape") {
                    event.preventDefault();
                    onSelectionChange?.([]);
                  }
                }} />
              {brushing?.facet === facet && <rect className="chart-composite-brush-rectangle"
                x={Math.min(brushing.from.x, brushing.to.x)}
                y={Math.min(brushing.from.y, brushing.to.y)}
                width={Math.abs(brushing.to.x - brushing.from.x)}
                height={Math.abs(brushing.to.y - brushing.from.y)}
                fill={theme.categorical[0]} fillOpacity={0.13}
                stroke={theme.text} strokeDasharray="4 3" strokeWidth={1.3}
                pointerEvents="none" />}
            </g>}
          </g>
        </g>;
      })}
      {showLegend && <g className="chart-composite-legend" aria-label="Shared series palette">
        {keys.map(({ key, label }, i) => <g key={key} transform={`translate(${8 + (i % legendColumns) * (width - 16) / legendColumns} ${10 + Math.floor(i / legendColumns) * 15})`}>
          <title>{label}</title>
          <circle cx={0} cy={0} r={3} fill={colorFor(key)} />
          <text x={8} y={3} fontSize={9} fill={theme.text}>{label}</text>
        </g>)}
      </g>}
    </g>
  );
}
