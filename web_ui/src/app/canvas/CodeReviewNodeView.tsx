import type { Node, NodeProps } from "@xyflow/react";
import { memo, useEffect, useRef, useState } from "react";
import { CollapseToggleButton } from "./CollapseToggleButton";
import type { MenuPosition } from "./menuPosition";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

/**
 * The Review Lens node - the guided PR reviewer's React card.
 * Same overall shell as every plugin-node sibling (GitlinkNodeView/
 * CodeSandboxNodeView): collapse/expand OR-ed with LOD, a card menu with
 * outside-click/Escape dismiss, the shared NodeMarkdown.tsx renderer, no
 * dock-to-parent action. Like Gitlink, this node is a three-tab workflow
 * (Setup / Walkthrough / Findings) rather than one linear scroll - fetch
 * a diff, read it group by group, then work the tiered findings each have
 * genuinely different phases, and a tabbed layout keeps all of them
 * reachable in one card without an ever-growing single column.
 *
 * State-ownership discipline (read this before touching any input): the
 * PR-URL draft and the Q&A question draft live in LOCAL component state,
 * initialized ONCE from the incoming scene snapshot and never re-synced
 * afterward - the exact non-clobbering posture GitlinkNodeView's own
 * drafts already established (a remote update mid-type must never stomp
 * what the user is currently typing). Committing state to the server is
 * always an explicit button (or Enter) action, never a live
 * keystroke-by-keystroke sync.
 *
 * The Walkthrough tab's full diff body is fetched lazily
 * (data.onFetchDiffText()) the first time the tab is opened after
 * data.codeReviewDiffVersion changes to a new value (a monotonic
 * per-node counter bumped by the backend on every successful fetch),
 * then cached in local state - never refetched on a bare tab-switch
 * back and forth with the same version. Keyed on the version counter
 * rather than any summary string, because two distinct fetches can
 * produce near-identical summaries while carrying different text (the
 * R5.3 post-review FIX 6 precedent).
 *
 * Security note, not a style preference: the diff text and every
 * model-produced string (overview, explanations, findings, answers)
 * render through the exact same NodeMarkdown.tsx pipeline every sibling
 * node view uses - no rehype-raw, no dangerouslySetInnerHTML anywhere in
 * this file or that one. The diff is wrapped in a fenced ```diff code
 * block first (toDiffFence, mirroring GitlinkNodeView's own proposal-diff
 * helper) purely so rehype-highlight can colorize it - it is never
 * treated as anything but inert text by the markdown pipeline, exactly
 * like the findings themselves (which can indirectly embed untrusted
 * diff content by way of the model).
 *
 * Dismissal is server-persisted UI state (data.onDismissFinding), not a
 * local hide: a dismissed finding stays dismissed across snapshots and
 * reloads, and undo restores it (the backend intent is
 * record_command-wrapped). It does NOT survive a re-review -
 * complete_code_review_run clears the dismissal list on every run, because
 * finding ids are re-minted per review and a dismissal of the old review's
 * f3 must not hide the new review's f3. This comment used to claim the
 * opposite. Copy is a best-effort clipboard write with the same .catch()
 * discipline every sibling view applies.
 */

export interface CodeReviewFileRow {
  path: string;
  status: string;
  additions: number;
  deletions: number;
  patch: string;
  patchTruncated: boolean;
  previousPath?: string | null;
}

export interface CodeReviewWalkthroughGroup {
  groupTitle: string;
  paths: string[];
  explanation: string;
}

export interface CodeReviewFinding {
  id: string;
  severity: string;
  tier: string;
  category: string;
  path: string;
  line: number;
  title: string;
  evidence: string;
  impact: string;
  recommendation: string;
}

export interface CodeReviewError {
  id: string;
  severity: string;
  tier: string;
  kind: string;
  path: string;
  line: number;
  title: string;
  evidence: string;
  fix: string;
}

export interface CodeReviewQa {
  question: string;
  answer: string;
}

