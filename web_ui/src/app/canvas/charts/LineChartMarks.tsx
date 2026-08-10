/**
 * ADR-013 stage 13.2: line chart marks - a stroked polyline with a
 * translucent fill down to the zero baseline (when in range) and point
 * markers, mirroring graphlink_chart_rendering.py's _render_line_chart
 * (same y-domain padding rule, same <=12-points value-label cutoff).
 */

import { createLinearScale, formatValue, niceTicks, pickCategoricalTicks, wrapLabel } from "./chartScales";
import { computeBottomMargin, computeLeftMargin, tooltipPositionFromEvent } from "./chartLayout";
import { CartesianAxes, type XTick } from "./CartesianAxes";
import type { ChartMarksProps } from "./BarChartMarks";

const MAX_LABELED_POINTS = 12;
const POINT_RADIUS = 4.5;

export function LineChartMarks({ chartData, theme, width, height, onHover, wrapperRef }: ChartMarksProps) {
  const values = chartData.values ?? [];
  const labels = chartData.labels ?? [];
  if (values.length === 0 || width <= 0 || height <= 0) return null;

  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const span = rawMax - rawMin;
  const buffer = span * 0.18 || Math.max(Math.abs(rawMax), 1) * 0.18 || 1;
  let domainMin = rawMin - buffer;
  let domainMax = rawMax + buffer;
  const yTicks = niceTicks(domainMin, domainMax, 5);
  domainMin = Math.min(domainMin, yTicks[0]);
  domainMax = Math.max(domainMax, yTicks[yTicks.length - 1]);

  const hasYAxisLabel = Boolean(chartData.yAxis);
  const marginLeft = computeLeftMargin(yTicks, hasYAxisLabel);
  const marginRight = 16;
  const marginTop = 12;
  const innerWidth = Math.max(1, width - marginLeft - marginRight);

  const { indexes: tickIndexes, rotate } = pickCategoricalTicks(labels.length, innerWidth);
  const marginBottom = computeBottomMargin(rotate, Boolean(chartData.xAxis));
  const innerHeight = Math.max(1, height - marginTop - marginBottom);

  const yScale = createLinearScale([domainMin, domainMax], [innerHeight, 0]);
  const slot = values.length > 1 ? innerWidth / (values.length - 1) : 0;
  const positions = values.map((_, index) => (values.length > 1 ? index * slot : innerWidth / 2));
  const baseline = Math.min(0, rawMin);
  const baselineInRange = baseline >= domainMin && baseline <= domainMax;

  const linePath = positions.map((x, index) => `${index === 0 ? "M" : "L"} ${x} ${yScale(values[index])}`).join(" ");
  const areaPath = baselineInRange
    ? [
        `M ${positions[0]} ${yScale(baseline)}`,
        ...positions.map((x, index) => `L ${x} ${yScale(values[index])}`),
        `L ${positions[positions.length - 1]} ${yScale(baseline)}`,
        "Z",
      ].join(" ")
    : "";

  const xTicks: XTick[] = tickIndexes.map((index) => ({
    position: positions[index],
    lines: wrapLabel(labels[index] ?? "", 14, 2),
  }));

  const lineColor = theme.categorical[0];

  return (
    <g transform={`translate(${marginLeft} ${marginTop})`}>
      <CartesianAxes
        theme={theme}
        innerWidth={innerWidth}
        innerHeight={innerHeight}
        yTicks={yTicks}
        yScale={yScale}
        xAxisLabel={chartData.xAxis ?? ""}
        yAxisLabel={chartData.yAxis ?? ""}
        xTicks={xTicks}
        rotateXTicks={rotate}
        zeroLineY={domainMin < 0 && domainMax > 0 ? yScale(0) : null}
      />
      {areaPath && <path d={areaPath} fill={lineColor} fillOpacity={0.15} stroke="none" />}
      <path d={linePath} fill="none" stroke={lineColor} strokeWidth={2.8} strokeLinejoin="round" strokeLinecap="round" />
      {positions.map((x, index) => {
        const value = values[index];
        const label = labels[index] ?? "";
        return (
          <g key={`${label}-${index}`}>
            <circle
              cx={x}
              cy={yScale(value)}
              r={POINT_RADIUS}
              fill={theme.surface}
              stroke={lineColor}
              strokeWidth={1.8}
              onPointerMove={(event) => {
                const position = tooltipPositionFromEvent(event, wrapperRef);
                onHover({ title: label, rows: [{ label: chartData.yAxis ?? "Value", value: formatValue(value) }], ...position });
              }}
              onPointerLeave={() => onHover(null)}
            />
            {values.length <= MAX_LABELED_POINTS && (
              <text x={x} y={yScale(value) - 10} textAnchor="middle" fontSize={10} fill={theme.text}>
                {formatValue(value)}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
}
