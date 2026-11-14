import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";

export const TEXT_INPUT_HANDLE = "text-input";
export const TEXT_OUTPUT_HANDLE = "text-output";
export type TextInputOperation = SceneNodeRow["textInputOperation"];

export function isPlainNote(row: Pick<SceneNodeRow, "kind" | "isSystemPrompt" | "isSummaryNote" | "isBranchComparison" | "isTableSource">): boolean {
  return row.kind === "note" && !row.isSystemPrompt && !row.isSummaryNote && !row.isBranchComparison && !row.isTableSource;
}

export function textSourceLabel(row: SceneNodeRow): string {
  const kind = row.kind === "artifact" ? "Drafter" : row.kind === "chat" ? "Chat" : "Note";
  const text = row.kind === "artifact" ? row.artifactContent : row.content;
  const preview = text.trimStart().slice(0, 64).split(/\r?\n/, 1)[0]?.trimEnd();
  return preview ? `${kind} · ${preview} (${row.id})` : `${kind} (${row.id})`;
}

// Exclude the destination and all its downstream notes. A structural edge
// between the same nodes has no bearing on whether a value binding is valid.
export function availableTextSources(nodes: readonly SceneNodeRow[], targetId: string): SceneNodeRow[] {
  const downstream = new Map<string, string[]>();
  for (const node of nodes) {
    if (!node.textInputSourceId) continue;
    const targets = downstream.get(node.textInputSourceId);
    if (targets) targets.push(node.id);
    else downstream.set(node.textInputSourceId, [node.id]);
  }
  const excluded = new Set([targetId]);
  for (const id of excluded) {
    for (const target of downstream.get(id) ?? []) excluded.add(target);
  }
  return nodes.filter((node) => !excluded.has(node.id) && (isPlainNote(node) || node.kind === "chat" || node.kind === "artifact"));
}
