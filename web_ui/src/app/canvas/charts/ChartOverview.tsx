import { memo } from "react";
import type { ChartDataRow } from "../../../lib/bridge-core/generated/scene-state";
import { canonicalizeChartSpec, ChartSpecError } from "../chartSpec";
import { computeHistogramBins, createLinearScale, formatValue } from "./chartScales";
import { computeSankeyLayout, sankeyFlowPath } from "./sankeyLayout";
import { buildSlices } from "./pieSlices";
import { FALLBACK_CHART_THEME } from "./chartTheme";

const WIDTH = 240;
const HEIGHT = 116;
const PALETTE = FALLBACK_CHART_THEME.categorical;

function overviewMarks(spec: ChartDataRow) {
  if (spec.type === "sankey") {
    const { nodes, flows } = computeSankeyLayout(spec.flows ?? []);
    return (
      <g transform={`scale(${WIDTH} ${HEIGHT})`}>
        {flows.map((flow) => <path key={JSON.stringify([flow.source, flow.target])}
          d={sankeyFlowPath(flow)} fill="currentColor" opacity=".4" />)}
        {nodes.map((node) => <rect key={node.name}
          x={node.x} y={node.y} width={node.width} height={node.height}
          fill={PALETTE[node.level % PALETTE.length]} />)}
      </g>
    );
  }

  const values = spec.values ?? [];
  if (spec.type === "pie") {
    return buildSlices(spec.labels ?? [], values).map((slice, index) => {
      const start = slice.startAngle / 3.6;
      const length = (slice.endAngle - slice.startAngle) / 3.6;
      return <circle key={index} cx="0" cy="0" r="41" fill="none" strokeWidth="19"
        transform={`translate(${WIDTH / 2} ${HEIGHT / 2}) rotate(-90)`}
        pathLength="100" strokeDasharray={`${length} ${100 - length}`} strokeDashoffset={-start}
        stroke={PALETTE[index % PALETTE.length]} />;
    });
  }

  const series = spec.type === "histogram"
    ? computeHistogramBins(values, spec.bins ?? 10).map((bin) => bin.count)
    : values;
  const isLine = spec.type === "line";
  const low = isLine ? Math.min(...series) : Math.min(0, ...series);
  const high = isLine ? Math.max(...series) : Math.max(0, ...series);
  const y = createLinearScale([low, high], [HEIGHT - 12, 10]);
  const slot = (WIDTH - 12) / series.length;
  const baseline = y(0);
  const textProps = { fill: "var(--gl-surface-text-secondary)", fontSize: 8, textAnchor: "end" } as const;

  return (
    <>
      <path d={`M6 10h${WIDTH - 12} M6 ${HEIGHT - 12}h${WIDTH - 12}`}
        fill="none" stroke="var(--gl-graph-border)" strokeDasharray="3 4" strokeWidth=".65" />
      {isLine ? (
        <polyline
          points={series.map((value, index) => {
            const x = 6 + index * (WIDTH - 12) / Math.max(1, series.length - 1);
            return `${x},${y(value)}`;
          }).join(" ")}
          fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
        />
      ) : (
        series.map((value, index) => (
          <rect key={index} x={6 + slot * (index + .12)} y={Math.min(y(value), baseline)}
            width={slot * .76} height={Math.abs(y(value) - baseline)} fill="currentColor" />
        ))
      )}
      <text x={WIDTH - 6} y="7" {...textProps}>{formatValue(high)}</text>
      {low !== high && <text x={WIDTH - 6} y={HEIGHT - 2} {...textProps}>{formatValue(low)}</text>}
    </>
  );
}

/** Actual series geometry at map scale. No tooltip, zoom listeners,
 * per-point targets, or size/theme observers. A viewBox keeps it printable. */
const ChartOverview = memo(function ChartOverview({ chartData, chartType }: {
  chartData: ChartDataRow;
  chartType: string;
}) {
  let spec: ChartDataRow;
  try {
    spec = canonicalizeChartSpec(chartData, chartType);
  } catch (error) {
    return <div className="chart-canvas-wrapper chart-canvas-placeholder">
      {error instanceof ChartSpecError ? error.message : "Chart unavailable"}
    </div>;
  }
  return (
    <div className="chart-canvas-wrapper chart-overview">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${spec.title} (${spec.type} chart)`}>
        {overviewMarks(spec)}
      </svg>
    </div>
  );
});

export default ChartOverview;
