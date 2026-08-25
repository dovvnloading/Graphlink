/** ADR-011 stage 11.6 dedup: this exact shape - `{ x: number; y: number }` -
 * was independently re-declared as a local `interface MenuPosition` in 11
 * *NodeView.tsx files (ArtifactNodeView, ChatNodeView, CodeNodeView,
 * CodeSandboxNodeView, ConversationNodeView, DocumentNodeView,
 * GitlinkNodeView, ImageNodeView, ThinkingNodeView,
 * WebResearchNodeView), byte-identical in every one. Single shared home for
 * the "where the right-click/kebab card menu should render" coordinate pair
 * those files store in local state (e.g. `useState<MenuPosition | null>`). */
export interface MenuPosition {
  x: number;
  y: number;
}
