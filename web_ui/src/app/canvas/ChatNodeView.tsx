import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo, useEffect, useRef, useState, type ReactNode } from "react";
import type { StreamListener } from "../../lib/ws/transport";
import { CHAT_SCROLL_REPORT_DEBOUNCE_MS } from "./canvasConstants";
import { downloadTextFile } from "./downloadTextFile";
import { GROUP_MONO_COLORS, GROUP_NAMED_COLORS } from "./GroupColorPicker";
import type { MenuPosition } from "./menuPosition";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";
import { useLodVisibility } from "./useLodVisibility";

/**
 * The chat node (Qt-removal plan R3.1/R3.2) - ChatNode's React successor:
 * a single message-bubble card, push-only (content arrives via the scene
 * document, never generated here). Real: render, collapse/expand, delete
 * (with the backend's reparent-children rule), copy. Deferred, with an
 * honest disabled+title label rather than a fake action or a silent drop
 * (an R3.4 live-drive audit found several legacy ChatNode menu items had
 * been dropped with zero acknowledgment - fixed here): Regenerate (assistant
 * nodes only, needs the R4 agent layer). One legacy item is still deliberately
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
 * still gated on !isUser (matching the legacy is_user guard). "Generate
 * Image" is likewise no longer deferred as of R4.4a: it now calls the real
 * generateImage intent, with no visibility/enablement gating beyond what
 * already existed - matches legacy's own unconditional enablement (the
 * empty-content case is caught server-side with a warning banner instead).
 * "Generate Chart" is likewise no longer deferred as of R6.2: it now opens a
 * real click-to-expand submenu (CHART_TYPE_OPTIONS below) offering the 5
 * chart types in legacy's own menu order (Bar/Line/Histogram/Pie/Sankey),
 * each dispatching the real generateChart intent with this node as the
 * parent. "Generate Key Takeaway" and "Generate Explainer Note" are
 * likewise no longer deferred as of R8a: their agents were lost with the
 * R7.6b Qt cutover and never ported, and the tooltip blaming a missing
 * agent layer had been stale since R4 - the very layer Regenerate/Image/
 * Chart already use. Both now dispatch real intents that drop the agent's
 * output into a new note beside this node (graphlink_note_agent.py).
 * "Open Document View" is likewise no longer deferred as of this change: it
 * now opens DocumentViewPanel.tsx with this node's own content
 * (frontend-only, no backend intent - the content is already client-side).
 * "Export" is
 * likewise no longer deferred as of R7.5a: it downloads the node's raw
 * content (not the rendered markdown) as a .md file via downloadTextFile -
 * frontend-only, no backend involved, since the content is already in
 * memory client-side. "Hide Other Branches" is likewise no longer deferred
 * as of R8a: it now calls the real onToggleBranchFocus callback, already
 * closed over this node's own id by SceneCanvas.tsx, which owns the actual
 * branch-isolation graph walk and per-node dimming style entirely - this
 * file's only job is flipping the button's own label between "Hide Other
 * Branches" and "Show All Branches" to match the scene-wide
 * isBranchFocusActive flag SceneCanvas.tsx also hands down.
 *
 * R6.3: the node's own scroll position within .chat-node-content (its
 * scrollable markdown body) is now restored on mount and reported
 * (debounced) on every scroll - the legacy serializer's own scroll_value
 * field, previously unmodeled here entirely, needed for R6.4/R6.5's session
 * load/save round trip.
 */

