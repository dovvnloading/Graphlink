import type { Node, NodeProps } from "@xyflow/react";
import { memo, useState } from "react";
import { artifactDiffPreview } from "./artifactProposalDiff";
import { CollapseToggleButton } from "./CollapseToggleButton";
import { downloadTextFile } from "./downloadTextFile";
import type { MenuPosition } from "./menuPosition";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

/**
 * The artifact node (Qt-removal plan R5.2) - the Artifact/Drafter plugin's
 * React card. One vertical card, same overall shell as its plugin-node
 * siblings (ConversationNodeView/WebResearchNodeView): collapse/expand OR-ed
 * with LOD, a card menu with outside-click/Escape dismiss, the shared
 * NodeMarkdown.tsx renderer (node redesign stage 1) every text-bearing node
 * kind now uses. Deliberately NOT a literal two-pane split-view clone of the
 * legacy widget - every prior node/plugin port in this codebase re-composes
 * the legacy widget's layout into one card instead, and this follows that
 * same convention: a document preview on top, the instruction turn history
 * below it, then the instruction input at the bottom.
 *
 * Security note, not a style preference: NodeMarkdown.tsx's own pipeline has
 * no rehype-raw, no dangerouslySetInnerHTML anywhere in this file or that
 * one. Without rehype-raw, react-markdown never interprets embedded HTML in
 * either the document preview or a turn bubble as live markup; it renders as
 * inert text. That is the same raw-text-can-never-become-a-live-DOM-element
 * guarantee the legacy app's escape-then-render pipeline gave, achieved here
 * by construction instead of an explicit escape step.
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

export interface ArtifactProposal {
  proposalId: string;
  content: string;
  baseSha256: string;
  baseMatchesCurrent: boolean;
  summary: string;
}

export interface ArtifactNodeData extends Record<string, unknown> {
  artifactContent: string;
  artifactError: string;
  artifactProposalId: string;
  history: ArtifactMessage[];
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onSubmit: (text: string) => void;
  onSetContent: (content: string, expectedContent: string, onSettled: (success: boolean) => void) => void;
  onFetchProposal: () => Promise<ArtifactProposal | null>;
  onAcceptProposal: (proposalId: string, onSettled: (success: boolean) => void) => void;
  onDiscardProposal: (proposalId: string, onSettled: (success: boolean) => void) => void;
  onCancel: () => void;
}

export type ArtifactFlowNode = Node<ArtifactNodeData, "artifact">;

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ThinkingNodeMenu/DocumentNodeMenu/ConversationNodeMenu/
 * WebResearchNodeMenu). */
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

  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
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

// -- turn bubble ------------------------------------------------------------

function ArtifactBubble({ message }: { message: ArtifactMessage }) {
  return (
    <div className={`artifact-node-bubble${message.role === "user" ? " user" : " assistant"}`}>
      {/* Reuses .chat-node-content's markdown-body rule set verbatim - same
          shared-class convention ConversationBubble's own -content div
          establishes across every sibling node kind. */}
      <div className="chat-node-content artifact-node-bubble-content">
        <NodeMarkdown content={message.content} />
      </div>
    </div>
  );
}

// -- view ----------------------------------------------------------------

function historyEqual(a: ArtifactMessage[], b: ArtifactMessage[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].role !== b[i].role || a[i].content !== b[i].content) return false;
  }
  return true;
}

/** ADR-011 stage 11.1: React.memo comparator - every ArtifactNodeData field
 * this component actually reads (including every callback), plus `selected`.
 * `id` is deliberately not compared - this component never destructures/reads
 * NodeProps.id. `history` is the one array-shaped field: a bare `===` would
 * make this comparator too tight (a fresh-but-value-identical array minted
 * by an upstream rebuild would force a re-render even though nothing this
 * node cares about changed), so it gets an element-by-element compare
 * instead. */
export function artifactNodePropsAreEqual(
  prev: NodeProps<ArtifactFlowNode>,
  next: NodeProps<ArtifactFlowNode>,
): boolean {
  if (prev.selected !== next.selected) return false;
  const a = prev.data;
  const b = next.data;
  return (
    a.artifactContent === b.artifactContent &&
    a.artifactError === b.artifactError &&
    a.artifactProposalId === b.artifactProposalId &&
    a.isCollapsed === b.isCollapsed &&
    a.pendingRequestId === b.pendingRequestId &&
    a.onToggleCollapse === b.onToggleCollapse &&
    a.onDelete === b.onDelete &&
    a.onSubmit === b.onSubmit &&
    a.onSetContent === b.onSetContent &&
    a.onFetchProposal === b.onFetchProposal &&
    a.onAcceptProposal === b.onAcceptProposal &&
    a.onDiscardProposal === b.onDiscardProposal &&
    a.onCancel === b.onCancel &&
    historyEqual(a.history, b.history)
  );
}

