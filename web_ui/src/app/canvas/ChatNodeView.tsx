import type { Node, NodeProps } from "@xyflow/react";
import { memo, useEffect, useRef, useState, type RefObject } from "react";
import type { StreamListener } from "../../lib/ws/transport";
import { CHAT_SCROLL_REPORT_DEBOUNCE_MS } from "./canvasConstants";
import { CollapseToggleButton } from "./CollapseToggleButton";
import { downloadTextFile } from "./downloadTextFile";
import type { MenuPosition } from "./menuPosition";
import { NodeMarkdown } from "./NodeMarkdown";
import { ChatCitationChips, KNOWLEDGE_CITATION_RECORD, citationsForReply, type KnowledgeCitationSource } from "./ChatCitationChips";
import { ChatScienceCard, type ScienceReferenceData } from "./ChatScienceCard";
import { NodeMenu } from "./NodeMenu";
import { NodeHeading } from "./NodeHeading";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";
import { useStreamBuffer } from "./useStreamBuffer";
import { MenuItem, MenuGroup, SubMenu } from "../ui/Menu";
import { Icon } from "../ui/icons/Icon";
import { IconButton } from "../ui/IconButton";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { Tooltip } from "../ui/Tooltip";
import { Disclosure } from "../ui/Disclosure";
import { formatTokens } from "../ui/format";

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
  // Full alternatives stay in the saved graph, not every scene snapshot.
  versionCount: number;
  versionIndex: number;
  onSelectVersion: (index: number) => void;
  onEditMessage: (content: string, expectedContent: string, onSettled: (success: boolean) => void) => void;
  onEditAsNewBranch: (content: string, expectedContent: string, onSettled: (success: boolean) => void) => void;
  onCompareVersions: () => void;
  isCollapsed: boolean;
  staleness?: "current" | "out_of_date" | "upstream_changed";
  dockedChildren: { id: string; label: string }[];
  // R6.3: the node's own persisted scroll position - restored into
  // .chat-node-content's scrollTop once on mount, then kept live-updated
  // (debounced) via onScrollChange as the user scrolls.
  chatScrollValue: number;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onUndockChild: (childId: string) => void;
  onRegenerate: () => void;
  /** Opens the server-validated preview; no replay until explicit confirmation. */
  onRerunFromHere?: () => void;
  onGenerateImage: () => void;
  onGenerateChart: (chartType: string) => void;
  onGenerateKeyTakeaway: () => void;
  onGenerateExplainerNote: () => void;
  onOpenDocumentView: () => void;
  onOpenCitation: (source: KnowledgeCitationSource) => void;
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
  // The backend indexes the full root-to-this-node history before publishing
  // this flag, so the visual indicator always reflects server-confirmed state.
  indexIntoKnowledge: boolean;
  onSetIndexIntoKnowledge: (enabled: boolean) => void;
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
  /** Request-time routing rule, absent for replies created before this field. */
  modelReason: string | null;
  onOpenModelSettings: () => void;
  isBranchSynthesis: boolean;
  synthesisInstructions: string;
  synthesisSourceNodeIds: string[];
  // ADR-018 stage 18.3: the opposite direction from provider/model above -
  // an explicit PIN deciding what the NEXT reply (or, when this node is a
  // branch root, the branch) resolves to, not provenance of a past one.
  // "" for both means "no pin here" (the overwhelming majority of nodes).
  overrideProvider: string;
  overrideModelId: string;
  onPinToCurrentModel: () => void;
  onClearModelOverride: () => void;
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
  // The verified Scientific Workbench result a "Discuss in chat" message was
  // started from - rendered as a compact card (ChatScienceCard) instead of
  // the model context in `content`, which may contain bounded excerpts.
  scienceReference?: ScienceReferenceData | null;
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
  onRerunFromHere,
  menuTriggerRef,
  canRerun,
  onEdit,
  onEditAsNewBranch,
  editDisabled,
  onGenerateImage,
  onGenerateChart,
  onGenerateKeyTakeaway,
  onGenerateExplainerNote,
  onOpenDocumentView,
  isBranchFocusActive,
  onToggleBranchFocus,
  onBranchFromHere,
  indexIntoKnowledge,
  onSetIndexIntoKnowledge,
  branchStatus,
  isFinalDeliverable,
  onSetBranchStatus,
  onSetFinalDeliverable,
  onCollapseBranch,
  overrideModelId,
  onPinToCurrentModel,
  onClearModelOverride,
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
  onRerunFromHere?: () => void;
  menuTriggerRef: RefObject<HTMLButtonElement>;
  canRerun: boolean;
  onEdit: () => void;
  onEditAsNewBranch: () => void;
  editDisabled: boolean;
  onGenerateImage: () => void;
  onGenerateChart: (chartType: string) => void;
  onGenerateKeyTakeaway: () => void;
  onGenerateExplainerNote: () => void;
  onOpenDocumentView: () => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
  onBranchFromHere: () => void;
  indexIntoKnowledge: boolean;
  onSetIndexIntoKnowledge: (enabled: boolean) => void;
  branchStatus: string;
  isFinalDeliverable: boolean;
  onSetBranchStatus: (status: string) => void;
  onSetFinalDeliverable: (isFinal: boolean) => void;
  onCollapseBranch: (collapsed: boolean) => void;
  overrideModelId: string;
  onPinToCurrentModel: () => void;
  onClearModelOverride: () => void;
  onClose: () => void;
}) {
  const copyText = () => {
    navigator.clipboard.writeText(content).catch((error: unknown) => {
      console.error("[chat-node] Copy text failed:", error);
    });
  };
  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu" ariaLabel="Chat message actions">
      <MenuGroup label="Edit">
        <MenuItem icon="copy" label="Copy text" onSelect={copyText} />
        {isUser && <>
          <MenuItem icon="edit" label="Edit" disabled={editDisabled} onSelect={onEdit} />
          <MenuItem icon="branch" label="Edit as new branch" disabled={editDisabled} onSelect={onEditAsNewBranch} />
        </>}
      </MenuGroup>
      <MenuGroup label="Branch">
        <MenuItem icon="branch" label="Branch from here" onSelect={onBranchFromHere} />
        {canRerun && onRerunFromHere && (
          <MenuItem icon="rerun" label="Re-run from here" disabled={editDisabled}
            onSelect={() => {
              const trigger = document.getElementById("chat-rerun-trigger-" + nodeId)
                ?? menuTriggerRef.current;
              trigger?.focus();
              onRerunFromHere();
            }} />
        )}
        {!isUser && <MenuItem icon="rerun" label="Regenerate response" onSelect={onRegenerate} />}
        <MenuItem icon={isBranchFocusActive ? "eye" : "eye-off"}
          label={isBranchFocusActive ? "Show all branches" : "Hide other branches"}
          onSelect={onToggleBranchFocus} />
        <MenuItem icon={isCollapsed ? "maximize" : "minimize"}
          label={isCollapsed ? "Expand branch" : "Collapse branch"}
          onSelect={() => onCollapseBranch(!isCollapsed)} />
      </MenuGroup>
      <MenuGroup label="Node">
        <MenuItem icon={isCollapsed ? "maximize" : "minimize"}
          label={isCollapsed ? "Expand" : "Collapse"} onSelect={onToggleCollapse} />
        <SubMenu label="Mark status" icon="tag">
          {BRANCH_STATUS_OPTIONS.map((option) => (
            <MenuItem key={option.value} label={option.label} kind="radio"
              checked={option.value === branchStatus}
              onSelect={() => onSetBranchStatus(option.value)} />
          ))}
        </SubMenu>
        <MenuItem icon="flag"
          label={isFinalDeliverable ? "Unmark final deliverable" : "Mark as final deliverable"}
          onSelect={() => onSetFinalDeliverable(!isFinalDeliverable)} />
        <MenuItem icon="pin" label="Pin to current model" onSelect={onPinToCurrentModel} />
        {overrideModelId && <MenuItem icon="pin" label={`Clear model pin (${overrideModelId})`}
          onSelect={onClearModelOverride} />}
        {dockedChildren.length > 0 && (
          <SubMenu label="Reveal docked items" icon="eye">
            {dockedChildren.map((child) => (
              <MenuItem key={child.id} label={child.label} onSelect={() => onUndockChild(child.id)} />
            ))}
          </SubMenu>
        )}
      </MenuGroup>
      <MenuGroup label="Share">
        <MenuItem icon="knowledge" label={indexIntoKnowledge ? "Remove from Knowledge Library" : "Add branch to Knowledge Library"}
          kind="checkbox" checked={indexIntoKnowledge}
          onSelect={() => onSetIndexIntoKnowledge(!indexIntoKnowledge)} />
        <MenuItem icon="document-text" label="Open in Document View" onSelect={onOpenDocumentView} />
        <MenuItem icon="download" label="Export…" onSelect={() => downloadTextFile(content, `chat-${nodeId}.md`)} />
      </MenuGroup>
      <SubMenu label="Generate" icon="sparkle">
        <MenuItem icon="document-text" label="Key takeaway" onSelect={onGenerateKeyTakeaway} />
        <MenuItem icon="document-text" label="Explainer note" onSelect={onGenerateExplainerNote} />
        <SubMenu label="Chart" icon="chart-bar">
          {CHART_TYPE_OPTIONS.map((option) => (
            <MenuItem key={option.value} label={option.label} onSelect={() => onGenerateChart(option.value)} />
          ))}
        </SubMenu>
        <MenuItem icon="image" label="Image" onSelect={onGenerateImage} />
      </SubMenu>
      <MenuItem label="Delete node" destructive onSelect={onDelete} />
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

// Node redesign, stage 3's own deferred sub-item ("content fade + Show more,
// replacing the hard 300px cutoff"), implemented now: must match
// .chat-node-content's own max-height in styles.css exactly, or the fade/
// button would appear (or fail to appear) at the wrong point.
export const CHAT_CONTENT_COLLAPSED_MAX_HEIGHT = 300;

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
    a.versionCount === b.versionCount &&
    a.versionIndex === b.versionIndex &&
    a.isCollapsed === b.isCollapsed &&
    a.staleness === b.staleness &&
    a.isBranchFocusActive === b.isBranchFocusActive &&
    a.indexIntoKnowledge === b.indexIntoKnowledge &&
    a.branchStatus === b.branchStatus &&
    a.isFinalDeliverable === b.isFinalDeliverable &&
    a.provider === b.provider &&
    a.model === b.model &&
    a.modelReason === b.modelReason &&
    a.overrideProvider === b.overrideProvider &&
    a.overrideModelId === b.overrideModelId &&
    a.isBranchSynthesis === b.isBranchSynthesis &&
    a.synthesisInstructions === b.synthesisInstructions &&
    a.pendingRequestId === b.pendingRequestId &&
    a.responseIncomplete === b.responseIncomplete &&
    a.promptTokens === b.promptTokens &&
    a.completionTokens === b.completionTokens &&
    a.estimatedCostUsd === b.estimatedCostUsd &&
    // A reference is fixed once created; its receipt hash identifies it.
    (a.scienceReference?.receiptHash ?? null) === (b.scienceReference?.receiptHash ?? null) &&
    a.onToggleCollapse === b.onToggleCollapse &&
    a.onDelete === b.onDelete &&
    a.onUndockChild === b.onUndockChild &&
    a.onRegenerate === b.onRegenerate &&
    a.onRerunFromHere === b.onRerunFromHere &&
    a.onSelectVersion === b.onSelectVersion &&
    a.onEditMessage === b.onEditMessage &&
    a.onEditAsNewBranch === b.onEditAsNewBranch &&
    a.onCompareVersions === b.onCompareVersions &&
    a.onGenerateImage === b.onGenerateImage &&
    a.onGenerateChart === b.onGenerateChart &&
    a.onGenerateKeyTakeaway === b.onGenerateKeyTakeaway &&
    a.onGenerateExplainerNote === b.onGenerateExplainerNote &&
    a.onOpenDocumentView === b.onOpenDocumentView &&
    a.onOpenCitation === b.onOpenCitation &&
    a.onScrollChange === b.onScrollChange &&
    a.onToggleBranchFocus === b.onToggleBranchFocus &&
    a.onBranchFromHere === b.onBranchFromHere &&
    a.onSetIndexIntoKnowledge === b.onSetIndexIntoKnowledge &&
    a.onSetBranchStatus === b.onSetBranchStatus &&
    a.onSetFinalDeliverable === b.onSetFinalDeliverable &&
    a.onCollapseBranch === b.onCollapseBranch &&
    a.onPinToCurrentModel === b.onPinToCurrentModel &&
    a.onClearModelOverride === b.onClearModelOverride &&
    a.onOpenModelSettings === b.onOpenModelSettings &&
    a.onCancelRegenerate === b.onCancelRegenerate &&
    a.subscribeStream === b.subscribeStream &&
    dockedChildrenEqual(a.dockedChildren, b.dockedChildren) &&
    toolInvocationsEqual(a.toolInvocations, b.toolInvocations) &&
    stringArraysEqual(a.synthesisSourceNodeIds, b.synthesisSourceNodeIds)
  );
}