export interface CodeReviewNodeData extends Record<string, unknown> {
  codeReviewPrUrl: string;
  codeReviewRepo: string;
  codeReviewPrNumber: number;
  codeReviewPrTitle: string;
  codeReviewPrState: string;
  codeReviewPrHtmlUrl: string;
  codeReviewBaseRef: string;
  codeReviewHeadRef: string;
  codeReviewAdditions: number;
  codeReviewDeletions: number;
  codeReviewChangedFiles: number;
  codeReviewFiles: CodeReviewFileRow[];
  codeReviewFilesTruncated: boolean;
  codeReviewDiffTruncated: boolean;
  codeReviewDiffChars: number;
  codeReviewDiffVersion: number;
  codeReviewWalkthrough: CodeReviewWalkthroughGroup[];
  codeReviewFindings: CodeReviewFinding[];
  codeReviewErrors: CodeReviewError[];
  codeReviewDismissedIds: string[];
  codeReviewTitle: string;
  codeReviewOverview: string;
  codeReviewConfidence: string;
  codeReviewScores: Record<string, string>;
  codeReviewQualityScore: number;
  codeReviewVerdict: string;
  codeReviewRisk: string;
  codeReviewQualitySummary: string;
  codeReviewQa: CodeReviewQa[];
  codeReviewState: string;
  codeReviewError: string;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onSetPrUrl: (prUrl: string) => void;
  onFetchDiff: (prUrl: string) => void;
  onFetchDiffText: () => Promise<string>;
  onRun: () => void;
  onCancel: () => void;
  onAsk: (question: string) => void;
  onDismissFinding: (findingId: string) => void;
  onToggleCollapse: () => void;
  onDelete: () => void;
}

export type CodeReviewFlowNode = Node<CodeReviewNodeData, "code_review">;

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/GitlinkNodeMenu/...). */
// -- card-level menu -------------------------------------------------------

