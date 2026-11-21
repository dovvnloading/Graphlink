import { Handle, Position, useNodeId, useStore, useUpdateNodeInternals } from "@xyflow/react";
import { useContext, useEffect, type CSSProperties, type MouseEvent, type ReactNode, type RefObject } from "react";

import type { MenuPosition } from "./menuPosition";
import { TEXT_INPUT_HANDLE, TEXT_OUTPUT_HANDLE } from "./textInputs";
import "./NodeCards.css";
import { StalenessBadge } from "./StalenessBadge";
import { RunBoundNodesContext } from "./RunBoundNodesContext";

// Size-reporting alone misses a port when a lazy node view replaces an
// equal-sized placeholder. Register the new handle after it enters the DOM.
function RefreshPorts() {
  const id = useNodeId();
  const update = useUpdateNodeInternals();
  useEffect(() => { if (id) update(id); }, [id, update]);
  return null;
}

/** Shared frame. Clip the card contents independently of ports and resize handles. */
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
  // One O(1) lookup per node, and a primitive-selector subscription: only
  // the affected card re-renders when a different branch becomes stale.
  const nodeId = useNodeId();
  const liveStaleness = useStore((state) => (
    nodeId ? state.nodeLookup.get(nodeId)?.data.staleness : undefined
  ));
  const visibleStaleness = staleness ?? liveStaleness;
  const isBoundForRunner = useStore((state) => (
    nodeId ? state.nodeLookup.get(nodeId)?.data.isBoundForRunner === true : false
  ));
  const openBoundNodes = useContext(RunBoundNodesContext);
  return (
    <div
      className={`scene-node scene-node-card ${kindClassName}${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      style={style}
      onContextMenu={onMenuChange && ((event) => {
        event.preventDefault();
        onMenuChange({ x: event.clientX, y: event.clientY });
      })}
      aria-haspopup={onMenuChange ? "menu" : undefined}
    >
      {resizer}
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-surface">
        <div className="node-card-header">
          {header}
          {onMenuChange && (
            <button ref={menuTriggerRef} type="button" className="node-card-menu-trigger nodrag"
              title="Node actions" aria-label="Node actions" aria-haspopup="menu" aria-expanded={Boolean(menu)}
              onClick={(event) => {
                const { left, bottom } = event.currentTarget.getBoundingClientRect();
                onMenuChange(menu ? null : { x: left, y: bottom + 4 });
              }}>
              <svg aria-hidden="true" viewBox="0 0 16 16" fill="currentColor">
                <circle cx="3.5" cy="8" r="1.2" />
                <circle cx="8" cy="8" r="1.2" />
                <circle cx="12.5" cy="8" r="1.2" />
              </svg>
            </button>
          )}
        </div>
        <StalenessBadge status={visibleStaleness} manualRerun={manualRerun}
          onRunBoundNodes={isBoundForRunner && nodeId && openBoundNodes
            ? () => openBoundNodes([nodeId]) : undefined} />
        {!collapsed && (
          <div className={`scene-node-body ${bodyClassName}`} onDoubleClick={onBodyDoubleClick}>
            {children}
          </div>
        )}
        {footer}
      </div>
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {textOutput && <Handle id={TEXT_OUTPUT_HANDLE} type="source" position={Position.Right}
        className="scene-text-handle" title="Text output · Drag to a note’s text input" aria-label="Text output" />}
      {textInput && <Handle id={TEXT_INPUT_HANDLE} type="target" position={Position.Left}
        className="scene-text-handle" title="Text input · Connect a text output" aria-label="Text input port" />}
      {tablePort && <RefreshPorts key={tablePort} />}
      {modelPort && <><RefreshPorts key="model" />
        <Handle id="model-input" type="target" position={Position.Left}
          className="scene-text-handle"
          title="Model text input" aria-label="Model text input" />
      </>}
      {(tablePort === "source" || tablePort === "both") && <Handle id="table-source" type="source"
        position={Position.Right} isConnectable={false} className="scene-text-handle scene-table-handle"
        title="Table output" aria-label="Table output" />}
      {(tablePort === "target" || tablePort === "both") && <Handle id="table-target" type="target"
        position={Position.Left} isConnectable={false} className="scene-text-handle scene-table-handle"
        title="Table input" aria-label="Table input" />}
      {menu}
    </div>
  );
}
