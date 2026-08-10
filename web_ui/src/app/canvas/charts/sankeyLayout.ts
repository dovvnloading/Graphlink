/**
 * ADR-013 stage 13.2: sankey column/port layout, ported from
 * graphlink_chart_rendering.py's _render_sankey_chart (the proven-correct
 * longest-path leveling + proportional port-stacking algorithm the backend
 * PNG renderer already uses) rather than inventing a fresh one or pulling in
 * a layout library. Operates in the SAME normalized [0, 1] x [0, 1] space
 * the Python version does - a pure function of the flow list alone, with no
 * opinion on actual pixel size - so a caller just multiplies by its real
 * width/height. Kept framework-free for direct unit testing (see this
 * file's sibling chartScales.ts for the same posture).
 */

import type { ChartFlowRow } from "../../../lib/bridge-core/generated/scene-state";

export interface SankeyNodeLayout {
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  level: number;
}

export interface SankeyFlowLayout {
  source: string;
  target: string;
  value: number;
  x0: number;
  x1: number;
  startY0: number;
  startY1: number;
  endY0: number;
  endY1: number;
  control: number;
}

export interface SankeyLayout {
  nodes: SankeyNodeLayout[];
  flows: SankeyFlowLayout[];
  maxLevel: number;
}

const COLUMN_GAP = 0.03;
const CHART_HEIGHT = 0.82;
const X_START = 0.08;
const X_END = 0.92;
const FALLBACK_SCALE = 0.04;
const MIN_FLOW_THICKNESS = 0.008;

