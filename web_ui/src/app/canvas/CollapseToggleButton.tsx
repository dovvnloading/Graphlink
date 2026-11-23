import { IconButton } from "../ui/IconButton";

/** Shared content-card disclosure; visual controls come from the kit. */
export function CollapseToggleButton({
  isCollapsed,
  onToggleCollapse,
}: {
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}) {
  return (
    <IconButton
      icon={isCollapsed ? "chevron-right" : "chevron-down"}
      label={isCollapsed ? "Expand" : "Collapse"}
      size="sm"
      className="chat-node-collapse-btn nodrag"
      aria-expanded={!isCollapsed}
      onClick={onToggleCollapse}
    />
  );
}
