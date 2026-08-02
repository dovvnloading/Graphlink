import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useState } from "react";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";

/**
 * The conversation node (Qt-removal plan R3.25/R3.26) - ConversationNode's
 * React successor. Different in kind from every prior R3 node view: instead
 * of one scalar content field, this node holds a growing LIST of messages
 * (data.history), each rendered as its own bubble inside the one node card -
 * the only R3 kind shaped like a real message list rather than one flat text
 * block.
 *
 * Real: render (one ConversationBubble per history entry, the shared
 * NodeMarkdown.tsx renderer every other text-bearing node view in this
 * codebase now uses - node redesign stage 1), collapse/expand (manual toggle OR-ed with
 * LOD auto-collapse, same as Chat/Document), delete (generic - a conversation
 * node is never a branch point/reparented, same as code/thinking/html/image),
 * per-bubble copy + delete-from-history, Send (appends a real user message
 * and triggers the real agent reply via the backend agent layer - see
 * sendConversationMessage's own backend docstring), (R4.3) Cancel: a
 * real per-node cancel affordance, the exact same conditional-render pattern
 * as the Composer's own Cancel button (Composer.tsx) applied one level down
 * - per-node instead of per-session. Cancel is rendered only while this
 * node's own data.pendingRequestId is non-null (this node has an in-flight
 * reply of its own), and Send is additionally disabled while that same
 * field is set, so a second send can't be issued mid-flight for this node.
 * (R8a) Open Document View is real too: it opens the shared
 * DocumentViewPanel (frontend-only, no backend intent) with this node's
 * entire history formatted as a numbered markdown transcript - see
 * DocumentViewPanel.tsx / SceneCanvas.tsx's toFlowNodes for the transcript
 * formatter.
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
  pendingRequestId: string | null;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onSend: (text: string) => void;
  onDeleteMessage: (index: number) => void;
  onCancel: () => void;
  onOpenDocumentView: () => void;
}

export type ConversationFlowNode = Node<ConversationNodeData, "conversation">;

interface MenuPosition {
  x: number;
  y: number;
}

/** Shared outside-click/Escape dismiss behavior - identical pattern to every
 * sibling menu component (ChatNodeMenu/ThinkingNodeMenu/DocumentNodeMenu). */
// -- card-level menu -------------------------------------------------------

function ConversationNodeMenu({
  position,
  isCollapsed,
  onToggleCollapse,
  onDelete,
  onOpenDocumentView,
  onClose,
}: {
  position: MenuPosition;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onOpenDocumentView: () => void;
  onClose: () => void;
}) {

  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
      {/* Order verified against the legacy PluginNodeContextMenu's own
          construction order for this node kind: Open Document View,
          Collapse/Expand, Delete Node - nothing else. */}
      {/* R8a: real. Opens the shared DocumentViewPanel (frontend-only, no
          backend intent) with this node's entire history formatted as a
          numbered markdown transcript. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onOpenDocumentView();
          onClose();
        }}
      >
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
    </NodeMenu>
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

  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
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
    </NodeMenu>
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
        <NodeMarkdown content={message.content} />
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
                // onKeyDown handler), including the IME-composing guard: an
                // IME's Enter-to-commit keystroke also reports
                // key==="Enter", so without it, confirming a composed
                // character sent the half-typed buffer.
                if (event.nativeEvent.isComposing) return;
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
                disabled={!draft.trim() || !!data.pendingRequestId}
                onClick={send}
              >
                Send
              </button>
              {data.pendingRequestId && (
                <button
                  type="button"
                  className="conversation-node-cancel-btn"
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
        <ConversationNodeMenu
          position={menuPosition}
          isCollapsed={data.isCollapsed}
          onToggleCollapse={data.onToggleCollapse}
          onDelete={data.onDelete}
          onOpenDocumentView={data.onOpenDocumentView}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
