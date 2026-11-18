/** Tukey box/whisker marks: median-of-halves quartiles, 1.5-IQR whiskers,
 * and all individual outliers. Geometry is shared with the export semantics. */
import { useId } from "react";
import type { ChartMarksProps } from "./BarChartMarks";
import { CartesianAxes, type XTick } from "./CartesianAxes";
import {
  computeBoxplotStats, createLinearScale, scientificDomain,
  scientificLinearTicks, formatScientificTick, pickCategoricalTicks, wrapLabel,
} from "./chartScales";
import { computeBottomMargin, tooltipPositionFromEvent } from "./chartLayout";

export function BoxplotChartMarks({
  chartData, theme, width, height, onHover, wrapperRef, viewport,
}: ChartMarksProps) {
  const clipId = useId();
  const groups = chartData.groups ?? [];
  if (!groups.length || width <= 0 || height <= 0) return null;
  const summaries = groups.map((group) => computeBoxplotStats(group.values));
  const low = Math.min(...groups.flatMap((group) => group.values));
  const high = Math.max(...groups.flatMap((group) => group.values));
  const baseY = scientificDomain(low, high, "linear");
  const yDomain = viewport?.y ?? baseY;
  const marginLeft = 76;
  const marginRight = 20;
  const marginTop = 18;
  const innerWidth = Math.max(1, width - marginLeft - marginRight);
  const { indexes, rotate } = pickCategoricalTicks(groups.length, innerWidth);
  const marginBottom = computeBottomMargin(rotate, true);
  const innerHeight = Math.max(1, height - marginTop - marginBottom);
  const xScale = createLinearScale(viewport?.x ?? [-0.5, groups.length - 0.5], [0, innerWidth]);
  const slot = viewport ? xScale(1) - xScale(0) : innerWidth / groups.length;
  const y = createLinearScale(yDomain, [innerHeight, 0]);
  const yTicks = scientificLinearTicks(...yDomain).filter((value) => value >= yDomain[0] && value <= yDomain[1]);
  const xTicks: XTick[] = indexes.filter((index) => !viewport || (index >= viewport.x[0] && index <= viewport.x[1])).map((index) => ({
    position: viewport ? xScale(index) : (index + 0.5) * slot,
    lines: wrapLabel(groups[index].label, 14, 2),
  }));
  const palette = theme.categorical.length ? theme.categorical : [theme.text];

  return (
    <g className="chart-boxplot-marks" transform={`translate(${marginLeft} ${marginTop})`}>
      <CartesianAxes
        theme={theme} innerWidth={innerWidth} innerHeight={innerHeight}
        xTicks={xTicks} yTicks={yTicks} yScale={y} yTickFormat={formatScientificTick}
        xAxisLabel={chartData.xAxis ?? "Group"}
        yAxisLabel={chartData.yAxis ?? "Value"} rotateXTicks={rotate}
        zeroLineY={yDomain[0] < 0 && yDomain[1] > 0 ? y(0) : null}
      />
      {viewport && <defs><clipPath id={clipId}><rect x={0} y={0} width={innerWidth} height={innerHeight} /></clipPath></defs>}
      <g clipPath={viewport ? "url(#" + clipId + ")" : undefined}>
      {groups.map((group, index) => {
        const stat = summaries[index];
        const cx = viewport ? xScale(index) : (index + 0.5) * slot;
        const half = slot * 0.28;
        const top = y(stat.q3);
        const bottom = y(stat.q1);
        const color = palette[index % palette.length];
        const hoverRows = [
          { label: "Observations", value: String(group.values.length) },
          { label: "Median", value: String(stat.median) },
          { label: "Q1 / Q3", value: String(stat.q1) + " / " + String(stat.q3) },
          { label: "Whiskers", value: String(stat.min) + " / " + String(stat.max) },
          { label: "Outliers", value: String(stat.outliers.length) },
        ];
        return (
          <g className="chart-boxplot-group" data-label={group.label} key={group.label}>
            <g stroke={theme.textMuted} strokeWidth={1.5} pointerEvents="none">
              <line x1={cx} x2={cx} y1={y(stat.max)} y2={top} />
              <line x1={cx} x2={cx} y1={bottom} y2={y(stat.min)} />
              <line x1={cx - half / 2} x2={cx + half / 2} y1={y(stat.max)} y2={y(stat.max)} />
              <line x1={cx - half / 2} x2={cx + half / 2} y1={y(stat.min)} y2={y(stat.min)} />
            </g>
            <rect className="chart-boxplot-box" x={cx - half} y={top}
              width={half * 2} height={Math.max(0.5, bottom - top)}
              fill={color} fillOpacity={0.75} stroke={theme.text} strokeWidth={1.2}
              pointerEvents="none" />
            <line className="chart-boxplot-median" x1={cx - half} x2={cx + half}
              y1={y(stat.median)} y2={y(stat.median)} stroke={theme.text} strokeWidth={2.2} pointerEvents="none" />
            {stat.outliers.map((outlier, j) => (
              <circle key={j} className="chart-boxplot-outlier" cx={cx} cy={y(outlier)}
                r={3.5} fill={theme.surface} stroke={color} strokeWidth={1.5} pointerEvents="none" />
            ))}
            <rect className="chart-boxplot-hit-target" x={viewport ? xScale(index - 0.5) : index * slot} y={0}
              width={slot} height={innerHeight} fill="transparent"
              onPointerMove={(event) => {
                onHover({ title: group.label, rows: hoverRows, ...tooltipPositionFromEvent(event, wrapperRef) });
              }}
              onPointerLeave={() => onHover(null)} />
          </g>
        );
      })}
      </g>
    </g>
  );
}