export interface ChatNodeData extends Record<string, unknown> {
  content: string;
  isUser: boolean;
  isCollapsed: boolean;
  dockedChildren: { id: string; label: string }[];
  // R6.3: the node's own persisted scroll position - restored into
  // .chat-node-content's scrollTop once on mount, then kept live-updated
  // (debounced) via onScrollChange as the user scrolls.
  chatScrollValue: number;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onUndockChild: (childId: string) => void;
  onRegenerate: () => void;
  onGenerateImage: () => void;
  onGenerateChart: (chartType: string) => void;
  onGenerateKeyTakeaway: () => void;
  onGenerateExplainerNote: () => void;
  onOpenDocumentView: () => void;
  onScrollChange: (value: number) => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
  // ADR-002 Workstream 1 ("Branch from here"): marks this node as the reply
  // target for the composer's NEXT send, instead of only ever continuing
  // from the current branch tip (sceneStore's replyTargetNodeId - see that
  // field's own comment). The actual fork happens server-side
  // (SceneDocument.send_message's branch_from_node_id) once the user
  // types a message and sends it; this callback only stages the pick.
  onBranchFromHere: () => void;
  // ADR-002 Workstream 1 ("Synthesize Branches"): provenance for a
  // Synthesize Branches result node - null/false/"" for every ordinary chat
  // node (the vast majority), which renders no badge/label at all (see the
  // render guards below). provider/model come from ComposerDocument.route()
  // at the moment the synthesis ran (backend/canvas.py's synthesize_
  // branches), NOT a live "current route" - they are a frozen record of
  // what actually produced this specific node's content, matching the
  // ADR's own "provider/model provenance" acceptance criterion.
  provider: string | null;
  model: string | null;
  isBranchSynthesis: boolean;
  synthesisInstructions: string;
  synthesisSourceNodeIds: string[];
  // ADR-002 Workstream 1 ("Branch status and lifecycle"): the final
  // sequenced item after fork/compare/synthesize. branchStatus is one of
  // exactly "active" (the default)/"accepted"/"rejected"/"superseded" -
  // per-node, no inheritance from or cascade to any other node (see
  // backend/canvas.py's SceneNode.branch_status comment). isFinalDeliverable
  // is server-computed (n.id === the document's one final_deliverable_
  // node_id pointer), never client-derived, so at most one node in the
  // whole scene can ever read true. onCollapseBranch("Collapse Branch"/
  // "Expand Branch" menu item) reuses is_collapsed but flips it across
  // this node's ENTIRE chat-kind subtree server-side, not just this one
  // node - deliberately separate from onToggleCollapse above, which is
  // the existing single-node collapse.
  branchStatus: string;
  isFinalDeliverable: boolean;
  onSetBranchStatus: (status: string) => void;
  onSetFinalDeliverable: (isFinal: boolean) => void;
  onCollapseBranch: (collapsed: boolean) => void;
  // ADR-006 stage 6.4 (universal streaming): non-null while a Regenerate
  // for THIS node is in flight - the backend publishes the request id on
  // the node's own row (never via the composer, which only ever carries the
  // session-level send), and the content area below keys a live stream
  // subscription off it, the exact pendingRequestId/subscribeStream pairing
  // CodeSandboxNodeView's live terminal already established.
  pendingRequestId: string | null;
  // ADR-006 stage 6.4 (partial-output preservation): true when the last
  // response was cut short (cancel/error/timeout) and `content` above is
  // the accumulated partial text the backend committed - renders the
  // "interrupted" banner below the content.
  responseIncomplete: boolean;
  subscribeStream: (requestId: string, listener: StreamListener) => () => void;
  // ADR-006 stage 6.4 review fix: per-node cancel for an in-flight streamed
  // regenerate - the Composer's own Stop button can't reach this request (it
  // only ever tracks the session-level send), so the node carries its own,
  // the same per-node-cancel posture ConversationNodeView's onCancel already
  // established. Fires the SAME generic cancelChatRequest intent that
  // cancel path uses - no new intent (see SceneCanvas's chat branch).
  onCancelRegenerate: () => void;
  // ADR-007 stage 7.4: this turn's tool calls + their results, in call
  // order - [] for the overwhelming majority of chat nodes (no tool-use
  // loop exists yet to populate this; ADR-008 is the first real writer).
  // Rendered as a collapsible section below the content, the ADR's own "an
  // assistant turn that calls tools renders the calls and their results
  // (collapsible)".
  toolInvocations: ToolInvocationData[];
  // ADR-016 stage 16.2: provider-reported real usage + the cost snapshot
  // taken when it was stamped - null for user messages, non-chat kinds,
  // and any reply whose provider reports nothing (e.g. llama.cpp streams).
  // Rendered alongside the model badge below when present.
  promptTokens: number | null;
  completionTokens: number | null;
  estimatedCostUsd: number | null;
}

// Mirrors the generated ToolInvocationRow (web_ui/src/lib/bridge-core/
// generated/scene-state.ts) field-for-field - a local interface rather than
// importing that generated type directly, matching how every other field on
// ChatNodeData above is a plain, ChatNodeView-local shape rather than a
// re-export of its own generated row type.
export interface ToolInvocationData {
  id: string;
  name: string;
  argumentsJson: string;
  result: string;
  isError: boolean;
}

export type ChatFlowNode = Node<ChatNodeData, "chat">;

// R6.2: legacy's own Generate Chart submenu order, confirmed during recon
// (graphlink_canvas_chart_item.py / the legacy chat-node menu build) - Bar,
// Line, Histogram, Pie, Sankey. `value` is the exact lowercase chart_type
// string backend/canvas.py's generateChart intent (and
// graphlink_chart_data.py's SUPPORTED_CHART_TYPES) expects.
const CHART_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "bar", label: "Bar" },
  { value: "line", label: "Line" },
  { value: "histogram", label: "Histogram" },
  { value: "pie", label: "Pie" },
  { value: "sankey", label: "Sankey" },
];

// ADR-002 Workstream 1 ("Branch status and lifecycle"): the exactly-4
// legal values (must match backend/canvas.py's SceneDocument.
// BRANCH_STATUS_VALUES), in the order they render in the Mark Status
// submenu.
const BRANCH_STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "accepted", label: "Accepted" },
  { value: "rejected", label: "Rejected" },
  { value: "superseded", label: "Superseded" },
];

// ADR-002 Workstream 1 ("Branch status and lifecycle"): the status-dot
// badge's colors REUSE GroupColorPicker.tsx's own named palette verbatim
// (looked up by name, not by array index, so a future reordering of that
// palette can't silently retarget these) rather than defining new hex
// literals - that palette is the one place in this codebase with real,
// visually-distinct color values for a "pick one of several named
// semantic colors" concept (the --gl-semantic-status-* CSS token names
// exist for exactly this kind of status vocabulary, but their current
// values are indistinguishable placeholder grays - see that token's own
// definition for why this reuses GroupColorPicker's palette instead).
const BRANCH_STATUS_COLORS: Record<string, string> = {
  active: GROUP_MONO_COLORS.find((c) => c.name === "Mid Gray")!.hex,
  accepted: GROUP_NAMED_COLORS.find((c) => c.name === "Green")!.hex,
  rejected: GROUP_NAMED_COLORS.find((c) => c.name === "Red")!.hex,
  superseded: GROUP_NAMED_COLORS.find((c) => c.name === "Orange")!.hex,
};

