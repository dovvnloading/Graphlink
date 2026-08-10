/**
 * ADR-013 stage 13.2: the shared axis/gridline chrome for bar, line, and
 * histogram (the three cartesian chart types) - one place for tick
 * rendering so each chart type only supplies its own marks.
 */

import { Fragment } from "react";
import type { ChartTheme } from "./chartTheme";
import { formatValue, type Scale } from "./chartScales";

export interface XTick {
  position: number;
  lines: string[];
}

export interface CartesianAxesProps {
  theme: ChartTheme;
  innerWidth: number;
  innerHeight: number;
  yTicks: number[];
  yScale: Scale;
  xAxisLabel: string;
  yAxisLabel: string;
  xTicks: XTick[];
  rotateXTicks: boolean;
  /** Pixel y-position of the zero baseline, when the value domain spans
   * both signs (bar/line charts with negative values) and it should be
   * called out with its own stroke, matching graphlink_chart_rendering.py's
   * ax.axhline(0, ...) for the same case. */
  zeroLineY?: number | null;
}

const LINE_HEIGHT = 12;

export function CartesianAxes({
  theme,
  innerWidth,
  innerHeight,
  yTicks,
  yScale,
  xAxisLabel,
  yAxisLabel,
  xTicks,
  rotateXTicks,
  zeroLineY = null,
}: CartesianAxesProps) {
  return (
    <g className="chart-axes">
      {yTicks.map((tick) => {
        const y = yScale(tick);
        return (
          <Fragment key={tick}>
            <line x1={0} x2={innerWidth} y1={y} y2={y} stroke={theme.gridline} strokeOpacity={0.35} strokeDasharray="3 3" />
            <text x={-8} y={y} textAnchor="end" dominantBaseline="middle" fill={theme.textMuted} fontSize={10}>
              {formatValue(tick)}
            </text>
          </Fragment>
        );
      })}
      {zeroLineY !== null && (
        <line x1={0} x2={innerWidth} y1={zeroLineY} y2={zeroLineY} stroke={theme.text} strokeOpacity={0.5} strokeWidth={1} />
      )}
      <line x1={0} x2={0} y1={0} y2={innerHeight} stroke={theme.border} />
      <line x1={0} x2={innerWidth} y1={innerHeight} y2={innerHeight} stroke={theme.border} />
      {xTicks.map((tick) => (
        <text
          key={tick.position}
          x={tick.position}
          y={innerHeight + 16}
          textAnchor={rotateXTicks ? "end" : "middle"}
          fill={theme.textMuted}
          fontSize={10}
          transform={rotateXTicks ? `rotate(-25 ${tick.position} ${innerHeight + 16})` : undefined}
        >
          {tick.lines.map((line, index) => (
            <tspan key={line} x={tick.position} dy={index === 0 ? 0 : LINE_HEIGHT}>
              {line}
            </tspan>
          ))}
        </text>
      ))}
      {xAxisLabel && (
        <text x={innerWidth / 2} y={innerHeight + (rotateXTicks ? 52 : 34)} textAnchor="middle" fill={theme.textMuted} fontSize={11}>
          {xAxisLabel}
        </text>
      )}
      {yAxisLabel && (
        <text
          x={-44}
          y={innerHeight / 2}
          textAnchor="middle"
          fill={theme.textMuted}
          fontSize={11}
          transform={`rotate(-90 ${-44} ${innerHeight / 2})`}
        >
          {yAxisLabel}
        </text>
      )}
    </g>
  );
}
