import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import { readMcpState } from "./mcpConnect";
import { readWorkflowNode } from "./workflowLibrary";

export const TEXT_INPUT_HANDLE = "text-input";
export const TEXT_OUTPUT_HANDLE = "text-output";
export type TextInputOperation = SceneNodeRow["textInputOperation"];

export function isPlainNote(row: Pick<SceneNodeRow, "kind" | "isSystemPrompt" | "isSummaryNote" | "isBranchComparison" | "isTableSource">): boolean {
  return row.kind === "note" && !row.isSystemPrompt && !row.isSummaryNote && !row.isBranchComparison && !row.isTableSource;
}

export function textSourceLabel(row: SceneNodeRow): string {
  if (row.kind === "graph_runner.workflow") return "Workflow · " + row.title + " (" + row.id + ")";
  const kind = row.kind === "artifact" ? "Drafter" : row.kind === "chat" ? "Chat" : row.kind === "model" ? "Model" : row.kind === "mcp_connect.tool" ? "MCP tool" : "Note";
  const text = row.kind === "artifact" ? row.artifactContent : row.content;
  const preview = text.trimStart().slice(0, 64).split(/\r?\n/, 1)[0]?.trimEnd();
  return preview ? `${kind} · ${preview} (${row.id})` : `${kind} (${row.id})`;
}

// Exclude the destination and all its downstream notes. A structural edge
// between the same nodes has no bearing on whether a value binding is valid.
export function availableTextSources(nodes: readonly SceneNodeRow[], targetId: string): SceneNodeRow[] {
  const downstream = new Map<string, string[]>();
  for (const node of nodes) {
    const sources = node.kind === "graph_runner.workflow" ? Object.values(readWorkflowNode(node.pluginState.workflow ?? "")?.sources ?? {})
      : node.kind === "mcp_connect.tool" ? Object.values(readMcpState(node.pluginState.mcp)?.config?.bindings ?? {}).map(binding => binding.sourceId)
      : [node.kind === "model" ? (node.itemIds[0] ?? "") : node.textInputSourceId];
    for (const source of sources) {
      if (!source) continue;
      const targets = downstream.get(source);
      if (targets) targets.push(node.id);
      else downstream.set(source, [node.id]);
    }
  }
  const excluded = new Set([targetId]);
  for (const id of excluded) {
    for (const target of downstream.get(id) ?? []) excluded.add(target);
  }
  return nodes.filter((node) => !excluded.has(node.id) && (isPlainNote(node) || node.kind === "chat" || node.kind === "artifact" || node.kind === "model" || node.kind === "mcp_connect.tool"
    || (node.kind === "graph_runner.workflow" && readWorkflowNode(node.pluginState.workflow ?? "")?.document.spec.outputs.filter(o => o.port.kind === "text").length === 1)));
}
