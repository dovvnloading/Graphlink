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
 * unscoped, not owned by any R-phase). One legacy item is still deliberately
 * NOT listed even as disabled: "Generate Group Summary" is itself
 * conditionally hidden in the legacy menu (only when a multi-selection
 * exists), and that precondition can't occur yet in the new stack (no
 * multi-select model) - showing it unconditionally would be a behavior
 * regression, not parity. "Reveal Docked Items" WAS in that same boat until
 * R3.13/R3.14 (ThinkingNode + generic docking): its precondition - one or
 * more docked children - can now be real (a thinking node docks via its own
 * "Dock to Parent Node" action), so it's implemented for real below, gated
 * on dockedChildren.length > 0 exactly like the legacy's own `if
 * docked_children:` guard. "Regenerate Response" is likewise no longer
 * deferred as of R4.3c: it now calls the real regenerateResponse intent,
 * still gated on !isUser (matching the legacy is_user guard).
 */

export interface ChatNodeData extends Record<string, unknown> {
  content: string;
  isUser: boolean;
  isCollapsed: boolean;
  dockedChildren: { id: string; label: string }[];
  onToggleCollapse: () => void;
  onDelete: () => void;
  onUndockChild: (childId: string) => void;
  onRegenerate: () => void;
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
  dockedChildren,
  onToggleCollapse,
  onDelete,
  onUndockChild,
  onRegenerate,
  onClose,
}: {
  position: MenuPosition;
  content: string;
  isUser: boolean;
  isCollapsed: boolean;
  dockedChildren: { id: string; label: string }[];
  onToggleCollapse: () => void;
  onDelete: () => void;
  onUndockChild: (childId: string) => void;
  onRegenerate: () => void;
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
      {/* Real (not disabled) - matches the legacy's own `if docked_children:`
          guard exactly. One button per docked child, each undocking that
          specific child back onto the canvas via the shared setNodeDocked
          intent (docked=false). */}
      {dockedChildren.length > 0 && (
        <>
          <div className="chat-node-menu-section-label">Reveal Docked Items</div>
          {dockedChildren.map((child) => (
            <button
              key={child.id}
              type="button"
              role="menuitem"
              onClick={() => {
                onUndockChild(child.id);
                onClose();
              }}
            >
              {child.label}
            </button>
          ))}
        </>
      )}
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
        <button
          type="button"
          role="menuitem"
          onClick={() => {
            onRegenerate();
            onClose();
          }}
        >
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
        <span className="chat-node-role-group">
          <span>{data.isUser ? "You" : "Assistant"}</span>
          {data.dockedChildren.length > 0 && (
            <span className="chat-node-docked-badge" title="Docked items">
              {data.dockedChildren.length}
            </span>
          )}
        </span>
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
          dockedChildren={data.dockedChildren}
          onToggleCollapse={data.onToggleCollapse}
          onDelete={data.onDelete}
          onUndockChild={data.onUndockChild}
          onRegenerate={data.onRegenerate}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
