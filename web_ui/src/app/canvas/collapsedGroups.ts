/** Transitive visibility rule for collapsed semantic frames/containers.
 *
 * Collapse hides every descendant card (and nested group) without editing
 * its persisted membership or positioning. A node with dual membership is
 * hidden when either enclosing group collapses. Cycles/dangling legacy IDs
 * are ignored safely, never allowed to spin through rendering.
 */
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";

type GroupNodeRow = Pick<SceneNodeRow, "id" | "kind" | "isCollapsed" | "itemIds">;

export function hiddenGroupMemberIds(nodes: readonly GroupNodeRow[]): Set<string> {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const stack = nodes.flatMap((node) =>
    (node.kind === "frame" || node.kind === "container") && node.isCollapsed
      ? node.itemIds : [],
  );
  const hidden = new Set<string>();
  while (stack.length > 0) {
    const id = stack.pop()!;
    if (hidden.has(id)) continue;
    const node = byId.get(id);
    if (!node) continue;
    hidden.add(id);
    if (node.kind === "frame" || node.kind === "container") {
      stack.push(...node.itemIds);
    }
  }
  return hidden;
}
