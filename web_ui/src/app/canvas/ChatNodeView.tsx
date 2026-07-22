import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";

/**
 * The chat node (Qt-removal plan R3.1/R3.2) - ChatNode's React successor:
 * a single message-bubble card, push-only (content arrives via the scene
 * document, never generated here). Real: render, collapse/expand, delete
 * (with the backend's reparent-children rule), copy. Deferred, with an
 * honest disabled+title label rather than a fake action or a silent drop
 * (an R3.4 live-drive audit found several legacy ChatNode menu items had
 * been dropped with zero acknowledgment - fixed here): Regenerate (assistant
 * nodes only, needs the R4 agent layer), Key Takeaway/Explainer Note/Chart/
 * Image generation (R4, same agent-layer blocker), Export (R6 session/export
 * work), Open Document View (the document-viewer island isn't wired into the
 * SPA overlay system yet), and Hide Other Branches (the legacy scene's
 * branch-visibility toggle has no backend/frontend equivalent at all yet -
 * unscoped, not owned by any R-phase). Two legacy items are deliberately NOT
 * listed even as disabled: "Reveal Docked Items" and "Generate Group Summary"
 * are themselves conditionally hidden in the legacy menu (only when docked
 * children or a multi-selection exist), and neither precondition can occur
 * yet in the new stack (no docking, no multi-select model) - showing them
 * unconditionally would be a behavior regression, not parity.
 */

export interface ChatNodeData extends Record<string, unknown> {
  content: string;
  isUser: boolean;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onDelete: () => void;
}

export type ChatFlowNode = Node<ChatNodeData, "chat">;

interface MenuPosition {
  x: number;
  y: number;
}

function ChatNodeMenu({
  position,
  content,
  isUser,
  isCollapsed,
  onToggleCollapse,
  onDelete,
  onClose,
}: {
  position: MenuPosition;
  content: string;
  isUser: boolean;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
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
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          navigator.clipboard.writeText(content);
          onClose();
        }}
      >
        Copy Text
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onToggleCollapse();
          onClose();
        }}
      >
        {isCollapsed ? "Expand" : "Collapse"}
      </button>
      <button type="button" role="menuitem" disabled title="Export lands in R6">
        Export
      </button>
      <button type="button" role="menuitem" disabled title="Branch visibility isn't built yet">
        Hide Other Branches
      </button>
      <button type="button" role="menuitem" disabled title="Document view integration isn't wired into the SPA yet">
        Open Document View
      </button>
      <button type="button" role="menuitem" disabled title="AI generation lands in R4">
        Generate Key Takeaway
      </button>
      <button type="button" role="menuitem" disabled title="AI generation lands in R4">
        Generate Explainer Note
      </button>
      <button type="button" role="menuitem" disabled title="AI generation lands in R4">
        Generate Chart
      </button>
      <button type="button" role="menuitem" disabled title="AI generation lands in R4">
        Generate Image
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
      {!isUser && (
        <button type="button" role="menuitem" disabled title="Agent regeneration lands in R4">
          Regenerate Response
        </button>
      )}
    </div>
  );
}

export function ChatNodeView({ data, selected }: NodeProps<ChatFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const lodCollapsed = zoom < LOD_ZOOM_THRESHOLD;
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  return (
    <div
      className={`scene-node chat-node${data.isUser ? " user" : " assistant"}${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title chat-node-role">
        <span>{data.isUser ? "You" : "Assistant"}</span>
        <button
          type="button"
          className="chat-node-collapse-btn"
          aria-label={data.isCollapsed ? "Expand" : "Collapse"}
          onClick={data.onToggleCollapse}
        >
          {data.isCollapsed ? "▸" : "▾"}
        </button>
      </div>
      {!collapsed && (
        <div className="scene-node-body chat-node-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {data.content}
          </ReactMarkdown>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <ChatNodeMenu
          position={menuPosition}
          content={data.content}
          isUser={data.isUser}
          isCollapsed={data.isCollapsed}
          onToggleCollapse={data.onToggleCollapse}
          onDelete={data.onDelete}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
