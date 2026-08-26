/** The inline header collapse/expand chevron every content-card node kind
 * renders identically (Chat/Artifact/CodeSandbox/Conversation/Document/
 * Gitlink/Plan/WebResearch) - extracted from 8 byte-for-byte copies of the
 * same button so the markup can no longer drift between kinds. */
export function CollapseToggleButton({
  isCollapsed,
  onToggleCollapse,
}: {
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}) {
  return (
    <button
      type="button"
      className="chat-node-collapse-btn"
      aria-label={isCollapsed ? "Expand" : "Collapse"}
      onClick={onToggleCollapse}
    >
      {isCollapsed ? "▸" : "▾"}
    </button>
  );
}