export function computeSankeyLayout(flows: ChartFlowRow[]): SankeyLayout {
  const outgoing = new Map<string, ChartFlowRow[]>();
  const incoming = new Map<string, ChartFlowRow[]>();
  const indegree = new Map<string, number>();
  const nodes = new Set<string>();

  for (const flow of flows) {
    nodes.add(flow.source);
    nodes.add(flow.target);
    if (!outgoing.has(flow.source)) outgoing.set(flow.source, []);
    outgoing.get(flow.source)!.push(flow);
    if (!incoming.has(flow.target)) incoming.set(flow.target, []);
    incoming.get(flow.target)!.push(flow);
    indegree.set(flow.target, (indegree.get(flow.target) ?? 0) + 1);
    if (!indegree.has(flow.source)) indegree.set(flow.source, 0);
  }

  const levels = new Map<string, number>();
  const queue = [...nodes].filter((node) => (indegree.get(node) ?? 0) === 0).sort();
  for (const node of queue) levels.set(node, 0);

  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const node = queue[cursor];
    for (const flow of outgoing.get(node) ?? []) {
      const nextLevel = Math.max(levels.get(flow.target) ?? 0, (levels.get(node) ?? 0) + 1);
      levels.set(flow.target, nextLevel);
      indegree.set(flow.target, (indegree.get(flow.target) ?? 0) - 1);
      if (indegree.get(flow.target) === 0) queue.push(flow.target);
    }
  }
  for (const node of nodes) {
    if (!levels.has(node)) {
      const fallback = (incoming.get(node) ?? []).reduce(
        (max, flow) => Math.max(max, (levels.get(flow.source) ?? 0) + 1),
        0,
      );
      levels.set(node, fallback);
    }
  }

  const nodeWeights = new Map<string, number>();
  const columnMap = new Map<number, string[]>();
  for (const node of nodes) {
    const outWeight = (outgoing.get(node) ?? []).reduce((sum, flow) => sum + flow.value, 0);
    const inWeight = (incoming.get(node) ?? []).reduce((sum, flow) => sum + flow.value, 0);
    nodeWeights.set(node, Math.max(outWeight, inWeight, 1.0));
    const level = levels.get(node) ?? 0;
    if (!columnMap.has(level)) columnMap.set(level, []);
    columnMap.get(level)!.push(node);
  }

  const availableColumns = columnMap.size ? Math.max(...columnMap.keys()) + 1 : 1;
  let globalScale: number | null = null;
  for (const columnNodes of columnMap.values()) {
    const totalWeight = columnNodes.reduce((sum, node) => sum + (nodeWeights.get(node) ?? 0), 0);
    const availableHeight = CHART_HEIGHT - COLUMN_GAP * Math.max(0, columnNodes.length - 1);
    if (totalWeight <= 0 || availableHeight <= 0) continue;
    const columnScale = availableHeight / totalWeight;
    globalScale = globalScale === null ? columnScale : Math.min(globalScale, columnScale);
  }
  const scale = globalScale ?? FALLBACK_SCALE;

  const step = (X_END - X_START) / Math.max(1, availableColumns - 1);
  const nodeWidth = Math.min(0.05, step * 0.28);
  const nodeLayout = new Map<string, SankeyNodeLayout>();

  for (const level of [...columnMap.keys()].sort((a, b) => a - b)) {
    const columnNodes = [...columnMap.get(level)!].sort((a, b) => {
      const weightDiff = (nodeWeights.get(b) ?? 0) - (nodeWeights.get(a) ?? 0);
      return weightDiff !== 0 ? weightDiff : a.toLowerCase().localeCompare(b.toLowerCase());
    });
    const totalHeight =
      columnNodes.reduce((sum, node) => sum + (nodeWeights.get(node) ?? 0) * scale, 0) +
      COLUMN_GAP * Math.max(0, columnNodes.length - 1);
    let currentY = 0.5 - totalHeight / 2;
    for (const node of columnNodes) {
      const height = (nodeWeights.get(node) ?? 0) * scale;
      nodeLayout.set(node, { name: node, x: X_START + level * step, y: currentY, width: nodeWidth, height, level });
      currentY += height + COLUMN_GAP;
    }
  }

  const outgoingCursor = new Map<string, number>();
  const incomingCursor = new Map<string, number>();
  for (const [node, layout] of nodeLayout) {
    outgoingCursor.set(node, layout.y);
    incomingCursor.set(node, layout.y);
  }
  const maxLevel = Math.max(0, ...[...nodeLayout.values()].map((layout) => layout.level));

  const sortedFlows = [...flows].sort((a, b) => {
    const sourceA = nodeLayout.get(a.source)!;
    const sourceB = nodeLayout.get(b.source)!;
    if (sourceA.level !== sourceB.level) return sourceA.level - sourceB.level;
    if (sourceA.y !== sourceB.y) return sourceA.y - sourceB.y;
    return nodeLayout.get(a.target)!.y - nodeLayout.get(b.target)!.y;
  });

  const flowLayouts: SankeyFlowLayout[] = sortedFlows.map((flow) => {
    const sourceLayout = nodeLayout.get(flow.source)!;
    const targetLayout = nodeLayout.get(flow.target)!;
    const thickness = Math.max(flow.value * scale, MIN_FLOW_THICKNESS);

    const startY0 = outgoingCursor.get(flow.source)!;
    const startY1 = startY0 + thickness;
    outgoingCursor.set(flow.source, startY1);

    const endY0 = incomingCursor.get(flow.target)!;
    const endY1 = endY0 + thickness;
    incomingCursor.set(flow.target, endY1);

    const x0 = sourceLayout.x + sourceLayout.width;
    const x1 = targetLayout.x;
    const control = Math.max(0.05, (x1 - x0) * 0.45);

    return { source: flow.source, target: flow.target, value: flow.value, x0, x1, startY0, startY1, endY0, endY1, control };
  });

  return { nodes: [...nodeLayout.values()], flows: flowLayouts, maxLevel };
}

/** Builds the same 8-point ribbon path _render_sankey_chart draws (two
 * cubic Bezier curves for the top and bottom edges of a variable-width
 * flow, closed into one filled shape), as an SVG path `d` string. Callers
 * pass already-pixel-scaled coordinates (this function does no scaling of
 * its own). */
export function sankeyFlowPath(flow: SankeyFlowLayout): string {
  const { x0, x1, startY0, startY1, endY0, endY1, control } = flow;
  return [
    `M ${x0} ${startY0}`,
    `C ${x0 + control} ${startY0} ${x1 - control} ${endY0} ${x1} ${endY0}`,
    `L ${x1} ${endY1}`,
    `C ${x1 - control} ${endY1} ${x0 + control} ${startY1} ${x0} ${startY1}`,
    "Z",
  ].join(" ");
}
