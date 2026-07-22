import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";

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
 * Real: render (markdown thinking text, same react-markdown + rehype-
 * highlight pipeline every other node kind pulls in), delete (generic
 * cascade-delete - a thinking node is never a branch point/reparented, same
 * as CodeNode), copy, dock. Deferred, with an honest disabled+title label
 * rather than a silent drop (same audit convention every prior node kind in
 * this plan has followed): Hide Other Branches (the legacy scene's branch-
 * visibility toggle has no backend/frontend equivalent at all yet - unscoped,
 * not owned by any R-phase).
 */

export interface ThinkingNodeData extends Record<string, unknown> {
  thinkingText: string;
  onDock: () => void;
  onDelete: () => void;
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
  onClose,
}: {
  position: MenuPosition;
  thinkingText: string;
  onDock: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      // globalThis.Node - the DOM interface, not @xyflow/react's Node (the
      // type-only import above shadows the bare name for casts like this).
      if (!menuRef.current?.contains(event.target as globalThis.Node)) onClose();
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [onClose]);

  return (
    <div
      ref={menuRef}
      className="chat-node-menu"
      style={{ position: "fixed", left: position.x, top: position.y }}
      role="menu"
    >
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
      <button type="button" role="menuitem" disabled title="Branch visibility isn't built yet">
        Hide Other Branches
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
    </div>
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
        <div className="scene-node-body thinking-node-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {data.thinkingText}
          </ReactMarkdown>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <ThinkingNodeMenu
          position={menuPosition}
          thinkingText={data.thinkingText}
          onDock={data.onDock}
          onDelete={data.onDelete}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
