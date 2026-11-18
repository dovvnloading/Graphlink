/** Scientific line and scatter marks. No charting library: the same finite
 * coordinates/error magnitudes checked by both canonicalizers are mapped to
 * SVG; only the scale (linear/log10) changes, never the numeric data. */
import { useId } from "react";
import type { AxisScale, ErrorPair } from "../chartSpec";
import type { ViewDomains } from "./chartViewport";
import type { ChartMarksProps } from "./BarChartMarks";
import { CartesianAxes, type XTick } from "./CartesianAxes";
import {
  createLinearScale, createLog10Scale, formatLogTick, formatScientificTick,
  log10Ticks, scientificLinearTicks, pickCategoricalTicks, scientificDomain, wrapLabel,
  type Scale,
} from "./chartScales";
import { computeBottomMargin, tooltipPositionFromEvent } from "./chartLayout";

interface PlottedSeries {
  name: string;
  values: number[];
  xError?: ErrorPair[] | null;
  yError?: ErrorPair[] | null;
}

export function scientificAxisLabel(label: string, unit?: string | null): string {
  return unit ? label + " (" + unit + ")" : label;
}

function axisExtent(values: number[], errors?: ErrorPair[] | null): [number, number] {
  let low = Infinity;
  let high = -Infinity;
  for (let i = 0; i < values.length; i += 1) {
    const [minus, plus] = errors?.[i] ?? [0, 0];
    low = Math.min(low, values[i] - minus);
    high = Math.max(high, values[i] + plus);
  }
  return [low, high];
}

/** These are physical coordinates, including every error endpoint. */
export function scientificExtents(xValues: number[], series: PlottedSeries[]): {
  x: [number, number]; y: [number, number];
} {
  let xMin = Infinity;
  let xMax = -Infinity;
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const row of series) {
    const [loX, hiX] = axisExtent(xValues, row.xError);
    const [loY, hiY] = axisExtent(row.values, row.yError);
    xMin = Math.min(xMin, loX);
    xMax = Math.max(xMax, hiX);
    yMin = Math.min(yMin, loY);
    yMax = Math.max(yMax, hiY);
  }
  return { x: [xMin, xMax], y: [yMin, yMax] };
}

function scaleFor(domain: [number, number], range: [number, number], axis: AxisScale): Scale {
  return axis === "log10" ? createLog10Scale(domain, range) : createLinearScale(domain, range);
}

function labeledTicks(domain: [number, number], scale: AxisScale): number[] {
  return scale === "log10" ? log10Ticks(...domain) : scientificLinearTicks(...domain, 5);
}

/** Keep tooltips exact: the legacy abbreviated tick-label formatter rounds
 * >=1000 to integers and values <.001 to zero, which is wrong for readings. */
export function exactWithUnit(value: number, unit?: string | null): string {
  return String(value) + (unit ? " " + unit : "");
}

function errorWithUnit(pair: ErrorPair, unit?: string | null): string {
  return "-" + String(pair[0]) + "/+" + String(pair[1]) + (unit ? " " + unit : "");
}