function CodeReviewNodeMenu({
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

// -- helpers ----------------------------------------------------------------

/** Wraps a raw unified diff in a markdown fenced code block tagged `diff`
 * so ReactMarkdown + rehype-highlight can colorize it for free - the same
 * technique GitlinkNodeView's own toDiffFence uses.
 *
 * The fence is sized to the content, not fixed at three backticks. Per
 * CommonMark a fenced block ends at the first line whose fence is at least
 * as long as the opening one, so a hard-coded ``` fence is closed by any
 * diff line containing ```, and everything after it renders as LIVE
 * MARKDOWN. That content is a third-party pull request: a PR touching a
 * README, a docs page, or any file with a code sample is enough to escape
 * the block by accident, and a hostile one can then emit headings, links,
 * and images (an image URL is an exfiltration channel) into the node. Using
 * one more backtick than the longest run in the text makes the block
 * unclosable from inside, which is what the fence was always assumed to be.
 *
 * This does not weaken the "inert text" property the module doc describes -
 * NodeMarkdown has no rehype-raw and no dangerouslySetInnerHTML, so the
 * escaped content was never HTML. It was markdown, which is enough. */
function toDiffFence(diffText: string): string {
  const longestRun = (diffText.match(/`+/g) ?? []).reduce(
    (longest, run) => Math.max(longest, run.length),
    0,
  );
  const fence = "`".repeat(Math.max(3, longestRun + 1));
  return fence + "diff\n" + diffText + "\n" + fence;
}

function tierLabel(tier: string): string {
  if (tier === "red") return "Needs attention";
  if (tier === "yellow") return "Warning";
  return "FYI";
}

function verdictLabel(verdict: string): string {
  if (verdict === "strong") return "Strong";
  if (verdict === "needs_revision") return "Needs Revision";
  if (verdict === "not_ready") return "Not Ready";
  return "Not Reviewed";
}

/** The engine's own REVIEW_CATEGORY_LABELS, mirrored. The wire carries the
 * raw snake_case keys ("correctness"), and the scorecard used to render
 * those verbatim as user-facing row labels while the engine's markdown
 * report printed "Correctness" for the same value. Falling back to the raw
 * key keeps an unknown category visible rather than blanking its row. */
const CATEGORY_LABELS: Record<string, string> = {
  correctness: "Correctness",
  reliability: "Reliability",
  security: "Security",
  maintainability: "Maintainability",
  readability: "Readability",
  testing: "Testing",
  performance: "Performance",
  architecture: "Architecture",
};

function categoryLabel(key: string): string {
  return CATEGORY_LABELS[key] ?? key;
}

function findingLocation(path: string, line: number): string {
  if (!path) return "diff-wide";
  return line > 0 ? `${path}:${line}` : path;
}

function copyText(text: string): void {
  // Best-effort clipboard write - a failure (missing Clipboard API,
  // denied permission) must never break the card; same .catch()
  // discipline every sibling view applies to its own copy actions.
  navigator.clipboard?.writeText(text)?.catch((error: unknown) => {
    console.error("Failed to copy finding to clipboard", error);
  });
}

type TabKey = "setup" | "walkthrough" | "findings";
const TABS: { key: TabKey; label: string }[] = [
  { key: "setup", label: "Setup" },
  { key: "walkthrough", label: "Walkthrough" },
  { key: "findings", label: "Findings" },
];

// -- view ----------------------------------------------------------------

function CodeReviewNodeViewImpl({ data, selected }: NodeProps<CodeReviewFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("setup");

  // -- Setup tab: local, never-resynced draft (see module doc above) -----
  const [prUrlDraft, setPrUrlDraft] = useState(data.codeReviewPrUrl);
  const [questionDraft, setQuestionDraft] = useState("");

  const busy = !!data.pendingRequestId;

  function fetchDiff() {
    const prUrl = prUrlDraft.trim();
    if (!prUrl) return;
    data.onSetPrUrl(prUrl);
    data.onFetchDiff(prUrl);
  }

  // The Ask draft is cleared when an ANSWER LANDS, never at click time.
  // onAsk is fire-and-forget over the websocket, and the intent drops the
  // request on four separate paths (node gone, node busy, no diff fetched
  // yet, model call failed) - each of which used to destroy the typed
  // question with nothing to show for it and no way to get it back.
  // codeReviewQa only ever grows on a successful answer, so its length is
  // the acknowledgement signal.
  const qaCountRef = useRef(data.codeReviewQa.length);
  useEffect(() => {
    if (data.codeReviewQa.length > qaCountRef.current) setQuestionDraft("");
    qaCountRef.current = data.codeReviewQa.length;
  }, [data.codeReviewQa.length]);

  function askQuestion() {
    const question = questionDraft.trim();
    if (!question) return;
    data.onAsk(question);
  }

  // -- Walkthrough tab: lazy-fetch-once-per-version ----------------------
  // Same shape as GitlinkNodeView's own Context-tab fetch: the re-fetch
  // guard is a ref (never rendered on its own), only the fetch RESULT is
  // React state; a sequence ref discards stale resolutions when two
  // fetches overlap; the version counter (never a summary string) is the
  // cache key.
  const [fetchedDiffText, setFetchedDiffText] = useState<string | null>(null);
  const [diffFetchError, setDiffFetchError] = useState<string | null>(null);
  const fetchedForVersionRef = useRef<number | null>(null);
  const diffFetchSeqRef = useRef(0);

  function runDiffFetch() {
    setFetchedDiffText(null);
    setDiffFetchError(null);
    const seq = ++diffFetchSeqRef.current;
    data
      .onFetchDiffText()
      .then((text) => {
        if (diffFetchSeqRef.current !== seq) return;
        setFetchedDiffText(text);
      })
      .catch(() => {
        if (diffFetchSeqRef.current !== seq) return;
        setDiffFetchError("Could not load the diff.");
      });
  }

  useEffect(() => {
    if (activeTab !== "walkthrough") return;
    if (data.codeReviewState === "draft") return;
    const version = data.codeReviewDiffVersion ?? 0;
    if (fetchedForVersionRef.current === version) return;
    fetchedForVersionRef.current = version;
    runDiffFetch();
    // data.onFetchDiffText is a fresh closure every render (see
    // SceneCanvas's toFlowNodes) - depending on it would refetch on every
    // unrelated re-render, so it is deliberately omitted;
    // fetchedForVersionRef is the real re-fetch guard.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, data.codeReviewDiffVersion, data.codeReviewState]);

  // -- Findings tab ------------------------------------------------------
  const dismissed = new Set(data.codeReviewDismissedIds);
  const visibleFindings = data.codeReviewFindings.filter((finding) => !dismissed.has(finding.id));
  const visibleErrors = data.codeReviewErrors.filter((error) => !dismissed.has(error.id));
  const dismissedCount = data.codeReviewDismissedIds.length;

  const hasIdentity = !!data.codeReviewRepo;
  const canRun = hasIdentity && !busy && data.codeReviewState !== "draft";

  return (
    <NodeShell
      kindClassName="code-review-node"
      selected={!!selected}
      collapsed={collapsed}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
      header={
        <div className="scene-node-title chat-node-role">
          <span>Review Lens</span>
          <CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={data.onToggleCollapse} />
        </div>
      }
      bodyClassName="code-review-node-content"
      menu={
        menuPosition && (
          <CodeReviewNodeMenu
            position={menuPosition}
            isCollapsed={data.isCollapsed}
            onToggleCollapse={data.onToggleCollapse}
            onDelete={data.onDelete}
            onClose={() => setMenuPosition(null)}
          />
        )
      }
    >
      <div className="code-review-node-tabs" role="tablist" aria-label="Review Lens sections">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            id={`code-review-tab-${tab.key}`}
            aria-controls={`code-review-panel-${tab.key}`}
            aria-selected={activeTab === tab.key}
            // Roving tabindex: only the selected tab is in the tab order,
            // and Left/Right move between them - the keyboard contract a
            // role="tablist" promises. Without it the widget announced
            // itself as a tablist and then behaved like three loose
            // buttons.
            tabIndex={activeTab === tab.key ? 0 : -1}
            className={`code-review-node-tab${activeTab === tab.key ? " active" : ""}`}
            onClick={() => setActiveTab(tab.key)}
            onKeyDown={(event) => {
              if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
              event.preventDefault();
              const index = TABS.findIndex((candidate) => candidate.key === activeTab);
              const step = event.key === "ArrowRight" ? 1 : TABS.length - 1;
              const next = TABS[(index + step) % TABS.length];
              setActiveTab(next.key);
              document.getElementById(`code-review-tab-${next.key}`)?.focus();
            }}
          >
            {tab.label}
            {tab.key === "findings" && visibleFindings.length + visibleErrors.length > 0 && (
              <span className="code-review-node-tab-count">
                {visibleFindings.length + visibleErrors.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {activeTab === "setup" && (
        <div
          className="code-review-node-setup-tab"
          role="tabpanel"
          id="code-review-panel-setup"
          aria-labelledby="code-review-tab-setup"
        >
          <div className="code-review-node-field-row">
            <span className="code-review-node-field-label">Pull request</span>
            <input
              type="text"
              className="code-review-node-input"
              value={prUrlDraft}
              onChange={(event) => setPrUrlDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  fetchDiff();
                }
              }}
              placeholder="https://github.com/owner/repo/pull/123"
              aria-label="Pull request URL"
            />
            <div className="code-review-node-inline-row">
              <button
                type="button"
                className="code-review-node-primary-btn"
                disabled={busy || !prUrlDraft.trim()}
                onClick={fetchDiff}
              >
                Fetch Diff
              </button>
            </div>
          </div>

          {hasIdentity ? (
            <div className="code-review-node-identity">
              <div className="code-review-node-identity-title">
                {data.codeReviewRepo}#{data.codeReviewPrNumber} — {data.codeReviewPrTitle || "Untitled"}
              </div>
              <div className="code-review-node-stat-row">
                <span className="code-review-node-stat-key">State</span>
                <span className="code-review-node-stat-value">{data.codeReviewPrState || "unknown"}</span>
              </div>
              <div className="code-review-node-stat-row">
                <span className="code-review-node-stat-key">Range</span>
                <span className="code-review-node-stat-value">
                  {data.codeReviewBaseRef || "?"} → {data.codeReviewHeadRef || "?"}
                </span>
              </div>
              <div className="code-review-node-stat-row">
                <span className="code-review-node-stat-key">Change</span>
                <span className="code-review-node-stat-value">
                  {data.codeReviewChangedFiles} files · +{data.codeReviewAdditions}/-{data.codeReviewDeletions}
                </span>
              </div>
              {(data.codeReviewDiffTruncated || data.codeReviewFilesTruncated) && (
                <p className="code-review-node-banner-warning" role="note">
                  Large pull request: the review covers a truncated excerpt, not the full diff.
                </p>
              )}
            </div>
          ) : (
            <p className="code-review-node-empty">No pull request fetched yet.</p>
          )}

          <div className="code-review-node-inline-row">
            <button type="button" disabled={!canRun} onClick={data.onRun}>
              Run Review
            </button>
            {data.pendingRequestId && (
              <button type="button" onClick={() => data.onCancel()} title="Cancel Review Lens request">
                Cancel
              </button>
            )}
          </div>

          {data.codeReviewError && (
            <p className="code-review-node-banner-error" role="alert">
              {data.codeReviewError}
            </p>
          )}
        </div>
      )}

      {activeTab === "walkthrough" && (
        <div
          className="code-review-node-walkthrough-tab"
          role="tabpanel"
          id="code-review-panel-walkthrough"
          aria-labelledby="code-review-tab-walkthrough"
        >
          {data.codeReviewWalkthrough.length > 0 ? (
            <ol className="code-review-node-walkthrough-list">
              {data.codeReviewWalkthrough.map((group, index) => (
                <li key={`${group.groupTitle}-${index}`} className="code-review-node-walkthrough-group">
                  <div className="code-review-node-walkthrough-title">
                    {index + 1}. {group.groupTitle}
                  </div>
                  <div className="code-review-node-walkthrough-paths">{group.paths.join(", ")}</div>
                  <p className="code-review-node-walkthrough-explanation">{group.explanation}</p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="code-review-node-empty">No walkthrough yet - run a review first.</p>
          )}

          {data.codeReviewState !== "draft" &&
            (diffFetchError ? (
              <>
                <p className="code-review-node-banner-error" role="alert">{diffFetchError}</p>
                {/* Wrapped: every button rule in styles.css is scoped to
                    .code-review-node-inline-row, so this one rendered as a
                    raw user-agent button in the middle of a styled card. */}
                <div className="code-review-node-inline-row">
                  <button type="button" onClick={runDiffFetch}>
                    Retry
                  </button>
                </div>
              </>
            ) : fetchedDiffText === null ? (
              <p className="code-review-node-empty" role="status" aria-live="polite">
                Loading diff…
              </p>
            ) : (
              // The unified diff through the same NodeMarkdown pipeline as
              // GitlinkNodeView's own proposal diff (fenced ```diff for
              // colorization, still inert text) - never raw HTML.
              <div className="chat-node-content code-review-node-diff-markdown">
                <NodeMarkdown content={toDiffFence(fetchedDiffText)} />
              </div>
            ))}

          <div className="code-review-node-qa">
            <span className="code-review-node-field-label">Ask about this diff</span>
            <div className="code-review-node-inline-row">
              <input
                type="text"
                className="code-review-node-input"
                value={questionDraft}
                onChange={(event) => setQuestionDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    askQuestion();
                  }
                }}
                placeholder="e.g. Where is authentication checked?"
                aria-label="Ask about this diff"
              />
              <button type="button" disabled={busy || !questionDraft.trim() || data.codeReviewState === "draft"} onClick={askQuestion}>
                Ask
              </button>
            </div>
            {data.codeReviewQa.length > 0 && (
              <ul className="code-review-node-qa-list">
                {data.codeReviewQa.map((entry, index) => (
                  <li key={index} className="code-review-node-qa-entry">
                    <div className="code-review-node-qa-question">{entry.question}</div>
                    <div className="chat-node-content code-review-node-qa-answer">
                      <NodeMarkdown content={entry.answer} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {activeTab === "findings" && (
        <div
          className="code-review-node-findings-tab"
          role="tabpanel"
          id="code-review-panel-findings"
          aria-labelledby="code-review-tab-findings"
        >
          {/* Three states, not two. `verdict === "none"` alone conflated
              "never reviewed" with "reviewed, but by the deterministic
              pre-screen, which does not grade" - the engine deliberately
              reports no verdict and no score for a fallback review. So a
              pre-screen that HAD found a hard-coded secret rendered
              "No review yet - run a review first." directly above its own
              findings list. codeReviewState is already on the wire and is
              the field that actually distinguishes them. */}
          {data.codeReviewVerdict !== "none" ? (
            <div className={`code-review-node-verdict code-review-node-verdict-${data.codeReviewVerdict}`} role="status">
              <span className="code-review-node-verdict-label">{verdictLabel(data.codeReviewVerdict)}</span>
              <span className="code-review-node-verdict-score">{data.codeReviewQualityScore}/100</span>
              {data.codeReviewRisk && (
                <span className="code-review-node-verdict-risk">{data.codeReviewRisk} risk</span>
              )}
            </div>
          ) : data.codeReviewState === "reviewed" ? (
            <div className="code-review-node-verdict code-review-node-verdict-none" role="status">
              <span className="code-review-node-verdict-label">Not assessed</span>
              <span className="code-review-node-verdict-risk">
                deterministic pre-screen only - run the review again for a graded assessment
              </span>
            </div>
          ) : (
            <p className="code-review-node-empty">No review yet - run a review first.</p>
          )}

          {data.codeReviewTitle && (
            <p className="code-review-node-review-title">{data.codeReviewTitle}</p>
          )}
          {data.codeReviewOverview && <p className="code-review-node-overview">{data.codeReviewOverview}</p>}

          {Object.keys(data.codeReviewScores).length > 0 && (
            <div className="code-review-node-scorecard">
              {Object.entries(data.codeReviewScores).map(([category, score]) => (
                <div key={category} className="code-review-node-stat-row">
                  <span className="code-review-node-stat-key">{categoryLabel(category)}</span>
                  <span className="code-review-node-stat-value">{score}/100</span>
                </div>
              ))}
            </div>
          )}

          {data.codeReviewQualitySummary && (
            <p className="code-review-node-overview">
              {data.codeReviewQualitySummary}
              {data.codeReviewConfidence && ` (model confidence: ${data.codeReviewConfidence})`}
            </p>
          )}

          {visibleErrors.map((error) => (
            <article key={error.id} className="code-review-node-finding">
              <div className="code-review-node-finding-head">
                <span className={`code-review-node-tier code-review-node-tier-${error.tier}`}>
                  {tierLabel(error.tier)}
                </span>
                <span className="code-review-node-finding-title">{error.title}</span>
              </div>
              <div className="code-review-node-finding-meta">
                {error.severity} · {error.kind} · {findingLocation(error.path, error.line)}
              </div>
              <p className="code-review-node-finding-text">{error.evidence}</p>
              <p className="code-review-node-finding-text">
                <strong>Fix:</strong> {error.fix}
              </p>
              <div className="code-review-node-inline-row">
                <button
                  type="button"
                  aria-label={`Copy error: ${error.title}`}
                  onClick={() => copyText(`[${error.severity}] ${error.title} (${findingLocation(error.path, error.line)}): ${error.evidence} Fix: ${error.fix}`)}
                >
                  Copy
                </button>
                <button
                  type="button"
                  aria-label={`Dismiss error: ${error.title}`}
                  onClick={() => data.onDismissFinding(error.id)}
                >
                  Dismiss
                </button>
              </div>
            </article>
          ))}

          {visibleFindings.map((finding) => (
            <article key={finding.id} className="code-review-node-finding">
              <div className="code-review-node-finding-head">
                <span className={`code-review-node-tier code-review-node-tier-${finding.tier}`}>
                  {tierLabel(finding.tier)}
                </span>
                <span className="code-review-node-finding-title">{finding.title}</span>
              </div>
              <div className="code-review-node-finding-meta">
                {finding.severity} · {finding.category} · {findingLocation(finding.path, finding.line)}
              </div>
              <p className="code-review-node-finding-text">{finding.evidence}</p>
              <p className="code-review-node-finding-text">{finding.impact}</p>
              <p className="code-review-node-finding-text">
                <strong>Recommendation:</strong> {finding.recommendation}
              </p>
              <div className="code-review-node-inline-row">
                <button
                  type="button"
                  aria-label={`Copy finding: ${finding.title}`}
                  onClick={() =>
                    copyText(
                      `[${finding.severity}] ${finding.title} (${findingLocation(finding.path, finding.line)}): ${finding.evidence} Recommendation: ${finding.recommendation}`,
                    )
                  }
                >
                  Copy
                </button>
                <button
                  type="button"
                  aria-label={`Dismiss finding: ${finding.title}`}
                  onClick={() => data.onDismissFinding(finding.id)}
                >
                  Dismiss
                </button>
              </div>
            </article>
          ))}

          {dismissedCount > 0 && (
            <p className="code-review-node-file-count">
              {dismissedCount} dismissed {dismissedCount === 1 ? "finding" : "findings"} - undo
              restores {dismissedCount === 1 ? "it" : "them"}.
            </p>
          )}
        </div>
      )}
    </NodeShell>
  );
}

/** ADR-011 stage 11.1 comparator helpers, mirroring GitlinkNodeView's own
 * shape-aware approach: toFlowNodes may mint a fresh array/object for one
 * of these fields on every snapshot even when its contents are unchanged,
 * so a plain `===` would be "too tight" for every such field. Only the
 * fields this view actually reads are compared. */
function stringArraysEqual(a: readonly string[], b: readonly string[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function codeReviewScoresEqual(a: Record<string, string>, b: Record<string, string>): boolean {
  if (a === b) return true;
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  for (const key of aKeys) {
    if (a[key] !== b[key]) return false;
  }
  return true;
}

function walkthroughEqual(
  a: readonly CodeReviewWalkthroughGroup[],
  b: readonly CodeReviewWalkthroughGroup[],
): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (
      a[i].groupTitle !== b[i].groupTitle ||
      a[i].explanation !== b[i].explanation ||
      !stringArraysEqual(a[i].paths, b[i].paths)
    ) {
      return false;
    }
  }
  return true;
}

function findingsEqual(a: readonly unknown[], b: readonly unknown[]): boolean {
  // Unlike GitlinkNodeView's own operation/path-only row comparison, every
  // finding field here reaches the DOM (title, severity, evidence, impact,
  // recommendation/fix all render) - and a re-review re-mints the same
  // f1../e1.. ids with potentially different content. A serialized compare
  // is the only exact check; the arrays are capped at 12/10 small objects,
  // so this costs microseconds per snapshot.
  if (a === b) return true;
  return JSON.stringify(a) === JSON.stringify(b);
}

function qaEqual(a: readonly CodeReviewQa[], b: readonly CodeReviewQa[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].question !== b[i].question || a[i].answer !== b[i].answer) return false;
  }
  return true;
}

/** ADR-011 stage 11.1: every prop this view actually reads, compared.
 * `codeReviewPrUrl` is intentionally OMITTED: per this file's own
 * "state-ownership discipline" module doc, it seeds the local URL draft
 * ONCE on mount and is never read again afterward - comparing it would
 * only cause spurious re-renders that render byte-identical output
 * (same reasoning GitlinkNodeView's own comparator applies to
 * gitlinkScopeMode/gitlinkSelectedPaths/gitlinkTaskPrompt). */
function codeReviewNodeDataAreEqual(prev: CodeReviewNodeData, next: CodeReviewNodeData): boolean {
  return (
    prev.codeReviewRepo === next.codeReviewRepo &&
    prev.codeReviewPrNumber === next.codeReviewPrNumber &&
    prev.codeReviewPrTitle === next.codeReviewPrTitle &&
    prev.codeReviewPrState === next.codeReviewPrState &&
    prev.codeReviewBaseRef === next.codeReviewBaseRef &&
    prev.codeReviewHeadRef === next.codeReviewHeadRef &&
    prev.codeReviewAdditions === next.codeReviewAdditions &&
    prev.codeReviewDeletions === next.codeReviewDeletions &&
    prev.codeReviewChangedFiles === next.codeReviewChangedFiles &&
    prev.codeReviewFilesTruncated === next.codeReviewFilesTruncated &&
    prev.codeReviewDiffTruncated === next.codeReviewDiffTruncated &&
    prev.codeReviewDiffChars === next.codeReviewDiffChars &&
    prev.codeReviewDiffVersion === next.codeReviewDiffVersion &&
    prev.codeReviewState === next.codeReviewState &&
    walkthroughEqual(prev.codeReviewWalkthrough, next.codeReviewWalkthrough) &&
    findingsEqual(prev.codeReviewFindings, next.codeReviewFindings) &&
    findingsEqual(prev.codeReviewErrors, next.codeReviewErrors) &&
    stringArraysEqual(prev.codeReviewDismissedIds, next.codeReviewDismissedIds) &&
    prev.codeReviewTitle === next.codeReviewTitle &&
    prev.codeReviewOverview === next.codeReviewOverview &&
    prev.codeReviewConfidence === next.codeReviewConfidence &&
    codeReviewScoresEqual(prev.codeReviewScores, next.codeReviewScores) &&
    prev.codeReviewQualityScore === next.codeReviewQualityScore &&
    prev.codeReviewVerdict === next.codeReviewVerdict &&
    prev.codeReviewRisk === next.codeReviewRisk &&
    prev.codeReviewQualitySummary === next.codeReviewQualitySummary &&
    qaEqual(prev.codeReviewQa, next.codeReviewQa) &&
    prev.codeReviewError === next.codeReviewError &&
    prev.isCollapsed === next.isCollapsed &&
    prev.pendingRequestId === next.pendingRequestId &&
    prev.onSetPrUrl === next.onSetPrUrl &&
    prev.onFetchDiff === next.onFetchDiff &&
    prev.onFetchDiffText === next.onFetchDiffText &&
    prev.onRun === next.onRun &&
    prev.onCancel === next.onCancel &&
    prev.onAsk === next.onAsk &&
    prev.onDismissFinding === next.onDismissFinding &&
    prev.onToggleCollapse === next.onToggleCollapse &&
    prev.onDelete === next.onDelete
  );
}

function codeReviewNodePropsAreEqual(
  prev: Readonly<NodeProps<CodeReviewFlowNode>>,
  next: Readonly<NodeProps<CodeReviewFlowNode>>,
): boolean {
  return prev.id === next.id && prev.selected === next.selected && codeReviewNodeDataAreEqual(prev.data, next.data);
}

export const CodeReviewNodeView = memo(CodeReviewNodeViewImpl, codeReviewNodePropsAreEqual);
