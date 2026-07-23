import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";

/**
 * The artifact node (Qt-removal plan R5.2) - the Artifact/Drafter plugin's
 * React card. One vertical card, same overall shell as its plugin-node
 * siblings (ConversationNodeView/WebResearchNodeView): collapse/expand OR-ed
 * with LOD, a card menu with outside-click/Escape dismiss, the shared
 * react-markdown + remarkGfm + rehypeHighlight pipeline. Deliberately NOT a
 * literal two-pane split-view clone of the legacy widget - every prior
 * node/plugin port in this codebase re-composes the legacy widget's layout
 * into one card instead, and this follows that same convention: a document
 * preview on top, the instruction turn history below it, then the
 * instruction input at the bottom.
 *
 * Security note, not a style preference: this reuses react-markdown/
 * remarkGfm/rehypeHighlight UNCHANGED - no rehype-raw, no
 * dangerouslySetInnerHTML anywhere in this file. Without rehype-raw,
 * react-markdown never interprets embedded HTML in either the document
 * preview or a turn bubble as live markup; it renders as inert text. That is
 * the same raw-text-can-never-become-a-live-DOM-element guarantee the legacy
 * app's escape-then-render pipeline gave, achieved here by construction
 * instead of an explicit escape step.
 *
 * Card menu mirrors WebResearchNodeMenu's own posture: Collapse/Expand +
 * Delete Node only - no "Open Document View" placeholder (that is a legacy
 * ConversationNode-specific leftover, not a convention every node kind
 * repeats) and no dock-to-parent action (this node kind is never docked,
 * same posture as html/image/conversation/web_research above it).
 *
 * The submit button's label ("Generate" vs "Refine") is derived purely from
 * whether data.artifactContent is non-empty after trimming - it is not a
 * flag from the wire, since ArtifactAgent.get_response(current_artifact,
 * history) itself always treats "was there prior content" as an empty-string
 * check, not a separate mode.
 */

export interface ArtifactMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ArtifactNodeData extends Record<string, unknown> {
  artifactContent: string;
  history: ArtifactMessage[];
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onSubmit: (text: string) => void;
  onCancel: () => void;
}

export type ArtifactFlowNode = Node<ArtifactNodeData, "artifact">;

interface MenuPosition {
  x: number;
  y: number;
}

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ThinkingNodeMenu/DocumentNodeMenu/ConversationNodeMenu/
 * WebResearchNodeMenu). */
function useMenuDismiss(menuRef: React.RefObject<HTMLDivElement | null>, onClose: () => void) {
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
  }, [menuRef, onClose]);
}

// -- card-level menu -------------------------------------------------------

function ArtifactNodeMenu({
  position,
  isCollapsed,
  onToggleCollapse,
  onDelete,
  onClose,
}: {
  position: MenuPosition;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  useMenuDismiss(menuRef, onClose);

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
          onToggleCollapse();
          onClose();
        }}
      >
        {isCollapsed ? "Expand" : "Collapse"}
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

// -- turn bubble ------------------------------------------------------------

function ArtifactBubble({ message }: { message: ArtifactMessage }) {
  return (
    <div className={`artifact-node-bubble${message.role === "user" ? " user" : " assistant"}`}>
      {/* Reuses .chat-node-content's markdown-body rule set verbatim - same
          shared-class convention ConversationBubble's own -content div
          establishes across every sibling node kind. */}
      <div className="chat-node-content artifact-node-bubble-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
          {message.content}
        </ReactMarkdown>
      </div>
    </div>
  );
}

// -- view ----------------------------------------------------------------

export function ArtifactNodeView({ data, selected }: NodeProps<ArtifactFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const lodCollapsed = zoom < LOD_ZOOM_THRESHOLD;
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  // Local, ephemeral instruction draft - starts empty and is never re-synced
  // from props after mount, same non-clobbering posture ConversationNodeView's
  // own message draft follows (there is no wire field this could even be
  // re-synced from - unlike WebResearchNodeView's query input, an
  // instruction is a one-shot message, not persisted node state).
  const [draft, setDraft] = useState("");

  function submit() {
    const text = draft.trim();
    if (!text) return;
    data.onSubmit(text);
    setDraft("");
  }

  const hasContent = data.artifactContent.trim().length > 0;
  const submitLabel = hasContent ? "Refine" : "Generate";

  return (
    <div
      className={`scene-node artifact-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title chat-node-role">
        <span>Artifact</span>
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
        <div className="scene-node-body artifact-node-content">
          <div className="artifact-node-document">
            {hasContent ? (
              <div className="chat-node-content artifact-node-document-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {data.artifactContent}
                </ReactMarkdown>
              </div>
            ) : (
              <p className="artifact-node-empty">Document is currently empty.</p>
            )}
          </div>

          {data.history.length > 0 && (
            <div className="artifact-node-messages">
              {data.history.map((message, index) => (
                // No per-message id on the wire shape - render order is
                // always the true history order, so index is a correct and
                // sufficient key here (same posture as ConversationBubble's
                // own key).
                <ArtifactBubble key={index} message={message} />
              ))}
            </div>
          )}

          <div className="artifact-node-input-row">
            <textarea
              className="artifact-node-input"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                // Enter-to-submit / Shift+Enter-for-newline - same convention
                // ConversationNodeView's own input (and the Composer's own
                // onKeyDown handler) already use.
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  submit();
                }
              }}
              placeholder="Describe what to generate or refine…"
              aria-label="Instruction"
              rows={1}
              spellCheck
            />
            <div className="artifact-node-input-actions">
              <button
                type="button"
                className="artifact-node-submit-btn"
                disabled={!draft.trim() || !!data.pendingRequestId}
                onClick={submit}
              >
                {submitLabel}
              </button>
              {data.pendingRequestId && (
                <button
                  type="button"
                  className="artifact-node-cancel-btn"
                  onClick={() => data.onCancel()}
                  title="Cancel response"
                >
                  Cancel
                </button>
              )}
            </div>
          </div>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <ArtifactNodeMenu
          position={menuPosition}
          isCollapsed={data.isCollapsed}
          onToggleCollapse={data.onToggleCollapse}
          onDelete={data.onDelete}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
