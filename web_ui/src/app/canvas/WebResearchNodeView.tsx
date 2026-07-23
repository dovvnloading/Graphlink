import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";

/**
 * The web-research node (Qt-removal plan R5.1) - the Web Research plugin's
 * React card. Same overall shape as ConversationNodeView (collapse/expand
 * OR-ed with LOD, a card menu with outside-click/Escape dismiss, the shared
 * react-markdown + remarkGfm + rehypeHighlight pipeline), but instead of a
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
  onToggleCollapse: () => void;
  onDelete: () => void;
  onRun: (query: string) => void;
  onCancel: () => void;
}

export type WebResearchFlowNode = Node<WebResearchNodeData, "web_research">;

interface MenuPosition {
  x: number;
  y: number;
}

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ThinkingNodeMenu/DocumentNodeMenu/ConversationNodeMenu). */
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

export function WebResearchNodeView({ data, selected }: NodeProps<WebResearchFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const lodCollapsed = zoom < LOD_ZOOM_THRESHOLD;
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
    <div
      className={`scene-node web-research-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
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
      {!collapsed && (
        <div className="scene-node-body web-research-node-content">
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
                  ConversationBubble's own -content div establishes), with a
                  custom anchor renderer: answerMarkdown is LLM-generated from
                  untrusted web evidence, so a javascript:/file: scheme must
                  never be allowed to navigate - only a real http(s) link ever
                  reaches window.open, everything else is inert. The href
                  attribute itself is only set for http(s) links - an
                  onClick-only guard leaves the raw (unsafe) href on the DOM
                  node, which the browser's own middle-click/auxclick and
                  "open link in new tab"/"copy link" context-menu actions read
                  directly, bypassing onClick entirely. Omitting href for
                  every other scheme removes that native escape hatch. */}
              <div className="chat-node-content web-research-node-answer">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeHighlight]}
                  components={{
                    a: ({ href, children }) => {
                      const isHttpUrl = !!href && /^https?:\/\//i.test(href.trim());
                      if (!isHttpUrl) {
                        // No href at all - nothing for middle-click/context-menu to act on.
                        return <>{children}</>;
                      }
                      return (
                        <a
                          href={href}
                          onClick={(event) => {
                            event.preventDefault();
                            window.open(href, "_blank", "noopener,noreferrer");
                          }}
                        >
                          {children}
                        </a>
                      );
                    },
                  }}
                >
                  {data.researchResult.answerMarkdown}
                </ReactMarkdown>
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
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <WebResearchNodeMenu
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
