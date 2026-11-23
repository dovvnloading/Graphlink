import { Handle, Position, useNodeId, useStore, useUpdateNodeInternals } from "@xyflow/react";
import { isValidElement, useContext, useEffect, type CSSProperties, type MouseEvent, type ReactNode, type RefObject } from "react";
import { Icon } from "../ui/icons/Icon";
import type { IconName } from "../ui/icons/registry";
import { Tooltip } from "../ui/Tooltip";
import type { MenuPosition } from "./menuPosition";
import { TEXT_INPUT_HANDLE, TEXT_OUTPUT_HANDLE } from "./textInputs";
import "./NodeCards.css";
import { StalenessBadge } from "./StalenessBadge";
import { RunBoundNodesContext } from "./RunBoundNodesContext";
import { useCanvasSearchMatchIds, useCanvasSearchQuery } from "./CanvasSearchContext";

// Size-reporting alone misses a port when a lazy node view replaces an
// equal-sized placeholder. Register the new handle after it enters the DOM.
function RefreshPorts() {
  const id = useNodeId();
  const update = useUpdateNodeInternals();
  useEffect(() => { if (id) update(id); }, [id, update]);
  return null;
}

/** Keep the icon choice in the shared shell, not in eighteen node views. */
const NODE_KIND_ICONS: readonly [string, IconName][] = [
  ["chat-node user", "node-user"],
  ["chat-node", "node-assistant"],
  ["thinking-node", "node-thinking"],
  ["code-review-node", "node-review"],
  ["code-sandbox-node", "node-runner"],
  ["code-node", "node-code"],
  ["document-node", "node-document"],
  ["html-node", "node-html"],
  ["image-node", "node-image"],
  ["fal-studio-node", "node-media"],
  ["conversation-node", "node-conversation"],
  ["web-research-node", "node-web"],
  ["artifact-node", "node-artifact"],
  ["gitlink-node", "node-git"],
  ["note-node", "node-note"],
  ["model-node", "node-model"],
  ["scientific-node", "node-science"],
  ["chart-node", "node-chart"],
  ["plan-node", "node-plan"],
  ["harness-node", "node-agent"],
  ["group-node", "node-container"],
  ["frame-node", "node-frame"],
];

export function nodeKindIcon(kindClassName: string): IconName {
  return NODE_KIND_ICONS.find(([className]) => kindClassName.startsWith(className))?.[1] ?? "node-placeholder";
}

/** Direct-render and legacy nodes may not have a React Flow title yet. */
function headingText(child: ReactNode): string {
  if (typeof child === "string" || typeof child === "number") return String(child);
  if (Array.isArray(child)) {
    for (const item of child) {
      const text = headingText(item);
      if (text) return text;
    }
  }
  if (isValidElement<{ children?: ReactNode }>(child)) return headingText(child.props.children);
  return "";
}


/** Context subscriptions live in this tiny leaf, never in a node view or
 * even NodeShell's main body. Typing changes only its data attribute. */
export function NodeSearchState({ nodeId }: { nodeId: string | null }) {
  const query = useCanvasSearchQuery();
  const matchIds = useCanvasSearchMatchIds();
  const mode = !query.trim() ? "off"
    : nodeId && matchIds.includes(nodeId) ? "match" : "dim";
  return <span className="node-search-state" data-search={mode} aria-hidden="true" />;
}

/** Shared frame. Clip card content independently of ports and resize handles. */
interface NodeShellProps {
  kindClassName: string;
  selected: boolean;
  collapsed: boolean;
  onMenuChange?: (position: MenuPosition | null) => void;
  /** Stable keyboard return target for a node-owned inline editor. */
  menuTriggerRef?: RefObject<HTMLButtonElement>;
  header: ReactNode;
  bodyClassName: string;
  children?: ReactNode;
  menu?: ReactNode;
  style?: CSSProperties;
  resizer?: ReactNode;
  textOutput?: boolean;
  textInput?: boolean;
  tablePort?: "source" | "target" | "both";
  modelPort?: boolean;
  /** Direct-render override; live canvas status is subscribed from React Flow. */
  staleness?: "current" | "out_of_date" | "upstream_changed";
  manualRerun?: boolean;
  footer?: ReactNode;
  onBodyDoubleClick?: (event: MouseEvent<HTMLDivElement>) => void;
}

