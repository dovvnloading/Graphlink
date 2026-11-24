import type { SceneEdgeRow, SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import type { ToFlowNodesCache } from "./SceneCanvas";
import type { McpToolFlowNode } from "./McpToolNodeView";
import type { SceneStore } from "./sceneStore";

export function mcpFlowNode(node: SceneNodeRow, incoming: SceneEdgeRow[], nodesById: Map<string, SceneNodeRow>,
  store: SceneStore, cache: ToFlowNodesCache, opacity: number): McpToolFlowNode {
  // Position-only publications must not re-render the editor. Refresh its
  // server-validated source choices only when connected output data changes.
  const sourceRevision = JSON.stringify(incoming.map(edge => {
    const source = nodesById.get(edge.source);
    return [edge.source, source?.title, source?.kind, source?.content,
      source?.pluginState.mcp, source?.tableSourceHash, source?.tableOutputVersion];
  }));
  const extraSig = `${opacity}|${sourceRevision}`;
  const cached = cache.flowNodes.get(node);
  if (cached?.extraSig === extraSig && cached.flowNode.type === "mcp_tool" && cached.flowNode.data.store === store)
    return cached.flowNode;
  const result: McpToolFlowNode = {
    id: node.id, type: "mcp_tool", position: { x: node.x, y: node.y },
    style: opacity === 1 ? undefined : { opacity },
    data: { title: node.title, raw: node.pluginState.mcp, isCollapsed: node.isCollapsed,
      pendingRequestId: node.pendingRequestId ?? null, sourceRevision, store },
  };
  cache.flowNodes.set(node, { extraSig, flowNode: result });
  return result;
}
