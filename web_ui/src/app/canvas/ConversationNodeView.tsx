import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo, useEffect, useState, type ReactNode } from "react";
import type { StreamListener } from "../../lib/ws/transport";
import type { MenuPosition } from "./menuPosition";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";
import { useLodVisibility } from "./useLodVisibility";

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
  // ADR-006 stage 6.4 (partial-output preservation): true when this
  // message's text was committed from a killed stream (cancel/error/
  // timeout) - the accumulated partial reply, marked with a small
  // "Interrupted" badge in the bubble header.
  incomplete: boolean;
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
  // ADR-006 stage 6.4 (universal streaming): while pendingRequestId above is
  // set, the view keys a live stream subscription off it and renders the
  // accumulating assistant reply as its own bubble after the persisted
  // history - same pendingRequestId/subscribeStream pairing
  // CodeSandboxNodeView's live terminal already established.
  subscribeStream: (requestId: string, listener: StreamListener) => () => void;
}

export type ConversationFlowNode = Node<ConversationNodeData, "conversation">;

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
          // ADR-011 stage 11.1 (D11): a bare fire-and-forget clipboard write
          // left this promise's rejection unhandled - same fix ImageNodeView's
          // own Copy Image action already applies for its clipboard write.
          navigator.clipboard.writeText(content).catch((error: unknown) => {
            console.error("[conversation-node] Copy Message failed:", error);
          });
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

// Node redesign follow-up ("per-bubble chrome"): hand-authored stroke icons
// for the bubble header's hover-revealed quick-action row, matching the SAME
// file-local-icon-component convention this codebase already established
// (GroupNodeView.tsx's own GroupIcon, ChatNodeView.tsx's own ChatNodeIcon) -
// not a shared icon import, even where a glyph (copy/check) is conceptually
// identical to ChatNodeIcon's own. "trash" is new here: Delete from History
// has no analogue among ChatNodeView's own quick actions (Branch from
// Here/Open Document View don't apply to a single message inside a growing
// conversation node).
type ConversationBubbleIconName = "copy" | "check" | "trash";

const CONVERSATION_BUBBLE_ICON_PATHS: Record<ConversationBubbleIconName, ReactNode> = {
  copy: (
    <>
      <rect x="2.5" y="2.5" width="8" height="8" rx="1.2" />
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.2" />
    </>
  ),
  check: <path d="M3.5 8.5 6.5 11.5 12.5 4.5" />,
  trash: (
    <>
      <path d="M3 4.5h10" />
      <path d="M6 4.5V3a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v1.5" />
      <path d="M4.5 4.5l.6 8a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8" />
      <path d="M6.5 7v4M9.5 7v4" />
    </>
  ),
};

