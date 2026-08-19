import type { Node, NodeProps } from "@xyflow/react";
import { memo, useState } from "react";
import type { MenuPosition } from "./menuPosition";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

/**
 * The web-research node (Qt-removal plan R5.1) - the Web Research plugin's
 * React card. Same overall shape as ConversationNodeView (collapse/expand
 * OR-ed with LOD, a card menu with outside-click/Escape dismiss, the shared
 * NodeMarkdown.tsx renderer (node redesign stage 1)), but instead of a
 * growing message list this node drives ONE research run at a time and
 * renders it as: a query input + Run/Cancel, an in-progress stage stepper,
 * and - once a result exists - the synthesized answer, its warnings, and its
 * source list.
 *
 * Honest scoping, called out because it is easy to get wrong: mid-run,
 * data.researchActiveSourceId is an OPAQUE id string - the backend has not
 * yet resolved a title/URL for whatever it is currently fetching (that only
 * exists once a ResearchSource is attached to a completed/stale
 * data.researchResult). So this view never tries to look that id up or
 * highlight a chip for it; instead it shows a plain
 * "Fetching source N of total…" progress line built from
 * researchCompleted/researchTotal alone. Per-source chips render ONLY from
 * data.researchResult.sources - which may be THIS run's finished result, or
 * a stale result left over from a previous run while a new one is already
 * back in progress (the two are independent: the stepper reflects
 * data.researchStage, the result section reflects data.researchResult, and
 * both can be visible at once).
 *
 * Card menu deliberately mirrors ConversationNodeMenu's dismiss/positioning
 * plumbing but carries only Collapse/Expand + Delete Node - no "Open
 * Document View" placeholder (that is a legacy ConversationNode-specific
 * leftover, not a convention every node kind repeats) and no dock-to-parent
 * action (this node kind is never docked, same posture as html/image/
 * conversation nodes above it).
 */

export interface WebResearchSourceRow {
  sourceId: string;
  title: string;
  url: string;
  canonicalUrl: string;
  snippet: string;
  rank: number;
  provider: string;
  finalUrl: string;
  status: string;
  errorCode: string;
  errorMessage: string;
  truncated: boolean;
  contentHash: string;
  citationCount: number;
}

export interface WebResearchCitationRow {
  sourceId: string;
  marker: string;
  claimContext: string;
}

export interface WebResearchResultRow {
  requestId: string;
  originalQuery: string;
  effectiveQuery: string;
  answerMarkdown: string;
  sources: WebResearchSourceRow[];
  citations: WebResearchCitationRow[];
  warnings: string[];
  providerSnapshot: Record<string, unknown>;
}

export interface WebResearchNodeData extends Record<string, unknown> {
  query: string;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  researchStage: string;
  researchCompleted: number;
  researchTotal: number;
  researchActiveSourceId: string | null;
  researchError: string;
  researchResult: WebResearchResultRow | null;
  researchRetainToKnowledge: boolean;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onRun: (query: string) => void;
  onCancel: () => void;
  onSetRetainToKnowledge: (retain: boolean) => void;
}

export type WebResearchFlowNode = Node<WebResearchNodeData, "web_research">;

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ThinkingNodeMenu/DocumentNodeMenu/ConversationNodeMenu). */
// -- card-level menu -------------------------------------------------------

