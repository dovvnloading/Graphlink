import { Handle, Position } from "@xyflow/react";
import type { CSSProperties, MouseEvent, ReactNode } from "react";

import type { MenuPosition } from "./menuPosition";
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
      </div>
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menu}
    </div>
  );
}
