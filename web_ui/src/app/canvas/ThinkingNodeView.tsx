import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useState } from "react";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";

/**
 * The thinking node (Qt-removal plan R3.13/R3.14) - graphlink_node_thinking.py's
 * React successor: a scratch/reasoning card that always requires a parent
 * (same as DocumentNode - the backend's add_thinking_node has no default for
 * parent_id). Unlike ChatNode/DocumentNode, ThinkingNode has no manual
 * collapse toggle at all in the legacy app - only the shared zoom-based LOD
 * auto-collapse applies (mirrors CodeNodeView's collapsed-from-LOD-alone
 * pattern, not Chat/Document's manual-OR-LOD pattern).
 *
 * This increment also introduces the first REAL docking mechanic: "Dock to
 * Parent Node" calls the new generic setNodeDocked(id, true) intent, which
 * removes this node from the canvas entirely (SceneCanvas.tsx's toFlowNodes
 * filters out any node with isDocked===true, and toFlowEdges drops any edge
 * pointing at it) and surfaces it instead as a badge + menu entry on its
 * parent chat node (see ChatNodeView.tsx's dockedChildren / "Reveal Docked
 * Items"). Undocking is the parent's action, not this node's - there is no
 * "Undock" item here, matching the legacy menu (only ChatNode's menu offers
 * the reverse direction).
 *
 * Real: render (markdown thinking text, the shared NodeMarkdown.tsx renderer
 * every other node kind now uses - node redesign stage 1), delete (generic
 * cascade-delete - a thinking node is never a branch point/reparented, same
 * as CodeNode), copy, dock, and now Hide Other Branches / Show All Branches -
 * a scene-wide toggle (SceneCanvas.tsx owns the graph walk and the dimming
 * style; this view just calls the closed-over onToggleBranchFocus() and
 * mirrors isBranchFocusActive back into the menu item's own label).
 */

export interface ThinkingNodeData extends Record<string, unknown> {
  thinkingText: string;
  onDock: () => void;
  onDelete: () => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
}

export type ThinkingFlowNode = Node<ThinkingNodeData, "thinking">;

interface MenuPosition {
  x: number;
  y: number;
}

function ThinkingNodeMenu({
  position,
  thinkingText,
  onDock,
  onDelete,
  isBranchFocusActive,
  onToggleBranchFocus,
  onClose,
}: {
  position: MenuPosition;
  thinkingText: string;
  onDock: () => void;
  onDelete: () => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
  onClose: () => void;
}) {


  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
      {/* Order verified against graphlink_node_thinking_menu.py's own
          construction order: Copy Content, Dock to Parent Node, Hide Other
          Branches, Delete Node. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          navigator.clipboard.writeText(thinkingText);
          onClose();
        }}
      >
        Copy Content
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onDock();
          onClose();
        }}
      >
        Dock to Parent Node
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onToggleBranchFocus();
          onClose();
        }}
      >
        {isBranchFocusActive ? "Show All Branches" : "Hide Other Branches"}
      </button>
      <button
        type="button"
        role="menuitem"
        className="chat-node-menu-danger"
        onClick={() => {
          onDelete();
          onClose();
        }}
      >
        Delete Node
      </button>
    </NodeMenu>
  );
}

export function ThinkingNodeView({ data, selected }: NodeProps<ThinkingFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const collapsed = zoom < LOD_ZOOM_THRESHOLD;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  return (
    <div
      className={`scene-node thinking-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title thinking-node-label">
        <span>Thinking</span>
      </div>
      {!collapsed && (
        <div className="scene-node-body thinking-node-content chat-node-content">
          <NodeMarkdown content={data.thinkingText} />
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <ThinkingNodeMenu
          position={menuPosition}
          thinkingText={data.thinkingText}
          onDock={data.onDock}
          onDelete={data.onDelete}
          isBranchFocusActive={data.isBranchFocusActive}
          onToggleBranchFocus={data.onToggleBranchFocus}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