export function NodeShell({
  kindClassName,
  selected,
  collapsed,
  onMenuChange,
  menuTriggerRef,
  header,
  bodyClassName,
  children,
  menu,
  style,
  resizer,
  textOutput,
  textInput,
  tablePort,
  modelPort,
  staleness,
  manualRerun,
  footer,
  onBodyDoubleClick,
}: NodeShellProps) {
  // Primitive selectors keep unrelated node updates outside this shell.
  const nodeId = useNodeId();
  const liveStaleness = useStore((state) => (
    nodeId ? state.nodeLookup.get(nodeId)?.data.staleness : undefined
  ));
  const liveTitle = useStore((state) => {
    const title = nodeId ? state.nodeLookup.get(nodeId)?.data.title : undefined;
    return typeof title === "string" ? title : undefined;
  });
  const isBoundForRunner = useStore((state) => (
    nodeId ? state.nodeLookup.get(nodeId)?.data.isBoundForRunner === true : false
  ));
  const visibleStaleness = staleness ?? liveStaleness;
  const openBoundNodes = useContext(RunBoundNodesContext);
  const icon = nodeKindIcon(kindClassName);
  const overviewTitle = liveTitle?.trim() || headingText(header) || "Node";

  return (
    <div
      className={"scene-node scene-node-card " + kindClassName +
        (selected ? " selected" : "") + (collapsed ? " collapsed" : "")}
      style={style}
      onContextMenu={onMenuChange && ((event) => {
        event.preventDefault();
        onMenuChange({ x: event.clientX, y: event.clientY });
      })}
      aria-haspopup={onMenuChange ? "menu" : undefined}
    >
      <NodeSearchState nodeId={nodeId} />
      {resizer}
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-surface">
        <div className="node-card-header">
          <Icon name={icon} size="sm" className="node-card-kind-icon" />
          <div className="node-card-title-slot">{header}</div>
          <div className="node-card-status">
            <StalenessBadge status={visibleStaleness} manualRerun={manualRerun}
              nodeKind={kindClassName === "thinking-node" ? "thinking" : undefined}
              onRunBoundNodes={isBoundForRunner && nodeId && openBoundNodes
                ? () => openBoundNodes([nodeId]) : undefined} />
          </div>
          <span className="node-card-spacer" aria-hidden="true" />
          {onMenuChange && (
            <Tooltip label="Node actions">
              <button ref={menuTriggerRef} type="button"
                className="node-card-menu-trigger gl-icon-button gl-icon-button--sm gl-icon-button--ghost nodrag"
                aria-label="Node actions" aria-haspopup="menu" aria-expanded={Boolean(menu)}
                onClick={(event) => {
                  const { left, bottom } = event.currentTarget.getBoundingClientRect();
                  onMenuChange(menu ? null : { x: left, y: bottom + 4 });
                }}>
                <Icon name="more-horizontal" size="sm" />
              </button>
            </Tooltip>
          )}
        </div>
        <div className="node-lod-overview" aria-hidden="true">
          <Icon name={icon} size="sm" className="node-lod-icon" />
          <span className="node-lod-title" data-title={overviewTitle} />
        </div>
        {!collapsed && (
          <div className={"scene-node-body " + bodyClassName} onDoubleClick={onBodyDoubleClick}>
            {children}
          </div>
        )}
        {footer && <div className="node-card-footer">{footer}</div>}
      </div>
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {textOutput && <Handle id={TEXT_OUTPUT_HANDLE} type="source" position={Position.Right}
        className="scene-text-handle" aria-label="Text output" />}
      {textInput && <Handle id={TEXT_INPUT_HANDLE} type="target" position={Position.Left}
        className="scene-text-handle" aria-label="Text input port" />}
      {tablePort && <RefreshPorts key={tablePort} />}
      {modelPort && <><RefreshPorts key="model" />
        <Handle id="model-input" type="target" position={Position.Left}
          className="scene-text-handle" aria-label="Model text input" />
      </>}
      {(tablePort === "source" || tablePort === "both") && <Handle id="table-source" type="source"
        position={Position.Right} isConnectable={false} className="scene-text-handle scene-table-handle"
        aria-label="Table output" />}
      {(tablePort === "target" || tablePort === "both") && <Handle id="table-target" type="target"
        position={Position.Left} isConnectable={false} className="scene-text-handle scene-table-handle"
        aria-label="Table input" />}
      {menu}
    </div>
  );
}