/** Request-time reason: the words deliberately describe the OLD reply,
 * while the actions below change routing for FUTURE replies only. */
function describeModelReason(reason: string): string {
  if (reason === "node override") return "A model pin on the sending node selected this reply's model.";
  if (reason === "branch override") return "A model pin on this conversation branch selected this model.";
  if (reason === "workspace default") return "The workspace default model selected this reply's model.";
  if (reason === "task assignment") return "The chat task's assigned model selected this reply's model.";
  if (reason === "conversation default") return "The conversation default selected this reply's model.";
  if (reason.startsWith("auto: ")) return `Automatic routing selected this model using the ${reason.slice(6)} policy.`;
  return `Routing rule: ${reason}`;
}

/** Same portaled, light-dismiss and roving-keyboard-focus pattern as
 * GroupColorPicker's NodeMenu popover. Never render for legacy replies. */
function ModelReasonDisclosure({
  model, reason, onOpenNodePinMenu, onOpenModelSettings,
}: {
  model: string;
  reason: string;
  onOpenNodePinMenu: (position: MenuPosition) => void;
  onOpenModelSettings: () => void;
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [anchor, setAnchor] = useState<MenuPosition | null>(null);
  function toggle() {
    if (anchor) {
      setAnchor(null);
      return;
    }
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) setAnchor({ x: rect.left, y: rect.bottom + 6 });
  }
  return (
    <span className="chat-node-model-reason nodrag">
      <button
        ref={triggerRef} type="button" className="chat-node-model-reason-trigger nodrag"
        aria-label={`Why this model: ${model}`}
        aria-haspopup="menu" aria-expanded={anchor !== null}
        onClick={toggle}
      ><Icon name="info" size="sm" /> Why this model</button>
      {anchor && (
        <NodeMenu
          position={anchor} onClose={() => setAnchor(null)}
          ignoreRef={triggerRef} className="chat-node-model-reason-popover"
          ariaLabel={`Why this model: ${model}`}
        >
          <p className="chat-node-model-reason-description">{describeModelReason(reason)}</p>
          <p className="chat-node-model-reason-hint">Changes affect future replies, not this recorded answer.</p>
          <MenuItem icon="pin" label="Change node pin" onSelect={() => onOpenNodePinMenu(anchor)} />
          <MenuItem icon="settings"
            label={reason.startsWith("auto: ") ? "Change automatic selection policy" : "Change task model setting"}
            onSelect={onOpenModelSettings} />
        </NodeMenu>
      )}
    </span>
  );
}

