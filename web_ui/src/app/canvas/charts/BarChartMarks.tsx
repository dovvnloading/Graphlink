/**
 * ADR-013 stage 13.2: bar chart marks. Bar geometry (0.62 relative bar
 * width, the >=10-bars value-label cutoff, the negative-value zero-baseline
 * stroke) mirrors graphlink_chart_rendering.py's _render_bar_chart so the
 * live client render and the exported PNG read as the same chart, not two
 * designs.
 */

import type { RefObject } from "react";
import type { ChartDataRow } from "../../../lib/bridge-core/generated/scene-state";
import type { ChartTheme } from "./chartTheme";
import { createLinearScale, formatValue, niceTicks, pickCategoricalTicks, wrapLabel } from "./chartScales";
import { computeBottomMargin, computeLeftMargin, tooltipPositionFromEvent } from "./chartLayout";
import { CartesianAxes, type XTick } from "./CartesianAxes";
import type { TooltipContent } from "./ChartTooltip";

const BAR_WIDTH_FRACTION = 0.62;
const MAX_LABELED_BARS = 10;

export interface ChartMarksProps {
  chartData: ChartDataRow;
  theme: ChartTheme;
  width: number;
  height: number;
  onHover: (content: TooltipContent | null) => void;
  wrapperRef: RefObject<HTMLElement>;
}

export function BarChartMarks({ chartData, theme, width, height, onHover, wrapperRef }: ChartMarksProps) {
  const values = chartData.values ?? [];
  const labels = chartData.labels ?? [];
  if (values.length === 0 || width <= 0 || height <= 0) return null;

  const rawMin = Math.min(0, ...values);
  const rawMax = Math.max(0, ...values);
  let domainMin: number;
  let domainMax: number;
  if (rawMin >= 0) {
    domainMin = 0;
    domainMax = rawMax * 1.18 || 1;
  } else {
    const buffer = (rawMax - rawMin) * 0.12 || 1;
    domainMin = rawMin - buffer;
    domainMax = rawMax + buffer;
  }
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
  const slot = innerWidth / values.length;
  const barWidth = slot * BAR_WIDTH_FRACTION;
  const zeroY = yScale(0);
  const zeroLineY = domainMin < 0 && domainMax > 0 ? zeroY : null;

  const xTicks: XTick[] = tickIndexes.map((index) => ({
    position: index * slot + slot / 2,
    lines: wrapLabel(labels[index] ?? "", 14, 2),
  }));

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
        zeroLineY={zeroLineY}
      />
      {values.map((value, index) => {
        const barX = index * slot + slot / 2 - barWidth / 2;
        const barY = Math.min(zeroY, yScale(value));
        const barHeight = Math.max(0.5, Math.abs(yScale(value) - zeroY));
        const color = theme.categorical[index % theme.categorical.length];
        const label = labels[index] ?? "";
        return (
          <g key={`${label}-${index}`}>
            <rect
              x={barX}
              y={barY}
              width={barWidth}
              height={barHeight}
              fill={color}
              stroke={theme.text}
              strokeOpacity={0.16}
              rx={2}
              onPointerMove={(event) => {
                const position = tooltipPositionFromEvent(event, wrapperRef);
                onHover({ title: label, rows: [{ label: chartData.yAxis ?? "Value", value: formatValue(value) }], ...position });
              }}
              onPointerLeave={() => onHover(null)}
            />
            {values.length <= MAX_LABELED_BARS && (
              <text
                x={barX + barWidth / 2}
                y={value >= 0 ? barY - 6 : barY + barHeight + 14}
                textAnchor="middle"
                fontSize={10}
                fill={theme.text}
              >
                {formatValue(value)}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
}
