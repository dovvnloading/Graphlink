/**
 * ADR-013 stage 13.2: pie/donut chart marks - a hand-rolled SVG arc path
 * (no charting library pulled in for this), matching
 * graphlink_chart_rendering.py's _render_pie_chart geometry: a donut
 * (0.38-relative ring width => inner radius = 0.62 x outer), starting at 12
 * o'clock and sweeping clockwise, the same >10-slices "top 9 + Other"
 * collapse, and a center total/"Total" callout.
 */

import { useMemo } from "react";
import type { ChartMarksProps } from "./BarChartMarks";
import { formatValue } from "./chartScales";
import { tooltipPositionFromEvent } from "./chartLayout";

const MAX_SLICES = 10;
const RING_WIDTH_FRACTION = 0.38;
const LEGEND_ITEM_HEIGHT = 18;

interface Slice {
  label: string;
  value: number;
  startAngle: number;
  endAngle: number;
}

function polarToCartesian(cx: number, cy: number, radius: number, angleDeg: number): { x: number; y: number } {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + radius * Math.cos(rad), y: cy + radius * Math.sin(rad) };
}

function describeDonutSlice(cx: number, cy: number, outerR: number, innerR: number, a0: number, a1: number): string {
  const clampedA1 = a1 - a0 >= 359.999 ? a0 + 359.999 : a1;
  const startOuter = polarToCartesian(cx, cy, outerR, a0);
  const endOuter = polarToCartesian(cx, cy, outerR, clampedA1);
  const startInner = polarToCartesian(cx, cy, innerR, clampedA1);
  const endInner = polarToCartesian(cx, cy, innerR, a0);
  const largeArc = clampedA1 - a0 > 180 ? 1 : 0;
  return [
    `M ${startOuter.x} ${startOuter.y}`,
    `A ${outerR} ${outerR} 0 ${largeArc} 1 ${endOuter.x} ${endOuter.y}`,
    `L ${startInner.x} ${startInner.y}`,
    `A ${innerR} ${innerR} 0 ${largeArc} 0 ${endInner.x} ${endInner.y}`,
    "Z",
  ].join(" ");
}

function buildSlices(labels: string[], values: number[]): Slice[] {
  let entries = labels.map((label, index) => ({ label, value: values[index] }));
  if (entries.length > MAX_SLICES) {
    const ranked = [...entries].sort((a, b) => b.value - a.value);
    const top = ranked.slice(0, MAX_SLICES - 1);
    const remainder = ranked.slice(MAX_SLICES - 1).reduce((sum, entry) => sum + entry.value, 0);
    entries = [...top, { label: "Other", value: remainder }];
  }
  const total = entries.reduce((sum, entry) => sum + entry.value, 0) || 1;
  let cursor = 0;
  return entries.map((entry) => {
    const sweep = (entry.value / total) * 360;
    const slice: Slice = { label: entry.label, value: entry.value, startAngle: cursor, endAngle: cursor + sweep };
    cursor += sweep;
    return slice;
  });
}

export function PieChartMarks({ chartData, theme, width, height, onHover, wrapperRef }: ChartMarksProps) {
  const slices = useMemo(
    () => buildSlices(chartData.labels ?? [], chartData.values ?? []),
    [chartData.labels, chartData.values],
  );
  if (slices.length === 0 || width <= 0 || height <= 0) return null;

  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const legendWidth = width > 380 ? Math.min(180, width * 0.34) : 0;
  const plotWidth = width - legendWidth;
  const cx = plotWidth / 2;
  const cy = height / 2;
  const outerR = Math.max(10, Math.min(plotWidth, height) / 2 - 12);
  const innerR = outerR * (1 - RING_WIDTH_FRACTION);

  return (
    <g>
      {slices.map((slice, index) => {
        const color = theme.categorical[index % theme.categorical.length];
        const percentage = total ? (slice.value / total) * 100 : 0;
        return (
          <path
            key={`${slice.label}-${index}`}
            d={describeDonutSlice(cx, cy, outerR, innerR, slice.startAngle, slice.endAngle)}
            fill={color}
            stroke={theme.surface}
            strokeWidth={2}
            onPointerMove={(event) => {
              const position = tooltipPositionFromEvent(event, wrapperRef);
              onHover({
                title: slice.label,
                rows: [
                  { label: "Value", value: formatValue(slice.value) },
                  { label: "Share", value: `${percentage.toFixed(1)}%` },
                ],
                ...position,
              });
            }}
            onPointerLeave={() => onHover(null)}
          />
        );
      })}
      <text x={cx} y={cy - 4} textAnchor="middle" fontSize={18} fontWeight={700} fill={theme.text}>
        {formatValue(total)}
      </text>
      <text x={cx} y={cy + 16} textAnchor="middle" fontSize={11} fill={theme.textMuted}>
        Total
      </text>
      {legendWidth > 0 && (
        <g transform={`translate(${plotWidth + 16} ${height / 2 - (slices.length * LEGEND_ITEM_HEIGHT) / 2})`}>
          {slices.map((slice, index) => {
            const percentage = total ? (slice.value / total) * 100 : 0;
            return (
              <g key={`${slice.label}-legend-${index}`} transform={`translate(0 ${index * LEGEND_ITEM_HEIGHT})`}>
                <rect width={10} height={10} y={-9} fill={theme.categorical[index % theme.categorical.length]} rx={2} />
                <text x={16} fontSize={10} fill={theme.text}>
                  {slice.label.length > 20 ? `${slice.label.slice(0, 19)}…` : slice.label}
                  <tspan fill={theme.textMuted}>{`  ${percentage.toFixed(1)}%`}</tspan>
                </text>
              </g>
            );
          })}
        </g>
      )}
    </g>
  );
}