function WebResearchNodeMenu({
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

// -- stage stepper ----------------------------------------------------------

/** The 6 in-progress stages, in order - "completed"/"failed"/"cancelled" are
 * terminal states rendered separately (see the banner/result branches in the
 * view below), never as a 7th/8th/9th step here. */
const STAGE_STEPS = [
  { key: "preparing", label: "Preparing" },
  { key: "searching", label: "Searching" },
  { key: "fetching", label: "Fetching" },
  { key: "extracting", label: "Extracting" },
  { key: "validating", label: "Validating" },
  { key: "synthesizing", label: "Synthesizing" },
] as const;

// -- per-source chip ---------------------------------------------------------

/** Colored purely by source.status, reusing the existing app-wide semantic
 * status tokens (--gl-semantic-status-success/warning/error/info) rather than
 * inventing a new palette - accepted reads as success, rejected/failed as
 * error, fetching as info (in progress), discovered as neutral/muted (not
 * yet attempted). */
function WebResearchSourceChip({ source }: { source: WebResearchSourceRow }) {
  return (
    <div className="web-research-node-source">
      <span
        className={`web-research-node-source-status web-research-node-source-status-${source.status}`}
      >
        {source.status}
      </span>
      <span className="web-research-node-source-title">
        {source.title || source.finalUrl || source.url || source.sourceId}
      </span>
    </div>
  );
}

// -- view ----------------------------------------------------------------

function WebResearchNodeViewImpl({ data, selected }: NodeProps<WebResearchFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  // Initialized once from persisted content and never re-synced on a later
  // scene snapshot - same non-clobbering rationale ConversationNodeView's own
  // draft-input state follows (a remote update mid-type must never stomp
  // what the user is currently typing).
  const [draft, setDraft] = useState(data.query);

  function run() {
    const query = draft.trim();
    if (!query) return;
    data.onRun(query);
  }

  const stageIndex = STAGE_STEPS.findIndex((step) => step.key === data.researchStage);
  const showStepper = stageIndex !== -1;
  const isFailed = data.researchStage === "failed";
  const isCancelled = data.researchStage === "cancelled";
  const showProgress = showStepper && data.researchTotal > 0;

  return (
    <NodeShell
      kindClassName="web-research-node"
      selected={!!selected}
      collapsed={collapsed}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
      header={
        <div className="scene-node-title chat-node-role">
          <span>Web Research</span>
          <button
            type="button"
            className="chat-node-collapse-btn"
            aria-label={data.isCollapsed ? "Expand" : "Collapse"}
            onClick={data.onToggleCollapse}
          >
            {data.isCollapsed ? "▸" : "▾"}
          </button>
        </div>
      }
      bodyClassName="web-research-node-content"
      menu={
        menuPosition && (
          <WebResearchNodeMenu
            position={menuPosition}
            isCollapsed={data.isCollapsed}
            onToggleCollapse={data.onToggleCollapse}
            onDelete={data.onDelete}
            onClose={() => setMenuPosition(null)}
          />
        )
      }
    >
      <div className="web-research-node-query-row">
        <input
          type="text"
          className="web-research-node-query-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              run();
            }
          }}
          placeholder="Research a question…"
          aria-label="Research query"
        />
        <div className="web-research-node-query-actions">
          <button
            type="button"
            className="web-research-node-run-btn"
            disabled={!draft.trim() || !!data.pendingRequestId}
            onClick={run}
          >
            Run
          </button>
          {data.pendingRequestId && (
            <button
              type="button"
              className="web-research-node-cancel-btn"
              onClick={() => data.onCancel()}
              title="Cancel research"
            >
              Cancel
            </button>
          )}
        </div>
        {/* ADR-021 stage 21.5: the opt-in that finally reaches ADR-017's
            retention path. WebResearchRequest.retain_to_knowledge and
            _retain_documents shipped complete and tested, but no production
            caller ever set the flag - so Web Research always discarded what
            it fetched, and no user could choose otherwise. Off by default,
            preserving that long-standing behavior for every existing node. */}
        <label className="web-research-node-retain nodrag">
          <input
            type="checkbox"
            checked={data.researchRetainToKnowledge}
            disabled={!!data.pendingRequestId}
            onChange={(event) => data.onSetRetainToKnowledge(event.target.checked)}
          />
          <span>Save sources to knowledge base</span>
        </label>
      </div>

      {showStepper && (
        <div className="web-research-node-stepper">
          {STAGE_STEPS.map((step, index) => (
            <span
              key={step.key}
              className={
                "web-research-node-step" +
                (index < stageIndex ? " done" : index === stageIndex ? " active" : " pending")
              }
            >
              {step.label}
            </span>
          ))}
        </div>
      )}

      {showProgress && (
        <p className="web-research-node-progress">
          Fetching source {Math.min(data.researchCompleted + 1, data.researchTotal)} of{" "}
          {data.researchTotal}…
        </p>
      )}

      {isFailed && (
        <div className="web-research-node-banner web-research-node-banner-failed">
          {data.researchError || "Research failed."}
        </div>
      )}
      {isCancelled && (
        <div className="web-research-node-banner web-research-node-banner-cancelled">
          {data.researchError || "Research was cancelled."}
        </div>
      )}

      {data.researchResult && (
        <div className="web-research-node-result">
          {/* Reuses .chat-node-content's full markdown-body rule set
              verbatim (same shared-class convention
              ConversationBubble's own -content div establishes).
              answerMarkdown is LLM-generated from untrusted web
              evidence, so a javascript:/file: scheme must never be
              allowed to navigate - this view USED to carry its own
              bespoke anchor override for exactly that reason, but
              NodeMarkdown.tsx's own SafeAnchor now provides the
              identical http(s)-only allowlist (and every OTHER node
              kind's markdown gets the same protection too, which none
              of them had before - see NodeMarkdown.tsx's own doc
              comment), so this view no longer needs a special case. */}
          <div className="chat-node-content web-research-node-answer">
            <NodeMarkdown content={data.researchResult.answerMarkdown} />
          </div>

          {data.researchResult.warnings.length > 0 && (
            <ul className="web-research-node-warnings">
              {data.researchResult.warnings.map((warning, index) => (
                <li key={index}>{warning}</li>
              ))}
            </ul>
          )}

          {data.researchResult.sources.length > 0 && (
            <div className="web-research-node-sources">
              {data.researchResult.sources.map((source) => (
                <WebResearchSourceChip key={source.sourceId} source={source} />
              ))}
            </div>
          )}
        </div>
      )}
    </NodeShell>
  );
}

