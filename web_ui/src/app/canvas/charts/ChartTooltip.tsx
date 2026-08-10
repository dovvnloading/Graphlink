/**
 * ADR-013 stage 13.2: the one tooltip presentation every chart type's hover
 * state feeds. An HTML overlay (not SVG <text>) positioned in the wrapper's
 * own CSS pixel space - deliberately NOT inside the zoomed/panned <g> that
 * useZoomPane drives, so the tooltip text never scales or drifts with the
 * chart's zoom level, matching how tooltips behave on the outer xyflow
 * canvas itself.
 */

import type { ChartTheme } from "./chartTheme";

export interface TooltipRow {
  label: string;
  value: string;
}

export interface TooltipContent {
  title: string;
  rows: TooltipRow[];
  /** Position in the wrapper's own CSS pixel space (not SVG/content space). */
  x: number;
  y: number;
}

export function ChartTooltip({ content, theme }: { content: TooltipContent | null; theme: ChartTheme }) {
  if (!content) return null;
  return (
    <div
      className="chart-tooltip"
      style={{
        left: content.x,
        top: content.y,
        backgroundColor: theme.tooltipBackground,
        borderColor: theme.tooltipBorder,
        color: theme.text,
        fontFamily: theme.fontFamily,
      }}
      role="tooltip"
    >
      <div className="chart-tooltip-title">{content.title}</div>
      {content.rows.map((row) => (
        <div className="chart-tooltip-row" key={row.label}>
          <span className="chart-tooltip-row-label" style={{ color: theme.textMuted }}>
            {row.label}
          </span>
          <span className="chart-tooltip-row-value">{row.value}</span>
        </div>
      ))}
    </div>
  );
}