export const ChatNodeView = memo(function ChatNodeView({
  id,
  data,
  selected,
}: NodeProps<ChatFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const [editMode, setEditMode] = useState<"in-place" | "new-branch" | null>(null);
  // Temporarily reveal a folded/LOD node while editing; no second undo step.
  const collapsed = (data.isCollapsed || lodCollapsed) && editMode === null;
  const [editDraft, setEditDraft] = useState("");
  const [editBase, setEditBase] = useState("");
  const [editError, setEditError] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const menuTriggerRef = useRef<HTMLButtonElement>(null);
  const restoreEditFocus = useRef(false);
  const staleEdit = editMode !== null && data.content !== editBase;

  useEffect(() => {
    if (editMode) {
      editorRef.current?.focus();
    } else if (restoreEditFocus.current) {
      // The textarea is gone on Save or Escape; don't strand focus on body.
      // The NodeShell menu button survives collapse and low-detail views.
      restoreEditFocus.current = false;
      if (menuTriggerRef.current?.isConnected) menuTriggerRef.current.focus();
    }
  }, [editMode]);

  function beginEdit(mode: "in-place" | "new-branch") {
    if (!data.isUser || data.pendingRequestId) return;
    restoreEditFocus.current = true;
    setEditBase(data.content);
    setEditDraft(data.content);
    setEditError("");
    setEditMode(mode);
  }

  function cancelEdit() {
    if (!editSaving) setEditMode(null);
  }

  function saveEdit() {
    if (!editMode || editSaving || data.pendingRequestId) return;
    if (staleEdit) {
      setEditError("This message changed while editing. Reopen its latest version.");
      return;
    }
    if (!editDraft.trim()) {
      setEditError("Message cannot be empty.");
      return;
    }
    if (editDraft === editBase) {
      setEditMode(null);
      return;
    }
    setEditSaving(true);
    const settled = (success: boolean) => {
      setEditSaving(false);
      if (success) {
        setEditMode(null);
        setEditError("");
      } else {
        setEditError("Save failed. Your draft is still here; retry or cancel.");
      }
    };
    if (editMode === "in-place") data.onEditMessage(editDraft, editBase, settled);
    else data.onEditAsNewBranch(editDraft, editBase, settled);
  }

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

  const contentRef = useRef<HTMLDivElement>(null);
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Restore only when the reading area mounts (also after overview/manual
  // folding). A backend scroll report should never move a visible reader.
  useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = data.chatScrollValue;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collapsed]);

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

  // ADR-006 stage 6.4 / ADR-011 stage 11.4: live Regenerate streaming,
  // throttled to at most one markdown re-parse per animation frame - see
  // useStreamBuffer's own doc comment for the full rationale (dedup: this
  // was duplicated inline here and in ConversationNodeView until the tech-
  // debt sweep extracted it).
  const streamedContent = useStreamBuffer(data.pendingRequestId, data.subscribeStream);

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
    // 300px cap: at that crossing point the element's OWN rendered height
    // changes (uncapped natural size -> capped 300px), which is exactly
    // the resize event this needs to catch; further growth past the cap
    // doesn't change the boolean this is tracking, so no further event is
    // needed. vitest.setup.ts's ResizeObserver stub never fires its
    // callback, so only the synchronous measure() call above is exercised
    // in tests; this is a real-browser-only refinement on top of it.
    const observer = new ResizeObserver(measure);
    observer.observe(contentEl);
    return () => observer.disconnect();
  }, [data.content, streamedContent, collapsed]);

  // Citation provenance uses a reserved structured row, not a model tool call.
  const visibleToolInvocations = data.toolInvocations.filter((item) => item.name !== KNOWLEDGE_CITATION_RECORD);
  // Executed tools are not a normal model reply: neither the menu nor its
  // quick action may offer automatic replay. Citations are metadata only.
  const manualRerun = visibleToolInvocations.length > 0 || data.isBranchSynthesis;
  const canRerunFromHere = !manualRerun &&
    (data.isUser ? data.versionCount > 1 : data.staleness === "out_of_date");
  const replyCitations = !data.isUser && !data.pendingRequestId
    ? citationsForReply(data.content, data.toolInvocations) : undefined;
  const hasUsage = data.promptTokens != null || data.completionTokens != null;
  const hasMetadata = Boolean(data.model || data.overrideModelId || hasUsage);
  // At map scale, keep a bounded plain-text excerpt; mounting Markdown for every
  // off-screen turn would defeat the level-of-detail optimization. Respect a
  // user's explicit fold even while the viewport is in overview.
  const scienceReference = data.scienceReference ?? null;
  const overview = lodCollapsed && !data.isCollapsed
    ? (scienceReference
      ? `${scienceReference.operationTitle}: ${scienceReference.summary}`
      : data.pendingRequestId ? streamedContent : data.content).slice(0, 160).trim()
    : "";
  const headerDetail = overview ? <span className="node-overview-excerpt">{overview}</span> : undefined;

  return (
    <NodeShell
      textOutput
      kindClassName={`chat-node${data.isUser ? " user" : " assistant"}`}
      selected={!!selected}
      collapsed={collapsed}
      staleness={data.staleness}
      manualRerun={manualRerun}
      onMenuChange={setMenuPosition}
      menuTriggerRef={menuTriggerRef}
      onBodyDoubleClick={data.isUser && editMode === null ? () => beginEdit("in-place") : undefined}
      header={
        <NodeHeading
          className="chat-node-role"
          detail={headerDetail}
        >
          <span className="chat-node-role-group">
            <span>{data.isUser ? "You" : "Assistant"}</span>
            {data.versionCount > 1 && (
              <span className="chat-node-version-switcher nodrag" role="group" aria-label="Message versions">
                <IconButton size="sm" className="chat-node-version-button nodrag" icon="chevron-left"
                  label="Previous version" disabled={data.versionIndex <= 0 || !!data.pendingRequestId}
                  onClick={() => data.onSelectVersion(data.versionIndex - 1)} />
                <span>
                  <span aria-hidden="true">{data.versionIndex + 1} of {data.versionCount}</span>
                  <span className="visually-hidden" role="status" aria-live="polite">
                    Version {data.versionIndex + 1} of {data.versionCount} selected
                  </span>
                </span>
                <IconButton size="sm" className="chat-node-version-button nodrag" icon="chevron-right"
                  label="Next version" disabled={data.versionIndex >= data.versionCount - 1 || !!data.pendingRequestId}
                  onClick={() => data.onSelectVersion(data.versionIndex + 1)} />
                <Button size="sm" variant="ghost" iconStart="compare"
                  className="chat-node-version-compare-button nodrag"
                  aria-label="Compare message versions" disabled={!!data.pendingRequestId}
                  onClick={data.onCompareVersions}>Compare</Button>
              </span>
            )}
            {data.branchStatus && data.branchStatus !== "active" && (
              <Tooltip label={`Branch status: ${data.branchStatus}`}>
                <span className="chat-node-status-badge" role="status" data-status={data.branchStatus}
                  aria-label={`Branch status: ${data.branchStatus}`}>
                  <Icon size="sm" name={data.branchStatus === "rejected" ? "status-rejected"
                    : data.branchStatus === "superseded" ? "history" : "status-success"} />
                  {data.branchStatus.charAt(0).toUpperCase() + data.branchStatus.slice(1)}
                </span>
              </Tooltip>
            )}
            {data.dockedChildren.length > 0 && (
              <Tooltip label="Docked items">
                <span className="chat-node-docked-badge" aria-label="Docked items"><Icon name="folder" size="sm" />{data.dockedChildren.length}</span>
              </Tooltip>
            )}
            {data.isBranchSynthesis && (
              <Tooltip label={`Branch synthesis (${data.synthesisSourceNodeIds.length} sources): ${data.synthesisInstructions}`}>
                <span className="chat-node-synthesis-badge" aria-label="Branch Synthesis">
                  <Icon name="merge" size="sm" /><span>Synthesis</span>
                </span>
              </Tooltip>
            )}
            {data.isFinalDeliverable && (
              <span className="chat-node-final-badge" aria-label="Final Deliverable">
                <Icon name="flag" size="sm" /><span>Final deliverable</span>
              </span>
            )}
            {data.indexIntoKnowledge && (
              <span className="chat-node-knowledge-badge" role="status" aria-label="Indexed in Knowledge Library">
                <Icon name="knowledge" size="sm" />In Knowledge Library
              </span>
            )}
          </span>
          <span className="chat-node-title-actions">
            {!collapsed && (
              <span className="chat-node-quick-actions">
                <IconButton size="sm" className="chat-node-quick-action nodrag"
                  icon={copied ? "check" : "copy"} label="Copy text" onClick={onQuickCopy} />
                <IconButton size="sm" className="chat-node-quick-action nodrag"
                  icon="branch" label="Branch from here" onClick={data.onBranchFromHere} />
                {canRerunFromHere && data.onRerunFromHere && (
                  <IconButton size="sm" className="chat-node-quick-action nodrag"
                    id={"chat-rerun-trigger-" + id} icon="rerun" label="Re-run from here"
                    disabled={!!data.pendingRequestId} onClick={data.onRerunFromHere} />
                )}
                <IconButton size="sm" className="chat-node-quick-action nodrag"
                  icon="document-text" label="Open Document View" onClick={data.onOpenDocumentView} />
              </span>
            )}
            <CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={data.onToggleCollapse} />
          </span>
        </NodeHeading>
      }
      bodyClassName="chat-node-content-shell"
      menu={
        menuPosition && (
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
            onRerunFromHere={data.onRerunFromHere}
            menuTriggerRef={menuTriggerRef}
            canRerun={canRerunFromHere}
            onEdit={() => beginEdit("in-place")}
            onEditAsNewBranch={() => beginEdit("new-branch")}
            editDisabled={!!data.pendingRequestId}
            onGenerateImage={data.onGenerateImage}
            onGenerateChart={data.onGenerateChart}
            onGenerateKeyTakeaway={data.onGenerateKeyTakeaway}
            onGenerateExplainerNote={data.onGenerateExplainerNote}
            onOpenDocumentView={data.onOpenDocumentView}
            isBranchFocusActive={data.isBranchFocusActive}
            onToggleBranchFocus={data.onToggleBranchFocus}
            onBranchFromHere={data.onBranchFromHere}
            indexIntoKnowledge={data.indexIntoKnowledge}
            onSetIndexIntoKnowledge={data.onSetIndexIntoKnowledge}
            branchStatus={data.branchStatus}
            isFinalDeliverable={data.isFinalDeliverable}
            onSetBranchStatus={data.onSetBranchStatus}
            onSetFinalDeliverable={data.onSetFinalDeliverable}
            onCollapseBranch={data.onCollapseBranch}
            overrideModelId={data.overrideModelId}
            onPinToCurrentModel={data.onPinToCurrentModel}
            onClearModelOverride={data.onClearModelOverride}
            onClose={() => setMenuPosition(null)}
          />
        )
      }
    >
      {editMode ? (
        <div className="chat-node-edit-panel">
          <textarea
            ref={editorRef}
            className="chat-node-editor nodrag nopan nowheel"
            aria-label={editMode === "in-place" ? "Edit message text" : "Edit new branch message"}
            value={editDraft}
            onChange={(event) => { setEditDraft(event.target.value); setEditError(""); }}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return;
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                cancelEdit();
              } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                event.stopPropagation();
                saveEdit();
              }
            }}
            disabled={editSaving}
            spellCheck
          />
          {staleEdit && <p role="alert">The message changed while editing. Reopen its latest version.</p>}
          {editError && <p role="alert">{editError}</p>}
          <div className="chat-node-edit-actions">
            <Button size="sm" variant="primary" disabled={editSaving || staleEdit || !editDraft.trim()} onClick={saveEdit}>
              {editSaving ? "Saving…" : editMode === "in-place" ? "Save changes" : "Save as new branch"}
            </Button>
            <Button size="sm" disabled={editSaving} onClick={cancelEdit}>Cancel</Button>
          </div>
          <small className="chat-node-edit-hint">Ctrl+Enter to save · Escape to cancel</small>
        </div>
      ) : scienceReference ? (
        <ChatScienceCard reference={scienceReference} content={data.content} />
      ) : (
        <>
          <div className="chat-node-reading-area">
            <div
              className={`chat-node-content${contentExpanded ? " expanded" : ""}`}
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
                <NodeMarkdown content={data.pendingRequestId ? streamedContent : data.content}
                    citations={replyCitations} onCitationClick={data.onOpenCitation} />
              )}
            </div>
            {contentOverflows && !contentExpanded && (
              <div className="chat-node-content-fade" aria-hidden="true" />
            )}
          </div>
          {!data.isUser && !data.pendingRequestId && (
            <ChatCitationChips citations={replyCitations ?? []} onOpenCitation={data.onOpenCitation} />
          )}
          {contentOverflows && (
            <Button size="sm" variant="ghost" className="chat-node-show-more nodrag"
              aria-expanded={contentExpanded}
              onClick={() => setContentExpanded((expanded) => !expanded)}>
              {contentExpanded ? "Show less" : "Show more"}
            </Button>
          )}
        </>
      )}
      {visibleToolInvocations.length > 0 && (
        <Disclosure className="chat-node-tool-invocations"
          {...{ title: visibleToolInvocations.length === 1 ? "1 tool call" : `${visibleToolInvocations.length} tool calls` }}>
          {visibleToolInvocations.map((invocation, index) => (
            <div key={invocation.id || index}
              className={`chat-node-tool-invocation${invocation.isError ? " error" : ""}`}>
              <div className="chat-node-tool-invocation-name">{invocation.name}</div>
              <pre className="chat-node-tool-invocation-arguments">{invocation.argumentsJson}</pre>
              <pre className="chat-node-tool-invocation-result">{invocation.result}</pre>
            </div>
          ))}
        </Disclosure>
      )}
      {/* ADR-006 stage 6.4 review fix: per-node Stop for an in-flight
          streamed regenerate - see onCancelRegenerate's own comment on
          ChatNodeData. Rendered only while this node's own request is
          genuinely in flight, the same conditional-render pattern
          ConversationNodeView's Cancel button already uses. */}
      {data.pendingRequestId && (
        <div className="chat-node-streaming-actions">
          <Button size="sm" variant="secondary" className="chat-node-stop-btn nodrag"
            onClick={data.onCancelRegenerate}>Stop</Button>
        </div>
      )}
      {/* ADR-006 stage 6.4 (partial-output preservation): shown when the
          persisted content above is a partial response the backend committed
          after a killed stream. Suppressed while a NEW regenerate is already
          in flight - the live stream above is the retry in progress. The
          inline Regenerate button fires the SAME regenerateResponse intent
          the card menu's own "Regenerate Response" item uses (no new
          intent), gated on !isUser exactly like that menu item. */}
      {data.responseIncomplete && !data.pendingRequestId && (
        <div className="chat-node-incomplete-banner" role="status">
          <span>Response interrupted — use Regenerate to retry.</span>
          {!data.isUser && (
            <IconButton size="sm" className="chat-node-incomplete-retry nodrag"
              icon="rerun" label="Regenerate" onClick={data.onRegenerate} />
          )}
        </div>
      )}
      {hasMetadata && !collapsed && (
        <div className="chat-node-footer-meta">
          {data.model && <span className="chat-node-model-badge">{data.model}</span>}
          {data.model && hasUsage && <span aria-hidden="true">·</span>}
          {hasUsage && (
            <Tooltip label={`Prompt: ${data.promptTokens ?? "?"} · Completion: ${data.completionTokens ?? "?"}`}>
              <span className="chat-node-usage-badge">
                {data.estimatedCostUsd != null ? `$${data.estimatedCostUsd.toFixed(4)}`
                  : `${formatTokens(data.promptTokens ?? 0)} / ${formatTokens(data.completionTokens ?? 0)} tokens`}
              </span>
            </Tooltip>
          )}
          {data.modelReason && (
            <ModelReasonDisclosure model={data.model} reason={data.modelReason}
              onOpenNodePinMenu={setMenuPosition} onOpenModelSettings={data.onOpenModelSettings} />
          )}
          {data.overrideModelId && (
            <Tooltip label={`Pinned to ${data.overrideProvider} — ${data.overrideModelId}`}>
              <Badge className="chat-node-model-override-badge">
                <Icon name="pin" size="sm" /> {data.overrideModelId}
              </Badge>
            </Tooltip>
          )}
        </div>
      )}
    </NodeShell>
  );
}, chatNodePropsAreEqual);
