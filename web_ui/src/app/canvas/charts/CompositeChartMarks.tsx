/** V2 composite marks: grouped columns, layered geometry and small multiples.
 * Live and overview SVG use exactly the same validated specification. */
import { useId } from "react";
import type { ChartMarksProps } from "./BarChartMarks";
import { CartesianAxes, type XTick } from "./CartesianAxes";
import { createLinearScale, createLog10Scale, formatLogTick, formatScientificTick,
  log10Ticks, scientificLinearTicks } from "./chartScales";
import { tooltipPositionFromEvent } from "./chartLayout";
import { compositeColorKeys, compositeDomains, compositeFacets, compositeGroups, compositeSample } from "./compositeGeometry";

export function CompositeChartMarks({ chartData, theme, width, height, onHover, wrapperRef,
  compact = false,
}: ChartMarksProps & { compact?: boolean }) {
  const clipPrefix = useId();
  const spec = chartData.composite;
  if (!spec || width <= 0 || height <= 0) return null;
  const facets = compositeFacets(spec);
  const keys = compositeColorKeys(spec);
  const colorFor = (group: string | null, index: number) =>
    theme.categorical[(group == null ? index : Math.max(0, keys.indexOf(group))) % theme.categorical.length];
  const domain = compositeDomains(chartData);
  const cols = facets.length === 1 ? 1 : 2;
  const rows = Math.ceil(facets.length / cols);
  const gap = compact ? 4 : 12;
  const panelW = Math.max(1, (width - gap * (cols - 1)) / cols);
  const panelH = Math.max(1, (height - gap * (rows - 1)) / rows);
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
  const marksBudget = Math.max(24, Math.floor((compact ? 180 : 1200) / (facets.length * spec.layers.length)));
  const escapeName = (facet: string | null) => facet ?? "all";
  return (
    <g className={compact ? "chart-composite-overview" : "chart-composite-marks"}>
      {facets.map((facet, panel) => {
        const px = (panel % cols) * (panelW + gap);
        const py = Math.floor(panel / cols) * (panelH + gap);
        const clipId = clipPrefix + "-" + panel;
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
                  const color = colorFor(record.group, layerIndex);
                  const indices = compositeSample(spec, layer, record.indices, marksBudget);
                  const xx = indices.map(i => x(spec.numbers[layer.x][i]));
                  const yy = indices.map(i => y(spec.numbers[layer.y][i]));
                  const vertices = indices.map((_, i) => xx[i] + "," + yy[i]).join(" ");
                  const base = layer.mark === "area" ? y(0) : null;
                  const lower = layer.mark === "band"
                    ? indices.slice().reverse().map(i => x(spec.numbers[layer.x][i]) + "," + y(spec.numbers[layer.y2!][i])).join(" ")
                    : "";
                  const name = (layer.label ?? layer.y) + (record.group === null ? "" : " · " + record.group);
                  const id = layerIndex + ":" + record.key;
                  return <g key={id} data-layer={layer.mark} data-group={record.group ?? ""}>
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
                        cx={xx[j]} cy={yy[j]} r={compact ? 1.6 : 3.2} fill={color} stroke={theme.surface}
                        strokeWidth={compact ? 0 : 0.6}
                        onPointerMove={compact ? undefined : event => onHover({
                          title: name, rows: [
                            { label: layer.x, value: String(spec.numbers[layer.x][index]) },
                            { label: layer.y, value: String(spec.numbers[layer.y][index]) },
                            ...(record.group ? [{ label: layer.colorBy ?? "Series", value: record.group }] : []),
                          ],
                          ...tooltipPositionFromEvent(event, wrapperRef),
                        })}
                        onPointerLeave={compact ? undefined : () => onHover(null)} />)}
                    {!compact && layer.mark === "line" && indices.map((index, j) =>
                      <circle key={index} cx={xx[j]} cy={yy[j]} r={4} fill="transparent"
                        onPointerMove={event => onHover({
                          title: name, rows: [
                            { label: layer.x, value: String(spec.numbers[layer.x][index]) },
                            { label: layer.y, value: String(spec.numbers[layer.y][index]) },
                          ], ...tooltipPositionFromEvent(event, wrapperRef),
                        })}
                        onPointerLeave={() => onHover(null)} />)}
                  </g>;
                }))}
            </g>
          </g>
        </g>;
      })}
      {!compact && keys.length > 0 && <g className="chart-composite-legend" aria-label="Shared series palette">
        {keys.slice(0, 8).map((key, i) => <g key={key} transform={`translate(${8 + (i % 4) * (width - 16) / 4} ${10 + Math.floor(i / 4) * 15})`}>
          <circle cx={0} cy={0} r={3} fill={colorFor(key, i)} />
          <text x={8} y={3} fontSize={9} fill={theme.text}>{key}</text>
        </g>)}
      </g>}
    </g>
  );
}