function ChatNodeMenu({
  position,
  nodeId,
  content,
  isUser,
  isCollapsed,
  dockedChildren,
  onToggleCollapse,
  onDelete,
  onUndockChild,
  onRegenerate,
  onGenerateImage,
  onGenerateChart,
  onGenerateKeyTakeaway,
  onGenerateExplainerNote,
  onOpenDocumentView,
  isBranchFocusActive,
  onToggleBranchFocus,
  onBranchFromHere,
  branchStatus,
  isFinalDeliverable,
  onSetBranchStatus,
  onSetFinalDeliverable,
  onCollapseBranch,
  onClose,
}: {
  position: MenuPosition;
  nodeId: string;
  content: string;
  isUser: boolean;
  isCollapsed: boolean;
  dockedChildren: { id: string; label: string }[];
  onToggleCollapse: () => void;
  onDelete: () => void;
  onUndockChild: (childId: string) => void;
  onRegenerate: () => void;
  onGenerateImage: () => void;
  onGenerateChart: (chartType: string) => void;
  onGenerateKeyTakeaway: () => void;
  onGenerateExplainerNote: () => void;
  onOpenDocumentView: () => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
  onBranchFromHere: () => void;
  branchStatus: string;
  isFinalDeliverable: boolean;
  onSetBranchStatus: (status: string) => void;
  onSetFinalDeliverable: (isFinal: boolean) => void;
  onCollapseBranch: (collapsed: boolean) => void;
  onClose: () => void;
}) {
  const [chartMenuOpen, setChartMenuOpen] = useState(false);
  const [statusMenuOpen, setStatusMenuOpen] = useState(false);


  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          // Best-effort clipboard write - a failure (missing Clipboard API,
          // a denied permissions prompt, an insecure context) is swallowed
          // rather than left as an unhandled rejection, matching
          // ImageNodeView.tsx's own handleCopyImage: a menu action like this
          // should never crash the node, it should just silently not have
          // copied anything.
          navigator.clipboard.writeText(content).catch((error) => {
            console.error("[chat-node] Copy Text failed:", error);
          });
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
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          downloadTextFile(content, `chat-${nodeId}.md`);
          onClose();
        }}
      >
        Export
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
      {/* ADR-002 Workstream 1: stages this node as the composer's next reply
          target (sceneStore's replyTargetNodeId) - available regardless of
          isUser, since either a user message or an assistant reply is a
          valid point to fork a new branch from. The composer shows a
          "Replying to" indicator once set; the actual second-child branch
          is created server-side the moment the user sends. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onBranchFromHere();
          onClose();
        }}
      >
        Branch from Here
      </button>
      {/* ADR-002 Workstream 1 ("Branch status and lifecycle"): the final
          sequenced item after fork/compare/synthesize, grouped here right
          after Branch from Here since all three below are branch-lifecycle
          actions. Mark Status reuses the exact click-to-expand submenu
          idiom Generate Chart below already established (chartMenuOpen),
          not a new interaction pattern - role="menuitemradio"/aria-checked
          marks the currently active status. */}
      <button
        type="button"
        role="menuitem"
        aria-haspopup="true"
        aria-expanded={statusMenuOpen}
        onClick={() => setStatusMenuOpen((open) => !open)}
      >
        Mark Status
      </button>
      {statusMenuOpen && (
        <div className="chat-node-submenu" role="menu" aria-label="Branch status">
          {BRANCH_STATUS_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              role="menuitemradio"
              aria-checked={option.value === branchStatus}
              onClick={() => {
                onSetBranchStatus(option.value);
                onClose();
              }}
            >
              {option.value === branchStatus ? "✓ " : ""}
              {option.label}
            </button>
          ))}
        </div>
      )}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onSetFinalDeliverable(!isFinalDeliverable);
          onClose();
        }}
      >
        {isFinalDeliverable ? "Unmark Final Deliverable" : "Mark as Final Deliverable"}
      </button>
      {/* "Collapse Branch"/"Expand Branch" flips off THIS node's own
          isCollapsed (same value/direction the plain single-node
          "Expand"/"Collapse" item above already reads) but applies
          server-side across the whole chat-kind subtree rooted here, not
          just this one node - see onCollapseBranch's own comment on
          ChatNodeData. Deliberately NOT automatic when status is set to
          "rejected" above - status and collapse stay decoupled. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onCollapseBranch(!isCollapsed);
          onClose();
        }}
      >
        {isCollapsed ? "Expand Branch" : "Collapse Branch"}
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
      {/* R8a: real. Opens the shared DocumentViewPanel (frontend-only, no
          backend intent) with this node's own content - already client-side. */}
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
      {/* R8a: real. Each runs its agent over THIS node's text and drops the
          result into a new note beside it - see backend/canvas.py's
          _generate_note_from_node. Fire-and-forget like Generate Image
          above: the note arrives on the next scene snapshot. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onGenerateKeyTakeaway();
          onClose();
        }}
      >
        Generate Key Takeaway
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onGenerateExplainerNote();
          onClose();
        }}
      >
        Generate Explainer Note
      </button>
      {/* R6.2: a real click-to-expand submenu (not disabled) - same
          click-to-toggle popover interaction GroupColorPicker.tsx already
          established for a nested picker inside a single flat menu list,
          rather than inventing a hover-triggered flyout with no other
          precedent anywhere in this codebase. */}
      <button
        type="button"
        role="menuitem"
        aria-haspopup="true"
        aria-expanded={chartMenuOpen}
        onClick={() => setChartMenuOpen((open) => !open)}
      >
        Generate Chart
      </button>
      {chartMenuOpen && (
        <div className="chat-node-submenu" role="menu" aria-label="Chart type">
          {CHART_TYPE_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              role="menuitem"
              onClick={() => {
                onGenerateChart(option.value);
                onClose();
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onGenerateImage();
          onClose();
        }}
      >
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
    </NodeMenu>
  );
}

/** R6.3: the debounce wrapper for scroll-position reporting - same plain
 * clearTimeout/setTimeout-box-keyed-off-the-caller's-own-timerRef shape as
 * ChartNodeView.tsx's makeDebouncedChartResize / HtmlNodeView.tsx's
 * makeDebouncedSplitterReport, exported standalone for the same direct-unit-
 * testability reason. */
export function makeDebouncedScrollReport(
  timerRef: { current: ReturnType<typeof setTimeout> | null },
  onScrollChange: (value: number) => void,
  debounceMs: number = CHAT_SCROLL_REPORT_DEBOUNCE_MS,
): (value: number) => void {
  return (value) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      onScrollChange(value);
    }, debounceMs);
  };
}