/** ADR-011 stage 11.1: every prop this view actually reads, compared. Most
 * `data` fields are primitives (`===` is correct); `researchResult` is the
 * one nested object field, so it gets a shape-aware compare below instead of
 * `===` (toFlowNodes may mint a fresh object each snapshot even when its
 * content is unchanged - a plain reference compare there would be "too
 * tight" and defeat memoization for every research node with a result).
 * `researchActiveSourceId` is intentionally OMITTED - see this file's module
 * doc: this view never reads that field at all (only the progress-line
 * math over researchCompleted/researchTotal), so comparing it would only
 * cause spurious re-renders, never fix a missed one. */
function stringArraysEqual(a: readonly string[], b: readonly string[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

/** Only the fields WebResearchSourceChip actually renders (status, plus the
 * title/finalUrl/url/sourceId fallback chain) are compared - the rest of
 * WebResearchSourceRow (snippet, rank, provider, errorCode, truncated, ...)
 * never reaches the DOM from this file. */
function researchSourcesEqual(
  a: readonly WebResearchSourceRow[],
  b: readonly WebResearchSourceRow[],
): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (
      x.sourceId !== y.sourceId ||
      x.status !== y.status ||
      x.title !== y.title ||
      x.finalUrl !== y.finalUrl ||
      x.url !== y.url
    ) {
      return false;
    }
  }
  return true;
}

function researchResultsEqual(
  a: WebResearchResultRow | null,
  b: WebResearchResultRow | null,
): boolean {
  if (a === b) return true;
  if (a === null || b === null) return false;
  return (
    a.answerMarkdown === b.answerMarkdown &&
    stringArraysEqual(a.warnings, b.warnings) &&
    researchSourcesEqual(a.sources, b.sources)
  );
}

function webResearchNodeDataAreEqual(prev: WebResearchNodeData, next: WebResearchNodeData): boolean {
  return (
    prev.query === next.query &&
    prev.isCollapsed === next.isCollapsed &&
    prev.pendingRequestId === next.pendingRequestId &&
    prev.researchStage === next.researchStage &&
    prev.researchCompleted === next.researchCompleted &&
    prev.researchTotal === next.researchTotal &&
    prev.researchError === next.researchError &&
    researchResultsEqual(prev.researchResult, next.researchResult) &&
    prev.researchRetainToKnowledge === next.researchRetainToKnowledge &&
    prev.onToggleCollapse === next.onToggleCollapse &&
    prev.onDelete === next.onDelete &&
    prev.onRun === next.onRun &&
    prev.onCancel === next.onCancel &&
    prev.onSetRetainToKnowledge === next.onSetRetainToKnowledge
  );
}

function webResearchNodePropsAreEqual(
  prev: Readonly<NodeProps<WebResearchFlowNode>>,
  next: Readonly<NodeProps<WebResearchFlowNode>>,
): boolean {
  return prev.selected === next.selected && webResearchNodeDataAreEqual(prev.data, next.data);
}

export const WebResearchNodeView = memo(WebResearchNodeViewImpl, webResearchNodePropsAreEqual);
