/** Dense heatmap SVG cells with a single matrix-wide color domain.
 * Negative/positive matrices use zero-centered colors, matching exports. */
import { useId } from "react";
import type { ChartMarksProps } from "./BarChartMarks";
import { heatmapColor, pickCategoricalTicks, wrapLabel } from "./chartScales";
import { computeBottomMargin, tooltipPositionFromEvent } from "./chartLayout";

export function HeatmapChartMarks({
  chartData, theme, width, height, onHover, wrapperRef,
}: ChartMarksProps) {
  const gradientId = useId();
  const data = chartData;
  const rows = data.rowLabels ?? [];
  const columns = data.columnLabels ?? [];
  const matrix = data.matrix ?? [];
  if (!rows.length || !columns.length || width <= 0 || height <= 0) return null;

  const values = matrix.flat();
  const low = Math.min(...values);
  const high = Math.max(...values);
  const diverging = low < 0 && high > 0;
  const marginLeft = 94;
  const marginRight = 78;
  const marginTop = 12;
  const innerWidth = Math.max(1, width - marginLeft - marginRight);
  const { indexes: colIndexes, rotate } = pickCategoricalTicks(columns.length, innerWidth);
  const marginBottom = computeBottomMargin(rotate, true) + 10;
  const innerHeight = Math.max(1, height - marginTop - marginBottom);
  const cellWidth = innerWidth / columns.length;
  const cellHeight = innerHeight / rows.length;
  const rowStride = Math.max(1, Math.ceil(rows.length / 12));
  const rowIndexes: number[] = [];
  for (let index = 0; index < rows.length; index += rowStride) rowIndexes.push(index);
  if (rowIndexes[rowIndexes.length - 1] !== rows.length - 1) rowIndexes.push(rows.length - 1);
  const gradientStops = diverging
    ? [{ offset: "0%", color: heatmapColor(high, low, high) },
       { offset: "50%", color: heatmapColor(0, low, high) },
       { offset: "100%", color: heatmapColor(low, low, high) }]
    : [{ offset: "0%", color: heatmapColor(high, low, high) },
       { offset: "100%", color: heatmapColor(low, low, high) }];

  return (
    <g className="chart-heatmap-marks" transform={`translate(${marginLeft} ${marginTop})`}>
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          {gradientStops.map((stop) => (
            <stop key={stop.offset} offset={stop.offset} stopColor={stop.color} />
          ))}
        </linearGradient>
      </defs>
      <rect x={0} y={0} width={innerWidth} height={innerHeight}
        fill="none" stroke={theme.border} strokeWidth={1} />
      {matrix.map((row, rowIndex) => row.map((value, colIndex) => (
        <rect key={rowIndex + ":" + colIndex} className="chart-heatmap-cell"
          data-row={rowIndex} data-column={colIndex}
          x={colIndex * cellWidth} y={rowIndex * cellHeight}
          width={cellWidth} height={cellHeight}
          fill={heatmapColor(value, low, high)} stroke={theme.surface}
          strokeWidth={0.6}
          aria-label={rows[rowIndex] + ", " + columns[colIndex] + ": " + String(value)}
          onPointerMove={(event) => {
            onHover({
              title: data.title ?? "Heatmap",
              rows: [
                { label: data.yAxis ?? "Rows", value: rows[rowIndex] },
                { label: data.xAxis ?? "Columns", value: columns[colIndex] },
                { label: "Value", value: String(value) + (data.valueUnit ? " " + data.valueUnit : "") },
              ],
              ...tooltipPositionFromEvent(event, wrapperRef),
            });
          }}
          onPointerLeave={() => onHover(null)} />
      )))}
      {colIndexes.map((index) => (
        <text key={index} x={(index + 0.5) * cellWidth} y={innerHeight + 16}
          textAnchor={rotate ? "end" : "middle"}
          transform={rotate
            ? `rotate(-25 ${(index + 0.5) * cellWidth} ${innerHeight + 16})`
            : undefined}
          fontSize={10} fill={theme.textMuted}>
          {wrapLabel(columns[index], 13, 1).join("")}
        </text>
      ))}
      {rowIndexes.map((index) => (
        <text key={index} x={-7} y={(index + 0.5) * cellHeight}
          dominantBaseline="middle" textAnchor="end" fill={theme.textMuted} fontSize={10}>
          {wrapLabel(rows[index], 14, 1).join("")}
        </text>
      ))}
      <text x={innerWidth / 2} y={innerHeight + (rotate ? 60 : 40)}
        textAnchor="middle" fontSize={11} fill={theme.textMuted}>{data.xAxis}</text>
      <text x={-80} y={innerHeight / 2}
        textAnchor="middle" fontSize={11} fill={theme.textMuted}
        transform={`rotate(-90 -80 ${innerHeight / 2})`}>{data.yAxis}</text>
      <g className="chart-heatmap-legend" transform={`translate(${innerWidth + 14} 0)`}>
        <rect width={12} height={innerHeight} fill={`url(#${gradientId})`} />
        <text x={17} y={8} fontSize={10} fill={theme.textMuted}>{String(high)}</text>
        {diverging && <text x={17} y={innerHeight / 2 + 3} fontSize={10} fill={theme.textMuted}>0</text>}
        <text x={17} y={innerHeight} fontSize={10} fill={theme.textMuted}>{String(low)}</text>
        {data.valueUnit && <text x={0} y={innerHeight + 16} fontSize={10} fill={theme.textMuted}>{data.valueUnit}</text>}
      </g>
    </g>
  );
}
