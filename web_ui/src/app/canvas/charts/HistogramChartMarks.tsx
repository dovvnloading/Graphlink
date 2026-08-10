/**
 * ADR-013 stage 13.2: histogram marks - equal-width binned bars over a
 * continuous value axis, with mean (dashed) / median (dash-dot) reference
 * lines and a small legend, mirroring graphlink_chart_rendering.py's
 * _render_histogram.
 */

import type { ChartMarksProps } from "./BarChartMarks";
import { computeHistogramBins, createLinearScale, formatValue, mean, median, niceTicks } from "./chartScales";
import { computeBottomMargin, computeLeftMargin, tooltipPositionFromEvent } from "./chartLayout";
import { CartesianAxes, type XTick } from "./CartesianAxes";

const BIN_GAP_FRACTION = 0.08;

export function HistogramChartMarks({ chartData, theme, width, height, onHover, wrapperRef }: ChartMarksProps) {
  const values = chartData.values ?? [];
  const bins = chartData.bins ?? 10;
  if (values.length === 0 || width <= 0 || height <= 0) return null;

  const histogram = computeHistogramBins(values, bins);
  const maxCount = Math.max(...histogram.map((bin) => bin.count));
  const yDomainMax = maxCount * 1.18 || 1;
  const yTicks = niceTicks(0, yDomainMax, 5);
  const domainMax = Math.max(yDomainMax, yTicks[yTicks.length - 1]);

  const marginLeft = computeLeftMargin(yTicks, Boolean(chartData.yAxis));
  const marginRight = 16;
  const marginTop = 12;
  const innerWidth = Math.max(1, width - marginLeft - marginRight);
  const marginBottom = computeBottomMargin(false, Boolean(chartData.xAxis));
  const innerHeight = Math.max(1, height - marginTop - marginBottom);

  const yScale = createLinearScale([0, domainMax], [innerHeight, 0]);
  const valueMin = histogram[0].x0;
  const valueMax = histogram[histogram.length - 1].x1;
  const xScale = createLinearScale([valueMin, valueMax], [0, innerWidth]);

  const xTickValues = niceTicks(valueMin, valueMax, 6).filter((tick) => tick >= valueMin && tick <= valueMax);
  const xTicks: XTick[] = xTickValues.map((tick) => ({ position: xScale(tick), lines: [formatValue(tick)] }));

  const avg = mean(values);
  const med = median(values);

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
        rotateXTicks={false}
      />
      {histogram.map((bin, index) => {
        const x0 = xScale(bin.x0);
        const x1 = xScale(bin.x1);
        const gap = (x1 - x0) * BIN_GAP_FRACTION;
        const barY = yScale(bin.count);
        return (
          <rect
            key={`${bin.x0}-${index}`}
            x={x0 + gap / 2}
            y={barY}
            width={Math.max(0.5, x1 - x0 - gap)}
            height={Math.max(0, innerHeight - barY)}
            fill={theme.categorical[index % theme.categorical.length]}
            fillOpacity={0.82}
            stroke={theme.text}
            strokeOpacity={0.24}
            onPointerMove={(event) => {
              const position = tooltipPositionFromEvent(event, wrapperRef);
              onHover({
                title: `${formatValue(bin.x0)} – ${formatValue(bin.x1)}`,
                rows: [{ label: chartData.yAxis ?? "Frequency", value: formatValue(bin.count) }],
                ...position,
              });
            }}
            onPointerLeave={() => onHover(null)}
          />
        );
      })}
      <line
        x1={xScale(avg)}
        x2={xScale(avg)}
        y1={0}
        y2={innerHeight}
        stroke={theme.categorical[0]}
        strokeWidth={1.6}
        strokeDasharray="6 4"
      />
      <line
        x1={xScale(med)}
        x2={xScale(med)}
        y1={0}
        y2={innerHeight}
        stroke={theme.categorical[1 % theme.categorical.length]}
        strokeWidth={1.6}
        strokeDasharray="7 3 1 3"
      />
      <g transform="translate(8 8)" fontSize={9}>
        <line x1={0} x2={14} y1={2} y2={2} stroke={theme.categorical[0]} strokeWidth={1.6} strokeDasharray="6 4" />
        <text x={18} y={5} fill={theme.textMuted}>
          {`Mean ${formatValue(avg)}`}
        </text>
        <line x1={0} x2={14} y1={16} y2={16} stroke={theme.categorical[1 % theme.categorical.length]} strokeWidth={1.6} strokeDasharray="7 3 1 3" />
        <text x={18} y={19} fill={theme.textMuted}>
          {`Median ${formatValue(med)}`}
        </text>
      </g>
    </g>
  );
}
