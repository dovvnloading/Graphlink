import { Handle, Position } from "@xyflow/react";
import type { CSSProperties, MouseEvent, ReactNode } from "react";

import type { MenuPosition } from "./menuPosition";
import { TEXT_INPUT_HANDLE, TEXT_OUTPUT_HANDLE } from "./textInputs";
import "./NodeCards.css";

/** Shared frame. Clip the card contents independently of ports and resize handles. */
interface NodeShellProps {
  kindClassName: string;
  selected: boolean;
  collapsed: boolean;
  onMenuChange?: (position: MenuPosition | null) => void;
  header: ReactNode;
  bodyClassName: string;
  children?: ReactNode;
  menu?: ReactNode;
  style?: CSSProperties;
  resizer?: ReactNode;
  textOutput?: boolean;
  textInput?: boolean;
  footer?: ReactNode;
  onBodyDoubleClick?: (event: MouseEvent<HTMLDivElement>) => void;
}

export function NodeShell({
  kindClassName,
  selected,
  collapsed,
  onMenuChange,
  header,
  bodyClassName,
  children,
  menu,
  style,
  resizer,
  textOutput,
  textInput,
  footer,
  onBodyDoubleClick,
}: NodeShellProps) {
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
            <button type="button" className="node-card-menu-trigger nodrag"
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
      {menu}
    </div>
  );
}
