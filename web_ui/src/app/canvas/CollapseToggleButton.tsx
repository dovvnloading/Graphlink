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
      className="chat-node-collapse-btn nodrag"
      aria-expanded={!isCollapsed}
      title={isCollapsed ? "Expand" : "Collapse"}
      aria-label={isCollapsed ? "Expand" : "Collapse"}
      onClick={onToggleCollapse}
    >
      <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor"
        strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d={isCollapsed ? "m6.5 4.5 3.5 3.5-3.5 3.5" : "m4.5 6.5 3.5 3.5 3.5-3.5"} />
      </svg>
    </button>
  );
}