function ConversationBubbleIcon({ name }: { name: ConversationBubbleIconName }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="chat-node-icon">
      {CONVERSATION_BUBBLE_ICON_PATHS[name]}
    </svg>
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
  const [copied, setCopied] = useState(false);

  // Adversarial review caught a real bug: bubbles are keyed by array index
  // (see the render loop below), and deleting an earlier message shifts
  // every later one down a slot - React reconciles the shifted bubble into
  // the SAME component instance that used to sit at that index, carrying
  // over its local `copied` state. Without this reset, copying message 0
  // then deleting it left the check-glyph flash stuck on whatever message
  // slid into slot 0, even though nobody ever copied ITS content.
  //
  // Fixed with React's own "adjusting state when a prop changes" pattern
  // (setState during render, not inside a useEffect - the lint rule this
  // codebase enforces, react-hooks/set-state-in-effect, flagged an earlier
  // draft that called setCopied(false) from an effect keyed on
  // message.content instead): comparing against a value tracked across
  // renders and resetting synchronously, before paint, whenever this
  // instance's message actually changes out from under it.
  const [trackedContent, setTrackedContent] = useState(message.content);
  if (message.content !== trackedContent) {
    setTrackedContent(message.content);
    setCopied(false);
  }

  // Same direct navigator.clipboard call (not routed through
  // ConversationBubbleMenu's own "Copy Message" item) as ChatNodeView's own
  // quick-action Copy button, for the identical reason: this one needs its
  // own transient "copied" flash independent of the menu's lifecycle.
  function onQuickCopy() {
    navigator.clipboard
      .writeText(message.content)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch((error: unknown) => {
        console.error("[conversation-node] Copy message failed:", error);
      });
  }

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
      {/* Per-bubble chrome, extending node redesign stage 3's ChatNodeView
          treatment to conversation-node bubbles: an avatar chip (reuses
          .chat-node-avatar verbatim - same 16px circular chip, same
          role-differentiated background shade, styled here via a
          .conversation-node-bubble.user override in styles.css) and a
          hover-revealed quick-action row surfacing the SAME 2 items
          ConversationBubbleMenu below already offers (Copy Message, Delete
          from History) - there are only 2 in this menu, so both qualify as
          "most reached for", unlike ChatNodeView's pick of 3 out of a larger
          menu.

          Unlike ChatNodeView, the avatar here is NOT aria-hidden alone - an
          adversarial review caught that ChatNodeView's own aria-hidden
          choice is only safe because a VISIBLE "You"/"Assistant" span sits
          right next to it (its own comment says so explicitly); this bubble
          had no such text anywhere, so a screen-reader user had zero way to
          tell who said what. The new .conversation-node-bubble-role span is
          that visible, accessible label - the avatar stays aria-hidden
          exactly because this text now carries the semantics, mirroring
          ChatNodeView's own reasoning instead of skipping the part that
          made it valid.

          Quick-action labels also include this bubble's 1-based position
          (adversarial review finding): with N messages in one node card,
          unqualified "Copy Message"/"Delete from History" names would be
          identical across every bubble, leaving a screen-reader user no way
          to tell which button acts on which message - unlike ChatNodeView,
          where each button is the only one of its name on that whole node. */}
      <div className="conversation-node-bubble-header">
        {/* Grouped together (not 2 separate top-level flex children) for the
            same reason .chat-node-role-group exists on ChatNodeView's own
            header - a 3rd top-level child would get pushed to the CENTER
            by this row's justify-content:space-between, instead of sitting
            with the avatar on the left where it belongs. */}
        <span className="conversation-node-bubble-role-group">
          <span className="chat-node-avatar" aria-hidden="true">
            {message.role === "user" ? "U" : "A"}
          </span>
          <span className="conversation-node-bubble-role">{message.role === "user" ? "You" : "Assistant"}</span>
          {/* ADR-006 stage 6.4 (partial-output preservation): marks a
              message whose text was committed from a killed stream - see
              ConversationMessage.incomplete's own comment above. */}
          {message.incomplete && (
            <span
              className="conversation-node-incomplete-badge"
              title="Response interrupted before completion"
            >
              Interrupted
            </span>
          )}
        </span>
        <span className="chat-node-quick-actions">
          <button
            type="button"
            className="chat-node-quick-action nodrag"
            onClick={onQuickCopy}
            title={`Copy message ${index + 1}`}
            aria-label={`Copy message ${index + 1}`}
          >
            <ConversationBubbleIcon name={copied ? "check" : "copy"} />
          </button>
          <button
            type="button"
            className="chat-node-quick-action nodrag"
            onClick={() => onDeleteMessage(index)}
            title={`Delete message ${index + 1} from history`}
            aria-label={`Delete message ${index + 1} from history`}
          >
            <ConversationBubbleIcon name="trash" />
          </button>
        </span>
      </div>
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

function ConversationNodeViewImpl({ data, selected }: NodeProps<ConversationFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const [draft, setDraft] = useState("");

  // ADR-006 stage 6.4: live reply streaming - the exact subscription/reset
  // pattern CodeSandboxNodeView's live terminal established (derived-state
  // reset during render so a new request never shows the previous reply's
  // stale content, effect below left to do only transport synchronization;
  // see that file's own comments for the full rationale).
  const [streamedReply, setStreamedReply] = useState("");
  const [subscribedRequestId, setSubscribedRequestId] = useState(data.pendingRequestId);
  if (data.pendingRequestId !== subscribedRequestId) {
    setSubscribedRequestId(data.pendingRequestId);
    setStreamedReply("");
  }

  useEffect(() => {
    const requestId = data.pendingRequestId;
    if (!requestId) return;
    const unsubscribe = data.subscribeStream(requestId, (delta, _done, reset) => {
      setStreamedReply((current) => (reset ? delta : current + delta));
    });
    return () => unsubscribe();
    // data.subscribeStream is a fresh closure every render (see SceneCanvas's
    // toFlowNodes) - depending on it would resubscribe on every unrelated
    // re-render; data.pendingRequestId itself is the real re-subscribe key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.pendingRequestId]);

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
            {/* ADR-006 stage 6.4: the in-flight assistant reply, rendered as
                its own live bubble after the persisted history while this
                node's pendingRequestId is set. Deliberately NOT a
                ConversationBubble - it has no history index yet (no
                copy/delete/menu can target it); the committed message
                arrives through the next scene snapshot the moment the
                stream ends. */}
            {data.pendingRequestId && (
              <div className="conversation-node-bubble assistant conversation-node-bubble-streaming">
                <div className="conversation-node-bubble-header">
                  <span className="conversation-node-bubble-role-group">
                    <span className="chat-node-avatar" aria-hidden="true">
                      A
                    </span>
                    <span className="conversation-node-bubble-role">Assistant</span>
                  </span>
                </div>
                <div className="chat-node-content conversation-node-bubble-content">
                  {streamedReply ? (
                    <NodeMarkdown content={streamedReply} />
                  ) : (
                    <span className="conversation-node-streaming-placeholder">Waiting for response…</span>
                  )}
                </div>
              </div>
            )}
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

/** `history` is the one array/object-shaped field on ConversationNodeData -
 * toFlowNodes may mint a fresh array (and fresh message objects) on every
 * snapshot even when the transcript itself hasn't changed, so a plain `===`
 * here would be "too tight" (defeats memoization for every conversation node
 * on every unrelated update). Element-wise compare instead, same shape-aware
 * pattern WebResearchNodeView's own researchSourcesEqual/NoteNodeView's own
 * stringArraysEqual use for their own array fields. */
function conversationHistoryEqual(
  a: readonly ConversationMessage[],
  b: readonly ConversationMessage[],
): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (x.role !== y.role || x.content !== y.content || x.incomplete !== y.incomplete) return false;
  }
  return true;
}

/** ADR-011 stage 11.1: every prop this view actually reads, compared - it
 * never destructures `id`, so it is intentionally absent here (this instance
 * never receives a changed `id` without React Flow remounting it under a new
 * key anyway). Every field ConversationNodeData declares is read somewhere in
 * render (the bubbles, the input row, the streaming subscription effect, or
 * the two menus), so every one of them is compared here - `history` gets the
 * shape-aware compare above, everything else (including every callback,
 * per the memoization task's own warning that skipping a callback prop is a
 * real "stale UI" bug, not a safe shortcut) is a stable primitive or callback
 * reference, so `===` is correct for those. */
function conversationNodeDataAreEqual(prev: ConversationNodeData, next: ConversationNodeData): boolean {
  return (
    conversationHistoryEqual(prev.history, next.history) &&
    prev.isCollapsed === next.isCollapsed &&
    prev.pendingRequestId === next.pendingRequestId &&
    prev.onToggleCollapse === next.onToggleCollapse &&
    prev.onDelete === next.onDelete &&
    prev.onSend === next.onSend &&
    prev.onDeleteMessage === next.onDeleteMessage &&
    prev.onCancel === next.onCancel &&
    prev.onOpenDocumentView === next.onOpenDocumentView &&
    prev.subscribeStream === next.subscribeStream
  );
}

function conversationNodePropsAreEqual(
  prev: Readonly<NodeProps<ConversationFlowNode>>,
  next: Readonly<NodeProps<ConversationFlowNode>>,
): boolean {
  return prev.selected === next.selected && conversationNodeDataAreEqual(prev.data, next.data);
}

export const ConversationNodeView = memo(ConversationNodeViewImpl, conversationNodePropsAreEqual);