// Node redesign, stage 3 ("card chrome"): hand-authored stroke icons for
// the header's hover-revealed quick-action row, matching the SAME
// convention this codebase already established twice (GroupNodeView.tsx's
// own GroupIcon, Composer.tsx's own Icon) rather than a fourth, different
// icon approach - fill:none/stroke:currentColor, one small file-local
// component per file that needs icons, not a shared icon library.
type ChatNodeIconName = "copy" | "check" | "branch" | "document";

const CHAT_NODE_ICON_PATHS: Record<ChatNodeIconName, ReactNode> = {
  copy: (
    <>
      <rect x="2.5" y="2.5" width="8" height="8" rx="1.2" />
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.2" />
    </>
  ),
  check: <path d="M3.5 8.5 6.5 11.5 12.5 4.5" />,
  branch: (
    <>
      <circle cx="4.5" cy="3.5" r="1.4" />
      <circle cx="4.5" cy="12.5" r="1.4" />
      <circle cx="11.5" cy="8" r="1.4" />
      <path d="M4.5 4.9V11.1" />
      <path d="M4.5 8H7a3 3 0 0 0 3-3" />
    </>
  ),
  document: (
    <>
      <path d="M4 2h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z" />
      <path d="M9 2v3h3" />
    </>
  ),
};

function ChatNodeIcon({ name }: { name: ChatNodeIconName }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="chat-node-icon">
      {CHAT_NODE_ICON_PATHS[name]}
    </svg>
  );
}

// Node redesign, stage 3's own deferred sub-item ("content fade + Show more,
// replacing the hard 560px cutoff"), implemented now: must match
// .chat-node-content's own max-height in styles.css exactly, or the fade/
// button would appear (or fail to appear) at the wrong point.
export const CHAT_CONTENT_COLLAPSED_MAX_HEIGHT = 560;

/** Pure comparison, exported standalone for the same direct-unit-testability
 * reason makeDebouncedScrollReport above already is - jsdom never lays out
 * real content (scrollHeight is always 0 there), so the actual measurement
 * can only be exercised in a real browser, but this comparison itself can be
 * tested with plain numbers. */
export function contentExceedsCollapsedHeight(scrollHeight: number): boolean {
  return scrollHeight > CHAT_CONTENT_COLLAPSED_MAX_HEIGHT;
}

function dockedChildrenEqual(a: { id: string; label: string }[], b: { id: string; label: string }[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].id !== b[i].id || a[i].label !== b[i].label) return false;
  }
  return true;
}

