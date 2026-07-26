/**
 * Ctrl+Arrow branch navigation (Qt-removal plan R7.5c) - a pure port of
 * graphlink_window_navigation.py's _navigate_up/_navigate_down/_navigate_left/
 * _navigate_right (lines 65-91) plus their shared _get_single_selected_node
 * filter (graphlink_window.py:1419-1422). Framework-free so the whole
 * traversal is unit-testable without mounting a canvas - same posture as
 * smartGuides.ts / scaleDragPosition / toFlowNodes.
 *
 * Ported semantics, each confirmed against the legacy source:
 * - Navigation only STARTS from a single selected node whose kind is one of
 *   the three legacy "navigable" classes (ChatNode/ConversationNode/
 *   HtmlViewNode). Multi-selection, no selection, or any other kind -> no-op.
 * - Targets are restricted to those same kinds. This is the faithful
 *   translation of legacy's `children` LIST, which only ever contained those
 *   three classes: code/document/image/thinking/chart/note nodes are real
 *   EDGE targets in this backend but were never in legacy's children graph
 *   (confirmed: CodeNode has no `children` attribute at all), so they stay
 *   unreachable by Ctrl+Arrow here too.
 * - Up -> parent. Down -> the LEFTMOST child by x-position (legacy sorts
 *   children by pos().x() on every keypress, so order is purely visual, not
 *   creation order). Left/Right -> previous/next sibling in that same
 *   x-sorted order, and both REQUIRE a parent (legacy guards on
 *   current.parent_node).
 * - Every boundary is a pure no-op: no wrap-around, no beep, no message.
 *   No parent -> Up/Left/Right do nothing; no children -> Down does nothing;
 *   leftmost/rightmost sibling -> Left/Right do nothing.
 *
 * Ties in x fall back to insertion order because both legacy's Python
 * `sorted` and JS `Array.prototype.sort` are stable.
 */

export type TreeNavigationDirection = "up" | "down" | "left" | "right";

/** The three legacy classes that carry a `children`/`parent_node` graph. */
const NAVIGABLE_KINDS = new Set(["chat", "conversation", "html"]);

export interface TreeNavigationNode {
  id: string;
  kind: string;
  x: number;
}

export interface TreeNavigationEdge {
  source: string;
  target: string;
}

export interface TreeNavigationScene {
  nodes: TreeNavigationNode[];
  edges: TreeNavigationEdge[];
}

function navigableById(scene: TreeNavigationScene): Map<string, TreeNavigationNode> {
  const map = new Map<string, TreeNavigationNode>();
  for (const node of scene.nodes) {
    if (NAVIGABLE_KINDS.has(node.kind)) map.set(node.id, node);
  }
  return map;
}

/** The navigable parent of `id`, or null. A node has at most one parent edge
 * in this model; if several exist the first is taken, matching legacy's
 * single `parent_node` reference. */
function parentOf(
  scene: TreeNavigationScene,
  navigable: Map<string, TreeNavigationNode>,
  id: string,
): TreeNavigationNode | null {
  for (const edge of scene.edges) {
    if (edge.target !== id) continue;
    const parent = navigable.get(edge.source);
    if (parent) return parent;
  }
  return null;
}

/** Navigable children of `id`, sorted leftmost-first by x - legacy's
 * `sorted(current.children, key=lambda c: c.pos().x())`. */
function childrenOf(
  scene: TreeNavigationScene,
  navigable: Map<string, TreeNavigationNode>,
  id: string,
): TreeNavigationNode[] {
  const children: TreeNavigationNode[] = [];
  for (const edge of scene.edges) {
    if (edge.source !== id) continue;
    const child = navigable.get(edge.target);
    if (child) children.push(child);
  }
  return children.sort((a, b) => a.x - b.x);
}

/**
 * The id of the node Ctrl+<direction> should move selection to, or null when
 * legacy would no-op. `selectedNodeId` is the single currently-selected node
 * (null for none, and callers must pass null for a multi-selection - see
 * legacy's exactly-one-item filter).
 */
export function resolveTreeNavigationTarget(
  scene: TreeNavigationScene,
  selectedNodeId: string | null,
  direction: TreeNavigationDirection,
): string | null {
  if (!selectedNodeId) return null;
  const navigable = navigableById(scene);
  const current = navigable.get(selectedNodeId);
  if (!current) return null;

  if (direction === "up") {
    return parentOf(scene, navigable, current.id)?.id ?? null;
  }

  if (direction === "down") {
    return childrenOf(scene, navigable, current.id)[0]?.id ?? null;
  }

  // left/right: siblings are the PARENT's x-sorted children, so a parentless
  // (root) node has no siblings to move between - legacy guards on
  // current.parent_node before even building the list.
  const parent = parentOf(scene, navigable, current.id);
  if (!parent) return null;
  const siblings = childrenOf(scene, navigable, parent.id);
  const index = siblings.findIndex((s) => s.id === current.id);
  if (index === -1) return null;
  const target = direction === "left" ? siblings[index - 1] : siblings[index + 1];
  return target?.id ?? null;
}
