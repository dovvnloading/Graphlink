import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";

/**
 * The conversation node (Qt-removal plan R3.25/R3.26) - ConversationNode's
 * React successor. Different in kind from every prior R3 node view: instead
 * of one scalar content field, this node holds a growing LIST of messages
 * (data.history), each rendered as its own bubble inside the one node card -
 * the only R3 kind shaped like a real message list rather than one flat text
 * block.
 *
 * Real: render (one ConversationBubble per history entry, same react-markdown
 * + remarkGfm + rehypeHighlight pipeline every other text-bearing node view
 * in this codebase already uses), collapse/expand (manual toggle OR-ed with
 * LOD auto-collapse, same as Chat/Document), delete (generic - a conversation
 * node is never a branch point/reparented, same as code/thinking/html/image),
 * per-bubble copy + delete-from-history, and Send (appends a real user
 * message; the assistant's reply is deferred to R4 - see sendConversationMessage's
 * own backend docstring - the backend surfaces that honestly over the
 * existing notification topic, no fake response synthesized here, matching
 * the Composer's own sendMessage precedent). Deferred, with an honest
 * disabled+title label rather than a silent drop (same audit convention
 * every prior node kind in this plan has followed): Open Document View (the
 * document-viewer island isn't wired into the SPA overlay system yet - same
 * reason ChatNodeView's own menu defers it) and Cancel (there is no in-flight
 * request concept anywhere in this frontend yet for it to ever act on -
 * agent cancellation lands in R4 alongside the agent layer itself).
 *
 * Card menu deliberately does NOT include "Hide Other Branches" or "Include
 * Previous Branch Context": the legacy PluginNodeContextMenu for this node
 * kind only ever shows Open Document View / Collapse-Expand / Delete Node -
 * "Include Previous Branch Context" is gated on an attribute ConversationNode
 * never defines, and branch-visibility toggling is a distinct legacy menu
 * class entirely, not this one.
 */

export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ConversationNodeData extends Record<string, unknown> {
  history: ConversationMessage[];
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onSend: (text: string) => void;
  onDeleteMessage: (index: number) => void;
}

export type ConversationFlowNode = Node<ConversationNodeData, "conversation">;

interface MenuPosition {
  x: number;
  y: number;
}

/** Shared outside-click/Escape dismiss behavior - identical pattern to every
 * sibling menu component (ChatNodeMenu/ThinkingNodeMenu/DocumentNodeMenu). */
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

function ConversationNodeMenu({
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
      {/* Order verified against the legacy PluginNodeContextMenu's own
          construction order for this node kind: Open Document View,
          Collapse/Expand, Delete Node - nothing else. */}
      <button type="button" role="menuitem" disabled title="Document view integration isn't wired into the SPA yet">
        Open Document View
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

// -- per-bubble menu ---------------------------------------------------------

function ConversationBubbleMenu({
  position,
  content,
  onDeleteMessage,
  onClose,
}: {
  position: MenuPosition;
  content: string;
  onDeleteMessage: () => void;
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
          navigator.clipboard.writeText(content);
          onClose();
        }}
      >
        Copy Message
      </button>
      <div className="chat-node-menu-separator" role="separator" />
      <button
        type="button"
        role="menuitem"
        className="chat-node-menu-danger"
        onClick={() => {
          onDeleteMessage();
          onClose();
        }}
      >
        Delete from History
      </button>
    </div>
  );
}

// -- bubble ------------------------------------------------------------------

function ConversationBubble({
  message,
  index,
  onDeleteMessage,
}: {
  message: ConversationMessage;
  index: number;
  onDeleteMessage: (index: number) => void;
}) {
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  return (
    <div
      className={`conversation-node-bubble${message.role === "user" ? " user" : " assistant"}`}
      onContextMenu={(event) => {
        event.preventDefault();
        // Stops this from also bubbling up into the card-level onContextMenu
        // handler below - a bubble right-click opens exactly one menu (its
        // own), never both.
        event.stopPropagation();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      {/* Reuses .chat-node-content's markdown-body rule set (headings,
          lists, code, tables, hljs) - the same shared-class convention
          .chat-node-menu already establishes across every sibling node's
          menu, applied here to markdown styling instead. */}
      <div className="chat-node-content conversation-node-bubble-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
          {message.content}
        </ReactMarkdown>
      </div>
      {menuPosition && (
        <ConversationBubbleMenu
          position={menuPosition}
          content={message.content}
          onDeleteMessage={() => onDeleteMessage(index)}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}

// -- view ----------------------------------------------------------------

export function ConversationNodeView({ data, selected }: NodeProps<ConversationFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const lodCollapsed = zoom < LOD_ZOOM_THRESHOLD;
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const [draft, setDraft] = useState("");

  function send() {
    const text = draft.trim();
    if (!text) return;
    data.onSend(text);
    setDraft("");
  }

  return (
    <div
      className={`scene-node conversation-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title chat-node-role">
        <span>Conversation</span>
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
        <div className="scene-node-body conversation-node-content">
          <div className="conversation-node-messages">
            {data.history.map((message, index) => (
              // No per-message id on the wire shape - render order is
              // always the true history order, so index is a correct and
              // sufficient key here.
              <ConversationBubble
                key={index}
                message={message}
                index={index}
                onDeleteMessage={data.onDeleteMessage}
              />
            ))}
          </div>
          <div className="conversation-node-input-row">
            <textarea
              className="conversation-node-input"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                // Enter-to-send / Shift+Enter-for-newline - same convention
                // the existing Composer already uses (Composer.tsx's own
                // onKeyDown handler).
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              placeholder="Send a message…"
              aria-label="Message"
              rows={1}
              spellCheck
            />
            <div className="conversation-node-input-actions">
              <button
                type="button"
                className="conversation-node-send-btn"
                disabled={!draft.trim()}
                onClick={send}
              >
                Send
              </button>
              <button
                type="button"
                className="conversation-node-cancel-btn"
                disabled
                title="Agent cancellation lands in R4"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <ConversationNodeMenu
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