function stringArraysEqual(a: string[], b: string[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function toolInvocationsEqual(a: ToolInvocationData[], b: ToolInvocationData[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (
      x.id !== y.id ||
      x.name !== y.name ||
      x.argumentsJson !== y.argumentsJson ||
      x.result !== y.result ||
      x.isError !== y.isError
    ) {
      return false;
    }
  }
  return true;
}

/** ADR-011 stage 11.1: React.memo comparator - id (read into the card menu's
 * Export filename), selected, and every ChatNodeData field this component
 * (or the card menu it renders) actually reads, including every callback
 * prop. Array/object-shaped fields (dockedChildren, toolInvocations,
 * synthesisSourceNodeIds) get an element-by-element compare rather than
 * `===` - toFlowNodes can legitimately mint a fresh, value-identical array
 * on every snapshot even when nothing on this node changed; a bare `===`
 * there would make this comparator too tight and quietly defeat the memo.
 *
 * chatScrollValue is deliberately excluded: it's read only inside the
 * mount-only scroll-restore effect below (empty dep array, R6.3's own
 * documented non-clobbering posture) - by the time this comparator ever
 * runs on a re-render, that effect has already fired once and will never
 * fire again, so no change to this field can ever affect what's on screen
 * after mount. Comparing it would only produce spurious re-renders during
 * active (debounced) scrolling - the "too tight" failure mode this
 * comparator exists to avoid. */
export function chatNodePropsAreEqual(prev: NodeProps<ChatFlowNode>, next: NodeProps<ChatFlowNode>): boolean {
  if (prev.id !== next.id || prev.selected !== next.selected) return false;
  const a = prev.data;
  const b = next.data;
  return (
    a.content === b.content &&
    a.isUser === b.isUser &&
    a.isCollapsed === b.isCollapsed &&
    a.isBranchFocusActive === b.isBranchFocusActive &&
    a.branchStatus === b.branchStatus &&
    a.isFinalDeliverable === b.isFinalDeliverable &&
    a.provider === b.provider &&
    a.model === b.model &&
    a.isBranchSynthesis === b.isBranchSynthesis &&
    a.synthesisInstructions === b.synthesisInstructions &&
    a.pendingRequestId === b.pendingRequestId &&
    a.responseIncomplete === b.responseIncomplete &&
    a.promptTokens === b.promptTokens &&
    a.completionTokens === b.completionTokens &&
    a.estimatedCostUsd === b.estimatedCostUsd &&
    a.onToggleCollapse === b.onToggleCollapse &&
    a.onDelete === b.onDelete &&
    a.onUndockChild === b.onUndockChild &&
    a.onRegenerate === b.onRegenerate &&
    a.onGenerateImage === b.onGenerateImage &&
    a.onGenerateChart === b.onGenerateChart &&
    a.onGenerateKeyTakeaway === b.onGenerateKeyTakeaway &&
    a.onGenerateExplainerNote === b.onGenerateExplainerNote &&
    a.onOpenDocumentView === b.onOpenDocumentView &&
    a.onScrollChange === b.onScrollChange &&
    a.onToggleBranchFocus === b.onToggleBranchFocus &&
    a.onBranchFromHere === b.onBranchFromHere &&
    a.onSetBranchStatus === b.onSetBranchStatus &&
    a.onSetFinalDeliverable === b.onSetFinalDeliverable &&
    a.onCollapseBranch === b.onCollapseBranch &&
    a.onCancelRegenerate === b.onCancelRegenerate &&
    a.subscribeStream === b.subscribeStream &&
    dockedChildrenEqual(a.dockedChildren, b.dockedChildren) &&
    toolInvocationsEqual(a.toolInvocations, b.toolInvocations) &&
    stringArraysEqual(a.synthesisSourceNodeIds, b.synthesisSourceNodeIds)
  );
}

export const ChatNodeView = memo(function ChatNodeView({
  id,
  data,
  selected,
}: NodeProps<ChatFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const collapsed = data.isCollapsed || lodCollapsed;
  const [copied, setCopied] = useState(false);

  // Node redesign, stage 3: the header's hover-revealed quick-action Copy
  // button - deliberately a SEPARATE, direct navigator.clipboard call, not
  // routed through the card menu's own "Copy Text" item (ChatNodeMenu,
  // below), since this one needs its own transient "copied" flash feedback
  // (matching the established pattern DocumentViewPanel.tsx/NodeMarkdown.tsx's
  // own CodeBlock already use) independent of the menu's lifecycle.
  function onQuickCopy() {
    navigator.clipboard
      .writeText(data.content)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch((error) => {
        // Best-effort - see ChatNodeMenu's own Copy Text handler above for
        // the same reasoning. No "copied" flash on failure, since nothing
        // was actually copied.
        console.error("[chat-node] Copy failed:", error);
      });
  }
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  // R6.3: restore the saved scroll position once on mount (an empty dep
  // array - deliberate, not a lint oversight: re-running this on every scene
  // snapshot/re-render would fight the user's own live scrolling every time
  // an unrelated field on this node changes). scrollTimerRef carries the
  // debounce state across scroll events (see makeDebouncedScrollReport
  // above).
  const contentRef = useRef<HTMLDivElement>(null);
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = data.chatScrollValue;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(
    () => () => {
      if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current);
    },
    [],
  );

  function onScroll(event: React.UIEvent<HTMLDivElement>) {
    makeDebouncedScrollReport(scrollTimerRef, data.onScrollChange)(event.currentTarget.scrollTop);
  }

  // Node redesign, stage 3's own deferred sub-item, implemented now: content
  // taller than the collapsed cap gets a fade + "Show more" toggle instead of
  // just an internal scrollbar with no indication there's more below.
  // Measures contentRef itself (the capped, overflow-y:auto element), not a
  // separate inner wrapper - scrollHeight already reports the element's true,
  // UNCAPPED content height regardless of its own max-height-clipped
  // clientHeight, so no extra DOM node is needed (and none was added: an
  // earlier draft wrapped NodeMarkdown in its own measurement div, which
  // broke the .chat-node-content > :first-child/:last-child margin-reset
  // rules just below by making them target that wrapper instead of the
  // markdown's own first/last element - caught before this ever shipped).
  const [contentExpanded, setContentExpanded] = useState(false);
  const [contentOverflows, setContentOverflows] = useState(false);

  // ADR-006 stage 6.4: live Regenerate streaming - the exact
  // subscription/reset pattern CodeSandboxNodeView's live terminal
  // established (derived-state reset during render so a new request never
  // shows the previous run's stale content, effect below left to do only
  // transport synchronization; see that file's own comments for the full
  // rationale).
  const [streamedContent, setStreamedContent] = useState("");
  const [subscribedRequestId, setSubscribedRequestId] = useState(data.pendingRequestId);
  if (data.pendingRequestId !== subscribedRequestId) {
    setSubscribedRequestId(data.pendingRequestId);
    setStreamedContent("");
  }

  // ADR-011 stage 11.4: deltas accumulate into these refs (not React state)
  // as they arrive - only the rAF-scheduled flush below ever calls
  // setStreamedContent, so a fast burst of many small deltas within one
  // frame re-parses markdown (NodeMarkdown's full unified/remark/rehype/
  // KaTeX/highlight pipeline) at most once per frame, not once per delta.
  // Every byte still lands in streamedContent, in order - only the RE-PARSE
  // cadence changes, never the data: pendingBufferRef always holds the full,
  // in-order text accumulated since the last flush, and flushStreamBuffer
  // moves the whole thing into state atomically. pendingResetRef tracks
  // whether the buffered text should REPLACE streamedContent at the next
  // flush (a `reset` frame) rather than append to it, mirroring the
  // original un-throttled `reset ? delta : current + delta` semantics
  // exactly - just deferred to flush time instead of applied delta-by-delta.
  const pendingBufferRef = useRef("");
  const pendingResetRef = useRef(false);
  const rafHandleRef = useRef<number | null>(null);

  function flushStreamBuffer() {
    if (rafHandleRef.current !== null) {
      cancelAnimationFrame(rafHandleRef.current);
      rafHandleRef.current = null;
    }
    const bufferedText = pendingBufferRef.current;
    const shouldReset = pendingResetRef.current;
    pendingBufferRef.current = "";
    pendingResetRef.current = false;
    if (!bufferedText && !shouldReset) return;
    setStreamedContent((current) => (shouldReset ? bufferedText : current + bufferedText));
  }

  useEffect(() => {
    const requestId = data.pendingRequestId;
    if (!requestId) return;
    const unsubscribe = data.subscribeStream(requestId, (delta, done, reset) => {
      if (reset) {
        pendingBufferRef.current = delta;
        pendingResetRef.current = true;
      } else {
        pendingBufferRef.current += delta;
      }
      // Stream completion/reset flushes synchronously - the final chunk (or
      // a fresh restart) shouldn't wait an extra frame, and this is also
      // what guarantees a trailing chunk never gets stranded in the buffer
      // past the last render.
      if (done || reset) {
        flushStreamBuffer();
      } else if (rafHandleRef.current === null) {
        rafHandleRef.current = requestAnimationFrame(flushStreamBuffer);
      }
    });
    return () => {
      unsubscribe();
      // A requestId change (new run, or this run finished) makes any
      // content still sitting in the buffer for the OLD request moot - the
      // render-time subscribedRequestId reset above already clears
      // streamedContent to "" for a new id, and data.content is the source
      // of truth once pendingRequestId returns to null - but flush (rather
      // than silently drop) here too, so no buffered byte is ever lost even
      // in that narrow window.
      flushStreamBuffer();
    };
    // data.subscribeStream is a fresh closure every render (see SceneCanvas's
    // toFlowNodes) - depending on it would resubscribe on every unrelated
    // re-render; data.pendingRequestId itself is the real re-subscribe key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.pendingRequestId]);

  useEffect(() => {
    const contentEl = contentRef.current;
    if (!contentEl) return undefined;

    function measure() {
      setContentOverflows(contentExceedsCollapsedHeight(contentEl!.scrollHeight));
    }
    measure();

    // Catches height changes a single measurement on mount would miss - e.g.
    // an embedded image (NodeMarkdown's own ZoomImage) has no intrinsic
    // height until it finishes loading, well after this effect first runs.
    // Observing contentEl itself still works once content crosses the
    // 560px cap: at that crossing point the element's OWN rendered height
    // changes (uncapped natural size -> capped 560px), which is exactly
    // the resize event this needs to catch; further growth past the cap
    // doesn't change the boolean this is tracking, so no further event is
    // needed. vitest.setup.ts's ResizeObserver stub never fires its
    // callback, so only the synchronous measure() call above is exercised
    // in tests; this is a real-browser-only refinement on top of it.
    const observer = new ResizeObserver(measure);
    observer.observe(contentEl);
    return () => observer.disconnect();
  }, [data.content]);

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
          {/* Node redesign, stage 3: a small avatar chip - purely visual
              (aria-hidden, the real role text right after it already
              carries the semantics) - so scanning a dense graph of many
              chat nodes doesn't depend on reading "You"/"Assistant" text
              at small zoom levels. User/Assistant differentiated by the
              same background shade .chat-node.user already tints its own
              title bar with, not a new color - this app's palette stays
              deliberately greyscale. Suppressed while collapsed - an
              adversarial review measured a real overflow: the collapsed
              pill is only 290px, and this chip stacked with the header's
              existing conditional badges (model/synthesis/docked/final,
              which can legitimately all co-occur) plus the quick-actions
              row below pushed the collapse button entirely outside
              .scene-node's overflow:hidden clip, making it unclickable. */}
          {!collapsed && (
            <span className="chat-node-avatar" aria-hidden="true">
              {data.isUser ? "U" : "A"}
            </span>
          )}
          <span>{data.isUser ? "You" : "Assistant"}</span>
          {/* ADR-002 Workstream 1 ("Branch status and lifecycle"): always
              rendered (unlike every other badge here, which is conditional)
              - branchStatus always has a real value ("active" for the vast
              majority of nodes, never null/undefined), so this is always
              meaningful to show, not just for a rare marked case. */}
          <span
            className="chat-node-status-badge"
            style={{ backgroundColor: BRANCH_STATUS_COLORS[data.branchStatus] ?? BRANCH_STATUS_COLORS.active }}
            title={`Branch status: ${data.branchStatus}`}
            aria-label={`Branch status: ${data.branchStatus}`}
          />
          {data.dockedChildren.length > 0 && (
            <span className="chat-node-docked-badge" title="Docked items">
              {data.dockedChildren.length}
            </span>
          )}
          {data.isBranchSynthesis && (
            // ADR-002 Workstream 1 ("Synthesize Branches"): reuses the same
            // "join/combine" glyph as NoteNodeView's Compare Branches badge
            // (⇄) - the two features are different renderers (note vs.
            // chat) so there is no shared component to factor this into,
            // but the visual vocabulary for "derived from multiple
            // branches" stays consistent across both.
            <span
              className="chat-node-synthesis-badge"
              title={`Branch Synthesis (${data.synthesisSourceNodeIds.length} sources): ${data.synthesisInstructions}`}
              aria-label="Branch Synthesis"
            >
              ⇄
            </span>
          )}
          {data.model && (
            <span className="chat-node-model-badge" title={data.provider ?? undefined}>
              {data.model}
            </span>
          )}
          {(data.promptTokens != null || data.completionTokens != null) && (
            // ADR-016 stage 16.2: real usage on the node itself - data
            // exists on the wire since ADR-006 stage 6.8 but was never
            // rendered until now (only the composer's own live counter
            // showed it). estimatedCostUsd is null for local providers'
            // genuine $0 vs an unpriced model - the "$" prefix distinguishes
            // a real, if free, answer from nothing to show.
            <span
              className="chat-node-usage-badge"
              title={`Prompt: ${data.promptTokens ?? "?"} · Completion: ${data.completionTokens ?? "?"}`}
            >
              {`${data.promptTokens ?? "?"}→${data.completionTokens ?? "?"}`}
              {data.estimatedCostUsd != null && ` · $${data.estimatedCostUsd.toFixed(4)}`}
            </span>
          )}
          {data.isFinalDeliverable && (
            // ADR-002 Workstream 1 ("Branch status and lifecycle"): reuses
            // the synthesis badge's "single glyph, no pill" shape - at most
            // one node in the whole scene can ever show this (server-
            // computed against the document's one final_deliverable_
            // node_id pointer).
            <span
              className="chat-node-final-badge"
              title="Final Deliverable"
              aria-label="Final Deliverable"
            >
              ★
            </span>
          )}
        </span>
        {/* A single wrapper span, not two separate top-level flex children -
            .chat-node-role's own layout is justify-content:space-between
            (see .chat-node-role-group's own comment above for why a 3rd
            top-level child gets pushed to the CENTER by that, not grouped
            with the collapse button on the right where it belongs). */}
        <span className="chat-node-title-actions">
          {/* Node redesign, stage 3: hover-revealed quick actions - the 3
              most-reached-for items from the card menu below (Copy Text,
              Branch from here, Open Document View), surfaced without a
              right-click. Hidden by default (opacity:0, matching
              .group-node-controls-chip's own established hover-reveal
              convention exactly) and revealed on hover OR while selected,
              so they're still discoverable without a mouse hovering right
              there after a click-to-select. nodrag on every button - these
              sit inside a React Flow node, and without it a click here
              would also start dragging the card. */}
          {/* Suppressed while collapsed - same overflow reason as the
              avatar chip's own comment above; this row alone was the
              larger contributor to the measured overflow. */}
          {!collapsed && (
            <span className="chat-node-quick-actions">
              <button
                type="button"
                className="chat-node-quick-action nodrag"
                onClick={onQuickCopy}
                title="Copy text"
                aria-label="Copy text"
              >
                <ChatNodeIcon name={copied ? "check" : "copy"} />
              </button>
              <button
                type="button"
                className="chat-node-quick-action nodrag"
                onClick={data.onBranchFromHere}
                title="Branch from here"
                aria-label="Branch from here"
              >
                <ChatNodeIcon name="branch" />
              </button>
              <button
                type="button"
                className="chat-node-quick-action nodrag"
                onClick={data.onOpenDocumentView}
                title="Open Document View"
                aria-label="Open Document View"
              >
                <ChatNodeIcon name="document" />
              </button>
            </span>
          )}
          <button
            type="button"
            className="chat-node-collapse-btn"
            aria-label={data.isCollapsed ? "Expand" : "Collapse"}
            onClick={data.onToggleCollapse}
          >
            {data.isCollapsed ? "▸" : "▾"}
          </button>
        </span>
      </div>
      {!collapsed && (
        <div className="chat-node-content-shell">
          <div
            className={`scene-node-body chat-node-content${contentExpanded ? " expanded" : ""}`}
            ref={contentRef}
            onScroll={onScroll}
          >
            {/* ADR-006 stage 6.4: while a Regenerate is in flight, shows live
                streamed deltas instead of the persisted content; once it
                completes (pendingRequestId back to null), falls back to the
                static, already-persisted content field - same render swap as
                CodeSandboxNodeView's live terminal. Review fix: before the
                first delta lands, an empty markdown body rendered as a blank
                card - the placeholder covers that window, same posture as
                ConversationNodeView's own streaming bubble. */}
            {data.pendingRequestId && !streamedContent ? (
              <span className="chat-node-streaming-placeholder">Waiting for response…</span>
            ) : (
              <NodeMarkdown content={data.pendingRequestId ? streamedContent : data.content} />
            )}
          </div>
          {contentOverflows && !contentExpanded && (
            <div className="chat-node-content-fade" aria-hidden="true" />
          )}
          {contentOverflows && (
            <button
              type="button"
              className="chat-node-show-more nodrag"
              onClick={() => setContentExpanded((expanded) => !expanded)}
            >
              {contentExpanded ? "Show less" : "Show more"}
            </button>
          )}
        </div>
      )}
      {/* ADR-007 stage 7.4: an assistant turn's tool calls + results - the
          ADR's own "renders the calls and their results (collapsible)".
          Native <details>/<summary> rather than the useState-driven
          expand/collapse pattern above (contentExpanded) - this section's
          state doesn't need to survive a re-render triggered by anything
          else on the node, and a native disclosure widget is free
          keyboard/AT support for a debug-style panel like this one. Hidden
          entirely (not just collapsed) when the turn made no tool calls -
          the overwhelming majority of chat nodes. */}
      {!collapsed && data.toolInvocations.length > 0 && (
        <details className="chat-node-tool-invocations">
          <summary>
            {data.toolInvocations.length === 1
              ? "1 tool call"
              : `${data.toolInvocations.length} tool calls`}
          </summary>
          {data.toolInvocations.map((invocation, index) => (
            <div
              key={invocation.id || index}
              className={`chat-node-tool-invocation${invocation.isError ? " error" : ""}`}
            >
              <div className="chat-node-tool-invocation-name">{invocation.name}</div>
              <pre className="chat-node-tool-invocation-arguments">{invocation.argumentsJson}</pre>
              <pre className="chat-node-tool-invocation-result">{invocation.result}</pre>
            </div>
          ))}
        </details>
      )}
      {/* ADR-006 stage 6.4 review fix: per-node Stop for an in-flight
          streamed regenerate - see onCancelRegenerate's own comment on
          ChatNodeData. Rendered only while this node's own request is
          genuinely in flight, the same conditional-render pattern
          ConversationNodeView's Cancel button already uses. */}
      {!collapsed && data.pendingRequestId && (
        <div className="chat-node-streaming-actions">
          <button
            type="button"
            className="chat-node-stop-btn nodrag"
            onClick={data.onCancelRegenerate}
            title="Cancel regenerate"
          >
            Stop
          </button>
        </div>
      )}
      {/* ADR-006 stage 6.4 (partial-output preservation): shown when the
          persisted content above is a partial response the backend committed
          after a killed stream. Suppressed while a NEW regenerate is already
          in flight - the live stream above is the retry in progress. The
          inline Regenerate button fires the SAME regenerateResponse intent
          the card menu's own "Regenerate Response" item uses (no new
          intent), gated on !isUser exactly like that menu item. */}
      {!collapsed && data.responseIncomplete && !data.pendingRequestId && (
        <div className="chat-node-incomplete-banner" role="status">
          <span>Response interrupted — use Regenerate to retry.</span>
          {!data.isUser && (
            <button
              type="button"
              className="chat-node-incomplete-retry nodrag"
              onClick={data.onRegenerate}
            >
              Regenerate
            </button>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <ChatNodeMenu
          position={menuPosition}
          nodeId={id}
          content={data.content}
          isUser={data.isUser}
          isCollapsed={data.isCollapsed}
          dockedChildren={data.dockedChildren}
          onToggleCollapse={data.onToggleCollapse}
          onDelete={data.onDelete}
          onUndockChild={data.onUndockChild}
          onRegenerate={data.onRegenerate}
          onGenerateImage={data.onGenerateImage}
          onGenerateChart={data.onGenerateChart}
          onGenerateKeyTakeaway={data.onGenerateKeyTakeaway}
          onGenerateExplainerNote={data.onGenerateExplainerNote}
          onOpenDocumentView={data.onOpenDocumentView}
          isBranchFocusActive={data.isBranchFocusActive}
          onToggleBranchFocus={data.onToggleBranchFocus}
          onBranchFromHere={data.onBranchFromHere}
          branchStatus={data.branchStatus}
          isFinalDeliverable={data.isFinalDeliverable}
          onSetBranchStatus={data.onSetBranchStatus}
          onSetFinalDeliverable={data.onSetFinalDeliverable}
          onCollapseBranch={data.onCollapseBranch}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}, chatNodePropsAreEqual);
