/**
 * ADR-013 stage 13.2: sankey marks - renders the layout sankeyLayout.ts
 * computes (itself a port of graphlink_chart_rendering.py's own algorithm)
 * as SVG: colored node blocks plus curved, variable-width flow ribbons
 * blended between their endpoint colors, matching the backend PNG's design.
 */

import { useMemo } from "react";
import type { ChartMarksProps } from "./BarChartMarks";
import { computeSankeyLayout, sankeyFlowPath, type SankeyFlowLayout, type SankeyNodeLayout } from "./sankeyLayout";
import { formatValue, wrapLabel } from "./chartScales";
import { tooltipPositionFromEvent } from "./chartLayout";

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  return [parseInt(clean.slice(0, 2), 16), parseInt(clean.slice(2, 4), 16), parseInt(clean.slice(4, 6), 16)];
}

function blendHex(colorA: string, colorB: string, ratio: number): string {
  const [ra, ga, ba] = hexToRgb(colorA);
  const [rb, gb, bb] = hexToRgb(colorB);
  const mix = (a: number, b: number) => Math.round(a * (1 - ratio) + b * ratio);
  return `rgb(${mix(ra, rb)}, ${mix(ga, gb)}, ${mix(ba, bb)})`;
}

function scaleNode(node: SankeyNodeLayout, width: number, height: number): SankeyNodeLayout {
  return { ...node, x: node.x * width, y: node.y * height, width: node.width * width, height: node.height * height };
}

function scaleFlow(flow: SankeyFlowLayout, width: number, height: number): SankeyFlowLayout {
  return {
    ...flow,
    x0: flow.x0 * width,
    x1: flow.x1 * width,
    control: flow.control * width,
    startY0: flow.startY0 * height,
    startY1: flow.startY1 * height,
    endY0: flow.endY0 * height,
    endY1: flow.endY1 * height,
  };
}

export function SankeyChartMarks({ chartData, theme, width, height, onHover, wrapperRef }: ChartMarksProps) {
  const flows = chartData.flows ?? [];
  const layout = useMemo(() => computeSankeyLayout(chartData.flows ?? []), [chartData.flows]);
  if (flows.length === 0 || width <= 0 || height <= 0) return null;

  const nodes = layout.nodes.map((node) => scaleNode(node, width, height));
  const scaledFlows = layout.flows.map((flow) => scaleFlow(flow, width, height));
  const colorByNode = new Map(nodes.map((node) => [node.name, theme.categorical[node.level % theme.categorical.length]]));

  const throughput = new Map<string, { inflow: number; outflow: number }>();
  for (const flow of flows) {
    const source = throughput.get(flow.source) ?? { inflow: 0, outflow: 0 };
    source.outflow += flow.value;
    throughput.set(flow.source, source);
    const target = throughput.get(flow.target) ?? { inflow: 0, outflow: 0 };
    target.inflow += flow.value;
    throughput.set(flow.target, target);
  }

  return (
    <g>
      {scaledFlows.map((flow, index) => {
        const color = blendHex(colorByNode.get(flow.source) ?? theme.categorical[0], colorByNode.get(flow.target) ?? theme.categorical[0], 0.5);
        return (
          <path
            key={`${flow.source}-${flow.target}-${index}`}
            d={sankeyFlowPath(flow)}
            fill={color}
            fillOpacity={0.5}
            stroke={color}
            strokeOpacity={0.35}
            onPointerMove={(event) => {
              const position = tooltipPositionFromEvent(event, wrapperRef);
              onHover({ title: `${flow.source} → ${flow.target}`, rows: [{ label: "Flow", value: formatValue(flow.value) }], ...position });
            }}
            onPointerLeave={() => onHover(null)}
          />
        );
      })}
      {nodes.map((node) => {
        const isLastColumn = node.level === layout.maxLevel;
        const labelX = isLastColumn ? node.x - 10 : node.x + node.width + 10;
        const lines = wrapLabel(node.name, 16, 3);
        const flowStats = throughput.get(node.name);
        return (
          <g key={node.name}>
            <rect
              x={node.x}
              y={node.y}
              width={node.width}
              height={Math.max(1, node.height)}
              fill={colorByNode.get(node.name)}
              stroke={theme.text}
              strokeOpacity={0.28}
              rx={2}
              onPointerMove={(event) => {
                const position = tooltipPositionFromEvent(event, wrapperRef);
                const rows = [];
                if (flowStats?.outflow) rows.push({ label: "Out", value: formatValue(flowStats.outflow) });
                if (flowStats?.inflow) rows.push({ label: "In", value: formatValue(flowStats.inflow) });
                onHover({ title: node.name, rows, ...position });
              }}
              onPointerLeave={() => onHover(null)}
            />
            <text
              x={labelX}
              y={node.y + node.height / 2}
              textAnchor={isLastColumn ? "end" : "start"}
              dominantBaseline="middle"
              fontSize={10}
              fill={theme.text}
            >
              {lines.map((line, lineIndex) => (
                <tspan key={line} x={labelX} dy={lineIndex === 0 ? -((lines.length - 1) * 6) : 12}>
                  {line}
                </tspan>
              ))}
            </text>
          </g>
        );
      })}
    </g>
  );
}
