import type { SceneEdgeRow, SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import type { ToFlowNodesCache } from "./SceneCanvas";
import type { ScientificFlowNode } from "./ScientificNodeView";
import type { SceneStore } from "./sceneStore";
import { readScienceState, type ScienceChartLink, type ScienceChartType, type ScienceParent } from "./scientificWorkbench";

export function isScienceKind(kind: string): boolean {
  return /^science_[a-z0-9_]+\.workbench$/.test(kind);
}

export function scientificFlowNode(node: SceneNodeRow, incoming: SceneEdgeRow[], nodesById: Map<string, SceneNodeRow>,
  store: SceneStore, cache: ToFlowNodesCache, opacity: number): ScientificFlowNode {
  const parents: ScienceParent[] = [];
  const seen = new Set<string>();
  for (const edge of incoming) {
    const parent = nodesById.get(edge.source);
    if (parent && parent.id !== node.id && isScienceKind(parent.kind) && !seen.has(parent.id)) {
      seen.add(parent.id);
      parents.push({ nodeId: parent.id, title: parent.title, raw: parent.pluginState.science });
    }
  }
  // Like table-driven output freshness, compare the chart's snapshotted
  // source receipt against the *live* result on each scene publication.
  // We retain the chart node as the source of truth for its own last draw.
  const state = readScienceState(node.pluginState.science);
  const sourceReceipt = state?.latest?.provenance.receiptHash ?? state?.latest?.provenance.resultHash;
  const charts: ScienceChartLink[] = [];
  for (const chart of nodesById.values()) {
    if (chart.kind !== "chart" || chart.chartSourceNodeId !== node.id) continue;
    const receipt = (chart.chartData as unknown as Record<string, unknown>).scienceReceipt;
    if (typeof receipt !== "object" || !receipt || Array.isArray(receipt)) continue;
    const ref = receipt as Record<string, unknown>;
    if (ref.sourceKey !== state?.nodeKey || typeof ref.receiptHash !== "string") continue;
    const chartType = chart.chartType as ScienceChartType;
    if (!["scatter", "line", "boxplot", "heatmap"].includes(chartType)) continue;
    charts.push({
      id: chart.id, title: chart.chartData.title || chart.title,
      chartType, receiptHash: ref.receiptHash,
      stale: !sourceReceipt || ref.receiptHash !== sourceReceipt
        || ref.resultHash !== state?.latest?.provenance.resultHash,
    });
  }
  charts.sort((a, b) => a.id.localeCompare(b.id));
  const extraSig = String(opacity) + "|" +
    charts.map((c) => [c.id, c.title, c.chartType, c.receiptHash, c.stale].join(":")).join("|");
  const cached = cache.flowNodes.get(node);
  const previous = cached?.flowNode;
  if (cached?.extraSig === extraSig && previous?.type === "scientific" && previous.data.store === store &&
      parents.length === previous.data.parents.length && parents.every((p, i) => {
        const old = previous.data.parents[i];
        return p.nodeId === old.nodeId && p.title === old.title && p.raw === old.raw;
      })) return previous;
  const flowNode: ScientificFlowNode = {
    id: node.id, type: "scientific", position: { x: node.x, y: node.y },
    style: opacity === 1 ? undefined : { opacity },
    data: { title: node.title, raw: node.pluginState.science, pluginId: node.kind.split(".")[0], parents, charts, store },
  };
  cache.flowNodes.set(node, { extraSig, flowNode });
  return flowNode;
}
