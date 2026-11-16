import type { SceneEdgeRow, SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import type { ToFlowNodesCache } from "./SceneCanvas";
import type { ScientificFlowNode } from "./ScientificNodeView";
import type { SceneStore } from "./sceneStore";
import type { ScienceParent } from "./scientificWorkbench";

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
  const cached = cache.flowNodes.get(node);
  const previous = cached?.flowNode;
  if (cached?.extraSig === String(opacity) && previous?.type === "scientific" && previous.data.store === store &&
      parents.length === previous.data.parents.length && parents.every((p, i) => {
        const old = previous.data.parents[i];
        return p.nodeId === old.nodeId && p.title === old.title && p.raw === old.raw;
      })) return previous;
  const flowNode: ScientificFlowNode = {
    id: node.id, type: "scientific", position: { x: node.x, y: node.y },
    style: opacity === 1 ? undefined : { opacity },
    data: { title: node.title, raw: node.pluginState.science, pluginId: node.kind.split(".")[0], parents, store },
  };
  cache.flowNodes.set(node, { extraSig: String(opacity), flowNode });
  return flowNode;
}