export function ScientificChartMarks({
  chartData, theme, width, height, onHover, wrapperRef, chartType, viewport,
}: ChartMarksProps & { chartType: string; viewport?: ViewDomains }) {
  const clipId = useId();
  const data = chartData;
  const named = data.series;
  const series: PlottedSeries[] = named ?? [{
    name: "",
    values: data.values ?? [],
    xError: data.xError,
    yError: data.yError,
  }];
  if (series.length === 0 || series[0].values.length === 0 || width <= 0 || height <= 0) return null;

  const xValues = data.xValues ?? series[0].values.map((_, index) => index);
  const xScaleType = data.xScale ?? "linear";
  const yScaleType = data.yScale ?? "linear";
  const extents = scientificExtents(xValues, series);
  const xDomain = viewport?.x ?? scientificDomain(...extents.x, xScaleType);
  const yDomain = viewport?.y ?? scientificDomain(...extents.y, yScaleType);

  const legendColumns = Math.min(4, named?.length ?? 0);
  const legendRows = named ? Math.ceil(named.length / legendColumns) : 0;
  const marginTop = 16 + legendRows * 22;
  const marginLeft = 74;
  const marginRight = 22;
  const innerWidth = Math.max(1, width - marginLeft - marginRight);
  const categorical = chartType === "line" && !data.xValues;
  const { indexes, rotate } = categorical
    ? pickCategoricalTicks(xValues.length, innerWidth)
    : { indexes: [], rotate: false };
  const marginBottom = computeBottomMargin(rotate, true);
  const innerHeight = Math.max(1, height - marginTop - marginBottom);
  const x = scaleFor(xDomain, [0, innerWidth], xScaleType);
  const y = scaleFor(yDomain, [innerHeight, 0], yScaleType);
  const yTicks = labeledTicks(yDomain, yScaleType).filter((value) => value >= yDomain[0] && value <= yDomain[1]);
  const xTicks: XTick[] = categorical
    ? indexes.filter((index) => !viewport || (xValues[index] >= xDomain[0] && xValues[index] <= xDomain[1])).map((index) => ({
        position: x(xValues[index]),
        lines: wrapLabel(data.labels?.[index] ?? "", 14, 2),
      }))
    : labeledTicks(xDomain, xScaleType).filter((value) => value >= xDomain[0] && value <= xDomain[1]).map((value) => ({
        position: x(value),
        lines: [xScaleType === "log10" ? formatLogTick(value) : formatScientificTick(value)],
      }));
  const palette = theme.categorical.length ? theme.categorical : [theme.text];

  return (
    <g className="chart-scientific-marks" transform={"translate(" + marginLeft + " " + marginTop + ")"}>
      <CartesianAxes
        theme={theme}
        innerWidth={innerWidth}
        innerHeight={innerHeight}
        xTicks={xTicks}
        yTicks={yTicks}
        yScale={y}
        yTickFormat={yScaleType === "log10" ? formatLogTick : formatScientificTick}
        xAxisLabel={scientificAxisLabel(data.xAxis ?? "X", data.xUnit)}
        yAxisLabel={scientificAxisLabel(data.yAxis ?? "Y", data.yUnit)}
        rotateXTicks={rotate}
        zeroLineY={yDomain[0] < 0 && yDomain[1] > 0 && yScaleType === "linear" ? y(0) : null}
      />
      {viewport && <defs><clipPath id={clipId}><rect x={0} y={0} width={innerWidth} height={innerHeight} /></clipPath></defs>}
      {named && (
        <g className="chart-series-legend" aria-label="Series legend">
          {named.map((row, index) => {
            const col = index % legendColumns;
            const legendRow = Math.floor(index / legendColumns);
            const lx = col * innerWidth / legendColumns;
            const ly = -20 - (legendRows - 1 - legendRow) * 22;
            return (
              <g key={row.name} transform={"translate(" + lx + " " + ly + ")"}>
                <line x1={0} x2={16} y1={0} y2={0} stroke={palette[index % palette.length]} strokeWidth={3} />
                <text x={21} y={3} fill={theme.text} fontSize={11}>{row.name}</text>
              </g>
            );
          })}
        </g>
      )}
      <g clipPath={viewport ? "url(#" + clipId + ")" : undefined}>
      {series.map((row, seriesIndex) => {
        const color = palette[seriesIndex % palette.length];
        const name = row.name || data.title || "Series";
        const path = xValues.map((xValue, index) =>
          (index === 0 ? "M " : "L ") + x(xValue) + " " + y(row.values[index]),
        ).join(" ");
        return (
          <g key={name} className="chart-scientific-series" data-series={name}>
            {chartType === "line" && (
              <path className="chart-series-line" d={path} fill="none"
                stroke={color} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
            )}
            {xValues.map((xValue, index) => {
              const value = row.values[index];
              const centerX = x(xValue);
              const centerY = y(value);
              const xError = row.xError?.[index];
              const yError = row.yError?.[index];
              const tooltipRows = [
                { label: data.xAxis ?? "X", value: categorical
                  ? data.labels?.[index] ?? String(index)
                  : exactWithUnit(xValue, data.xUnit) },
                { label: data.yAxis ?? "Y", value: exactWithUnit(value, data.yUnit) },
              ];
              if (xError) tooltipRows.push({ label: "X error", value: errorWithUnit(xError, data.xUnit) });
              if (yError) tooltipRows.push({ label: "Y error", value: errorWithUnit(yError, data.yUnit) });
              return (
                <g key={index} className="chart-scientific-point" data-index={index}>
                  {xError && (
                    <g className="chart-x-error" stroke={color} strokeWidth={1.4} pointerEvents="none">
                      <line x1={x(xValue - xError[0])} x2={x(xValue + xError[1])} y1={centerY} y2={centerY} />
                      <line x1={x(xValue - xError[0])} x2={x(xValue - xError[0])} y1={centerY - 4} y2={centerY + 4} />
                      <line x1={x(xValue + xError[1])} x2={x(xValue + xError[1])} y1={centerY - 4} y2={centerY + 4} />
                    </g>
                  )}
                  {yError && (
                    <g className="chart-y-error" stroke={color} strokeWidth={1.4} pointerEvents="none">
                      <line x1={centerX} x2={centerX} y1={y(value - yError[0])} y2={y(value + yError[1])} />
                      <line x1={centerX - 4} x2={centerX + 4} y1={y(value - yError[0])} y2={y(value - yError[0])} />
                      <line x1={centerX - 4} x2={centerX + 4} y1={y(value + yError[1])} y2={y(value + yError[1])} />
                    </g>
                  )}
                  <circle className="chart-series-marker" cx={centerX} cy={centerY}
                    r={4.5} fill={theme.surface} stroke={color} strokeWidth={2}
                    onPointerMove={(event) => {
                      const position = tooltipPositionFromEvent(event, wrapperRef);
                      onHover({ title: name, rows: tooltipRows, ...position });
                    }}
                    onPointerLeave={() => onHover(null)}
                  />
                </g>
              );
            })}
          </g>
        );
      })}
      </g>
    </g>
  );
}