export const ArtifactNodeView = memo(function ArtifactNodeView({
  data,
  selected,
}: NodeProps<ArtifactFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  // Local, ephemeral instruction draft - starts empty and is never re-synced
  // from props after mount, same non-clobbering posture ConversationNodeView's
  // own message draft follows (there is no wire field this could even be
  // re-synced from - unlike WebResearchNodeView's query input, an
  // instruction is a one-shot message, not persisted node state).
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState("");
  const [editBase, setEditBase] = useState("");
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const [review, setReview] = useState<{ proposal: ArtifactProposal; original: string } | null>(null);
  const [loadingReview, setLoadingReview] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const activeReview = review?.proposal.proposalId === data.artifactProposalId ? review : null;
  const staleReview = !!activeReview && (
    !activeReview.proposal.baseMatchesCurrent ||
    activeReview.original !== data.artifactContent
  );
  const proposedDiff = activeReview ? artifactDiffPreview(activeReview.original, activeReview.proposal.content) : null;
  const staleEdit = editing && data.artifactContent !== editBase;
  const displayedDocument = editing ? editDraft : data.artifactContent;
  const wordCount = displayedDocument.trim() ? displayedDocument.trim().split(/\s+/).length : 0;

  function beginEdit() {
    if (data.pendingRequestId) return;
    setEditBase(data.artifactContent);
    setEditDraft(data.artifactContent);
    setEditError("");
    setEditing(true);
  }

  function saveEdit() {
    if (saving || data.pendingRequestId) return;
    if (data.artifactContent !== editBase) {
      setEditError("The document changed while you were editing. Download your draft before reopening the latest version.");
      return;
    }
    if (editDraft.length > 256_000) {
      setEditError("This draft exceeds the 256,000-character limit. Shorten it before saving.");
      return;
    }
    if (editDraft === editBase) {
      setEditing(false);
      return;
    }
    setSaving(true);
    data.onSetContent(editDraft, editBase, (success) => {
      setSaving(false);
      if (success) {
        setEditing(false);
        setEditError("");
      } else {
        // Keep the entire unsaved buffer visible on an offline refusal,
        // stale-write conflict or any other server rejection.
        setEditError("Save failed. Your edits remain here; retry or download the draft.");
      }
    });
  }

  async function loadProposal() {
    if (!data.artifactProposalId || loadingReview) return;
    setLoadingReview(true);
    setReviewError("");
    try {
      const proposal = await data.onFetchProposal();
      if (!proposal || proposal.proposalId !== data.artifactProposalId) {
        setReviewError("This proposal is no longer available. Reopen the latest suggestion.");
        return;
      }
      setReview({ proposal, original: data.artifactContent });
    } catch {
      setReviewError("Unable to load the proposed document. Your current draft was not changed.");
    } finally {
      setLoadingReview(false);
    }
  }

  function decideProposal(accept: boolean) {
    if (!activeReview || deciding || data.pendingRequestId) return;
    if (accept && (editing || staleReview)) {
      setReviewError("Your current document changed or has unsaved edits. Export the proposal before continuing.");
      return;
    }
    setDeciding(true);
    setReviewError("");
    const settle = (success: boolean) => {
      setDeciding(false);
      if (success) {
        setReview(null);
      } else {
        setReviewError("The proposal action was rejected. Nothing was overwritten; refresh this review and retry.");
      }
    };
    if (accept) data.onAcceptProposal(activeReview.proposal.proposalId, settle);
    else data.onDiscardProposal(activeReview.proposal.proposalId, settle);
  }

  function submit() {
    const text = draft.trim();
    if (!text || editing || data.pendingRequestId || data.artifactProposalId) return;
    data.onSubmit(text);
    setDraft("");
  }

  const hasContent = data.artifactContent.trim().length > 0;
  const busy = !!data.pendingRequestId;
  // Busy is stated, not just implied by a greyed-out button. The sandbox
  // card's Run reads "Running…" while in flight; this one stayed on
  // "Generate"/"Refine" and merely disabled, which reads as broken rather
  // than working.
  const submitLabel = busy
    ? hasContent
      ? "Refining…"
      : "Generating…"
    : hasContent
      ? "Refine"
      : "Generate";

  return (
    <NodeShell
      kindClassName="artifact-node"
      selected={!!selected}
      collapsed={collapsed}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
      header={
        <div className="scene-node-title chat-node-role">
          <span>Artifact</span>
          <CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={data.onToggleCollapse} />
        </div>
      }
      bodyClassName="artifact-node-content"
      menu={
        menuPosition && (
          <ArtifactNodeMenu
            position={menuPosition}
            isCollapsed={data.isCollapsed}
            onToggleCollapse={data.onToggleCollapse}
            onDelete={data.onDelete}
            onClose={() => setMenuPosition(null)}
          />
        )
      }
    >
      {data.artifactError && (
        // On the node, not only in a session-wide toast: with two artifact
        // nodes on the canvas a toast cannot say which one failed. Same
        // banner treatment the sandbox card uses for its own failures.
        <div className="code-sandbox-node-banner-error" role="alert">
          {data.artifactError}
        </div>
      )}

      {data.artifactProposalId && (
        <section className="artifact-node-proposal" aria-label="AI revision proposal">
          <p className="artifact-node-proposal-heading">
            AI revision ready for review — the saved document is unchanged.
          </p>
          {!activeReview && (
            <button type="button" disabled={loadingReview} onClick={() => { void loadProposal(); }}>
              {loadingReview ? "Loading proposal…" : "Review AI revision"}
            </button>
          )}
          {reviewError && <p role="alert" className="artifact-node-edit-warning">{reviewError}</p>}
          {activeReview && proposedDiff && (
            <>
              {activeReview.proposal.summary && (
                <p className="artifact-node-proposal-summary">{activeReview.proposal.summary}</p>
              )}
              {staleReview && (
                <p role="alert" className="artifact-node-edit-warning">
                  The saved document differs from this proposal's original base. Accept is disabled to protect your edits.
                </p>
              )}
              {proposedDiff.changed ? (
                <>
                  <p className="artifact-node-diff-summary">
                    {proposedDiff.leadingUnchangedLines} unchanged leading lines and{" "}
                    {proposedDiff.trailingUnchangedLines} unchanged trailing lines omitted from preview.
                  </p>
                  <div className="artifact-node-diff-grid">
                    <div>
                      <strong>Current change region</strong>
                      <pre className="artifact-node-diff-before">{proposedDiff.before || "(empty)"}</pre>
                      {proposedDiff.omittedBeforeLines > 0 && <small>{proposedDiff.omittedBeforeLines} more lines not shown</small>}
                    </div>
                    <div>
                      <strong>Proposed change region</strong>
                      <pre className="artifact-node-diff-after">{proposedDiff.after || "(empty)"}</pre>
                      {proposedDiff.omittedAfterLines > 0 && <small>{proposedDiff.omittedAfterLines} more lines not shown</small>}
                    </div>
                  </div>
                </>
              ) : (
                <p>No document changes were proposed.</p>
              )}
              <div className="artifact-node-proposal-actions">
                <button type="button" disabled={deciding || editing || busy || staleReview}
                  onClick={() => decideProposal(true)}>
                  {deciding ? "Processing…" : "Accept revision"}
                </button>
                <button type="button" disabled={deciding || busy}
                  onClick={() => decideProposal(false)}>Discard revision</button>
                <button type="button"
                  onClick={() => downloadTextFile(activeReview.proposal.content, "artifact-proposal.md")}>
                  Download proposed .md
                </button>
              </div>
            </>
          )}
        </section>
      )}
      <div className="artifact-node-toolbar">
        <span className="artifact-node-word-count">{wordCount.toLocaleString()} words</span>
        {editing ? (
          <>
            <button type="button" disabled={saving || busy || staleEdit} onClick={saveEdit}>
              {saving ? "Saving…" : "Save changes"}
            </button>
            <button type="button" disabled={saving} onClick={() => setEditing(false)}>
              Cancel edit
            </button>
          </>
        ) : (
          <button type="button" disabled={busy} onClick={beginEdit}>
            Edit Markdown
          </button>
        )}
        <button
          type="button"
          disabled={!displayedDocument}
          onClick={() => downloadTextFile(displayedDocument, "artifact.md")}
        >
          Download .md
        </button>
      </div>
      {staleEdit && (
        <p className="artifact-node-edit-warning" role="alert">
          This document changed while you were editing. Download your draft and reopen the current version.
        </p>
      )}
      {editError && editing && (
        <p className="artifact-node-edit-warning" role="alert">{editError}</p>
      )}
      <div className="artifact-node-document">
        {editing ? (
          <textarea
            className="artifact-node-editor nodrag"
            aria-label="Edit document Markdown"
            value={editDraft}
            onChange={(event) => { setEditDraft(event.target.value); setEditError(""); }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                saveEdit();
              }
            }}
            disabled={saving}
            spellCheck
          />
        ) : hasContent ? (
          <div className="chat-node-content artifact-node-document-content">
            <NodeMarkdown content={data.artifactContent} />
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
            disabled={!draft.trim() || busy || editing || !!data.artifactProposalId}
            onClick={submit}
          >
            {submitLabel}
          </button>
          {busy && (
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
    </NodeShell>
  );
}, artifactNodePropsAreEqual);
