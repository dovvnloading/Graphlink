import type { Node, NodeProps } from "@xyflow/react";
import { memo, useEffect, useRef, useState } from "react";
import { Dialog, useOverlays } from "../overlays/overlays";
import type { MenuPosition } from "./menuPosition";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

/**
 * The Gitlink node (Qt-removal plan R5.3) - the Gitlink plugin's React card.
 * Same overall shell as every plugin-node sibling (ArtifactNodeView/
 * WebResearchNodeView): collapse/expand OR-ed with LOD, a card menu with
 * outside-click/Escape dismiss, the shared NodeMarkdown.tsx renderer (node
 * redesign stage 1), no dock-to-parent action. Unlike its siblings,
 * this node is a three-tab workflow (Setup / Context / Proposal) rather than
 * one linear scroll - the underlying task (pick a repo, scope a context,
 * generate a change set, review a diff, apply it to real local files) has
 * four genuinely different phases, and a tabbed layout is the least-surprise
 * way to keep all of them reachable in one card without an ever-growing
 * single column.
 *
 * State-ownership discipline (read this before touching any input in Setup):
 * repo/branch/scope-mode/local-root/task-prompt/file-selection all live in
 * LOCAL component state, initialized ONCE from the incoming scene snapshot
 * and never re-synced afterward - the exact non-clobbering posture
 * WebResearchNodeView's own query draft already established (a remote update
 * mid-type must never stomp what the user is currently typing/selecting).
 * scope_mode and the selected-paths set are never independently mirrored to
 * the server at all - they exist purely to be read at the moment Build
 * Context or Generate Change Set is clicked, then handed over as plain
 * call arguments. Committing state to the server is always an explicit
 * button (or blur/Enter) action, never a live keystroke-by-keystroke sync.
 *
 * The Context tab's full XML body is fetched lazily (data.onFetchContext())
 * the first time the tab is opened after data.gitlinkContextVersion changes
 * to a new value (a monotonic per-node counter bumped by the backend on
 * every successful Build Context call), then cached in local state - never
 * refetched on a bare tab-switch back and forth with the same version. This
 * is keyed on the version counter rather than data.gitlinkContextSummary
 * because two distinct builds can produce an identical summary string (e.g.
 * "Scanned 1 files." for two different single-file selections), which would
 * otherwise silently skip a required refetch (R5.3 post-review FIX 6).
 *
 * Security note, not a style preference: the Proposal tab renders BOTH
 * data.gitlinkProposalMarkdown and data.gitlinkPreviewText (a unified diff)
 * through the exact same NodeMarkdown.tsx pipeline every sibling node view
 * uses - no rehype-raw, no dangerouslySetInnerHTML anywhere in this file or
 * that one. The diff text is wrapped in
 * a fenced ```diff code block first (toDiffFence, mirroring CodeNodeView's
 * own toFencedCodeBlock) purely so rehype-highlight can colorize it - it is
 * never treated as anything but inert text by the markdown pipeline, exactly
 * like the proposal markdown itself (which can indirectly embed untrusted
 * repo content by way of the model).
 *
 * Apply confirmation: the first plugin in this codebase that mutates real
 * local files, so Apply does not fire data.onApply directly - it opens a
 * small in-canvas confirmation built from the EXISTING R2.1 overlay
 * primitive (Dialog/useOverlays from ../overlays/overlays, the same one
 * HelpDialog/SettingsDialog/ChatLibraryDialog already render through), keyed
 * uniquely per node instance (`gitlink-apply-${id}`) so two Gitlink nodes on
 * the same canvas can never collide over one dialog name. The confirmation's
 * own Yes button is the ONLY call site for data.onApply, and it is always
 * called with the current data.gitlinkChangeFingerprint prop value verbatim
 * - never anything computed here. This view has no fingerprinting logic of
 * its own, full stop.
 */

export interface GitlinkPendingChangeRow {
  path: string;
  operation: string;
  reason: string;
  content?: string | null;
}

export interface GitlinkNodeData extends Record<string, unknown> {
  gitlinkRepo: string;
  gitlinkBranch: string;
  gitlinkScopeMode: string;
  gitlinkLocalRoot: string;
  gitlinkRepoFilePaths: string[];
  gitlinkSelectedPaths: string[];
  gitlinkTaskPrompt: string;
  gitlinkContextStats: Record<string, string>;
  gitlinkContextSummary: string;
  // Monotonic per-node counter bumped by the backend on every successful
  // Build Context call (see R5.3 post-review FIX 6) - used instead of
  // gitlinkContextSummary to key the Context tab's lazy-fetch guard, since
  // two different builds can produce an identical human-readable summary.
  gitlinkContextVersion: number;
  gitlinkProposalMarkdown: string;
  gitlinkPendingChanges: GitlinkPendingChangeRow[];
  gitlinkPreviewText: string;
  gitlinkChangeFingerprint: string | null;
  gitlinkChangeState: string;
  gitlinkError: string;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onFetchRepositories: () => Promise<string[]>;
  onLoadTree: (repo: string, branch: string) => void;
  onSetLocalRoot: (localRoot: string) => void;
  onBrowseLocalRoot: () => void;
  onImportSnapshot: (repo: string, branch: string) => void;
  onBuildContext: (scopeMode: string, selectedPaths: string[]) => void;
  onFetchContext: () => Promise<string>;
  onRun: (taskPrompt: string) => void;
  onCancel: () => void;
  onApply: (fingerprint: string) => void;
  onToggleCollapse: () => void;
  onDelete: () => void;
}

export type GitlinkFlowNode = Node<GitlinkNodeData, "gitlink">;

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ThinkingNodeMenu/DocumentNodeMenu/ConversationNodeMenu/
 * WebResearchNodeMenu/ArtifactNodeMenu). */
// -- card-level menu -------------------------------------------------------

function GitlinkNodeMenu({
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

/** Wraps a raw unified diff in a markdown fenced code block tagged `diff` so
 * ReactMarkdown + rehype-highlight can colorize it for free - the same
 * technique CodeNodeView.tsx's own toFencedCodeBlock uses for source code,
 * applied here to diff text instead. Never treated as anything but inert
 * text by the pipeline either way. */
function toDiffFence(diffText: string): string {
  return "```diff\n" + diffText + "\n```";
}

type TabKey = "setup" | "context" | "proposal";
const TABS: { key: TabKey; label: string }[] = [
  { key: "setup", label: "Setup" },
  { key: "context", label: "Context" },
  { key: "proposal", label: "Proposal" },
];

// -- view ----------------------------------------------------------------

function GitlinkNodeViewImpl({ id, data, selected }: NodeProps<GitlinkFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("setup");
  const overlays = useOverlays();
  const applyOverlayName = `gitlink-apply-${id}`;

  // -- Setup tab: local, never-resynced drafts (see module doc above) -----
  const [repoDraft, setRepoDraft] = useState(data.gitlinkRepo);
  const [branchDraft, setBranchDraft] = useState(data.gitlinkBranch);
  const [scopeMode, setScopeMode] = useState(data.gitlinkScopeMode || "selected");
  const [localRootDraft, setLocalRootDraft] = useState(data.gitlinkLocalRoot);
  const [taskPromptDraft, setTaskPromptDraft] = useState(data.gitlinkTaskPrompt);
  const [selectedPaths, setSelectedPaths] = useState<string[]>(data.gitlinkSelectedPaths);
  const [fileFilter, setFileFilter] = useState("");

  // R5.3 post-review FIX 7: selectedPaths above is seeded ONCE from
  // data.gitlinkSelectedPaths and, by design (see the module doc), never
  // re-synced on ordinary prop updates - but a genuinely different repo or
  // branch loading a new tree must invalidate a prior selection, since paths
  // selected against the OLD tree are not guaranteed to exist in the new one
  // and would otherwise silently ride into the next Build Context call.
  // repoBranchRef is initialized from the SAME initial repo/branch values
  // selectedPaths itself was seeded from, so the first effect run after
  // mount sees no change and never clears the just-restored selection.
  const repoBranchRef = useRef(`${data.gitlinkRepo}::${data.gitlinkBranch}`);
  useEffect(() => {
    const current = `${data.gitlinkRepo}::${data.gitlinkBranch}`;
    if (repoBranchRef.current !== current) {
      repoBranchRef.current = current;
      setSelectedPaths([]);
    }
  }, [data.gitlinkRepo, data.gitlinkBranch]);
  const [repoOptions, setRepoOptions] = useState<string[] | null>(null);
  const [repoOptionsLoading, setRepoOptionsLoading] = useState(false);
  const [repoOptionsError, setRepoOptionsError] = useState<string | null>(null);

  const busy = !!data.pendingRequestId;

  function loadTree() {
    const repo = repoDraft.trim();
    if (!repo) return;
    data.onLoadTree(repo, branchDraft.trim());
  }

  function commitLocalRoot() {
    data.onSetLocalRoot(localRootDraft.trim());
  }

  function listRepos() {
    setRepoOptionsLoading(true);
    setRepoOptionsError(null);
    data
      .onFetchRepositories()
      .then((names) => setRepoOptions(names))
      .catch(() => setRepoOptionsError("Could not load repositories."))
      .finally(() => setRepoOptionsLoading(false));
  }

  const filteredPaths = data.gitlinkRepoFilePaths.filter((path) =>
    path.toLowerCase().includes(fileFilter.trim().toLowerCase()),
  );

  function togglePath(path: string) {
    setSelectedPaths((current) =>
      current.includes(path) ? current.filter((p) => p !== path) : [...current, path],
    );
  }

  function selectVisible() {
    setSelectedPaths((current) => Array.from(new Set([...current, ...filteredPaths])));
  }

  function clearSelection() {
    setSelectedPaths([]);
  }

  function runChangeSet() {
    const prompt = taskPromptDraft.trim();
    if (!prompt) return;
    data.onRun(prompt);
  }

  // -- Context tab: lazy-fetch-once-per-version ----------------------------
  // The re-fetch guard is a ref, not state: it exists purely to gate the
  // effect body (never rendered on its own), so mutating it directly avoids
  // the classic "setState mirroring a dependency" anti-pattern - only the
  // actual fetch RESULT (fetchedContextXml) needs to be React state, since
  // that is the one value this component renders.
  const [fetchedContextXml, setFetchedContextXml] = useState<string | null>(null);
  // ADR-003 stage 3.1 review-fix: onFetchContext() previously had no .catch()
  // at all, so a rejection left this tab stuck on "Loading context…" forever
  // with no visible error and no way to retry - matching listRepos()'s own
  // repoOptionsError pattern above.
  const [contextFetchError, setContextFetchError] = useState<string | null>(null);
  // R5.3 post-review FIX 6: keyed on data.gitlinkContextVersion (a monotonic
  // per-node counter, defaulting to 0, bumped by the backend on every
  // successful Build Context call) rather than data.gitlinkContextSummary -
  // the summary is a human-readable string that two DIFFERENT builds can
  // produce identically (e.g. "Scanned 1 files." for two different
  // single-file selections), which would make this ref wrongly believe
  // nothing changed and skip a required refetch. The sentinel starts at
  // `null` (never a legal version value, since the field is always a
  // non-negative number) so the very first build (version 1) always passes
  // the comparison below; a node that has never had context built at all
  // stays at version 0 and never reaches this ref check, because the
  // empty-summary early return just below still gates on that.
  const fetchedForVersionRef = useRef<number | null>(null);
  // R5.3 post-review FIX 5: guards against an OLDER in-flight fetch resolving
  // AFTER a newer one (e.g. the user rebuilds context twice in quick
  // succession) and clobbering the correct, newer XML with stale content.
  // This is deliberately separate from fetchedForVersionRef above, which only
  // decides whether to START a new fetch - this ref instead gates what
  // happens when a fetch RESOLVES, regardless of resolution order.
  const contextFetchSeqRef = useRef(0);

  function runContextFetch() {
    setFetchedContextXml(null);
    setContextFetchError(null);
    const seq = ++contextFetchSeqRef.current;
    data
      .onFetchContext()
      .then((xml) => {
        // A newer fetch has since been kicked off - this result is stale,
        // discard it silently rather than overwriting the newer content.
        if (contextFetchSeqRef.current !== seq) return;
        setFetchedContextXml(xml);
      })
      .catch(() => {
        if (contextFetchSeqRef.current !== seq) return;
        setContextFetchError("Could not load context.");
      });
  }

  useEffect(() => {
    if (activeTab !== "context") return;
    if (!data.gitlinkContextSummary) return;
    const version = data.gitlinkContextVersion ?? 0;
    if (fetchedForVersionRef.current === version) return;
    fetchedForVersionRef.current = version;
    runContextFetch();
    // data.onFetchContext is a fresh closure every render (see SceneCanvas's
    // toFlowNodes) - depending on it would refetch on every unrelated
    // re-render, so it is deliberately omitted; fetchedForVersionRef is the
    // real re-fetch guard.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, data.gitlinkContextVersion]);

  // -- Proposal tab / Apply confirmation -----------------------------------
  const applying = data.gitlinkChangeState === "applying" || busy;
  const canApply = data.gitlinkPendingChanges.length > 0 && !!data.gitlinkChangeFingerprint;

  return (
    <NodeShell
      kindClassName="gitlink-node"
      selected={!!selected}
      collapsed={collapsed}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
      header={
        <div className="scene-node-title chat-node-role">
          <span>Gitlink</span>
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
      bodyClassName="gitlink-node-content"
      menu={
        menuPosition && (
          <GitlinkNodeMenu
            position={menuPosition}
            isCollapsed={data.isCollapsed}
            onToggleCollapse={data.onToggleCollapse}
            onDelete={data.onDelete}
            onClose={() => setMenuPosition(null)}
          />
        )
      }
    >
          <div className="gitlink-node-tabs" role="tablist" aria-label="Gitlink sections">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.key}
                className={`gitlink-node-tab${activeTab === tab.key ? " active" : ""}`}
                onClick={() => setActiveTab(tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {activeTab === "setup" && (
            <div className="gitlink-node-setup-tab" role="tabpanel">
              <div className="gitlink-node-field-row">
                <input
                  type="text"
                  className="gitlink-node-input"
                  value={repoDraft}
                  onChange={(event) => setRepoDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      loadTree();
                    }
                  }}
                  placeholder="owner/repo"
                  aria-label="Repository"
                />
                <input
                  type="text"
                  className="gitlink-node-input"
                  value={branchDraft}
                  onChange={(event) => setBranchDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      loadTree();
                    }
                  }}
                  placeholder="branch"
                  aria-label="Branch"
                />
                <div className="gitlink-node-inline-row">
                  <button type="button" disabled={busy} onClick={listRepos}>
                    {repoOptionsLoading ? "Loading…" : "List My Repos"}
                  </button>
                  <button type="button" disabled={busy || !repoDraft.trim()} onClick={loadTree}>
                    Load Repo Tree
                  </button>
                </div>
                {repoOptionsError && <p className="gitlink-node-banner-error">{repoOptionsError}</p>}
                {repoOptions && repoOptions.length > 0 && (
                  <ul className="gitlink-node-repo-options">
                    {repoOptions.map((name) => (
                      <li key={name}>
                        <button type="button" onClick={() => setRepoDraft(name)}>
                          {name}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <label className="gitlink-node-field-row">
                <span className="gitlink-node-field-label">Context scope</span>
                <select
                  className="gitlink-node-select"
                  value={scopeMode}
                  onChange={(event) => setScopeMode(event.target.value)}
                >
                  <option value="selected">Selected files</option>
                  <option value="full">Full repo</option>
                </select>
              </label>

              <div className="gitlink-node-field-row">
                <label className="gitlink-node-field-row">
                  <span className="gitlink-node-field-label">Local root</span>
                  <input
                    type="text"
                    className="gitlink-node-input"
                    value={localRootDraft}
                    onChange={(event) => setLocalRootDraft(event.target.value)}
                    onBlur={commitLocalRoot}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        // Do NOT call commitLocalRoot() here too - .blur() below
                        // synchronously fires the onBlur={commitLocalRoot}
                        // handler above, so calling it directly here as well
                        // would dispatch the WS intent twice per Enter press
                        // (R5.3 post-review FIX 8).
                        (event.target as HTMLInputElement).blur();
                      }
                    }}
                    placeholder="C:\path\to\local\checkout"
                    aria-label="Local root"
                  />
                </label>
                <div className="gitlink-node-inline-row">
                  <button type="button" disabled={busy} onClick={data.onBrowseLocalRoot}>
                    Browse…
                  </button>
                </div>
              </div>

              <div className="gitlink-node-inline-row">
                <button
                  type="button"
                  disabled={busy || !repoDraft.trim()}
                  onClick={() => data.onImportSnapshot(repoDraft.trim(), branchDraft.trim())}
                >
                  Import Repo Snapshot
                </button>
              </div>

              <div className="gitlink-node-file-tree">
                <input
                  type="text"
                  className="gitlink-node-input"
                  value={fileFilter}
                  onChange={(event) => setFileFilter(event.target.value)}
                  placeholder="Filter files…"
                  aria-label="Filter files"
                />
                <div className="gitlink-node-inline-row">
                  <button type="button" onClick={selectVisible} disabled={filteredPaths.length === 0}>
                    Select Visible
                  </button>
                  <button type="button" onClick={clearSelection} disabled={selectedPaths.length === 0}>
                    Clear Selection
                  </button>
                </div>
                <ul className="gitlink-node-file-list">
                  {filteredPaths.length === 0 ? (
                    <li className="gitlink-node-empty">No files loaded yet.</li>
                  ) : (
                    filteredPaths.map((path) => (
                      <li key={path}>
                        <label className="gitlink-node-file-row">
                          <input
                            type="checkbox"
                            checked={selectedPaths.includes(path)}
                            onChange={() => togglePath(path)}
                          />
                          {path}
                        </label>
                      </li>
                    ))
                  )}
                </ul>
                <p className="gitlink-node-file-count">{selectedPaths.length} selected</p>
              </div>

              <div className="gitlink-node-inline-row">
                <button type="button" disabled={busy} onClick={() => data.onBuildContext(scopeMode, selectedPaths)}>
                  Build Context
                </button>
              </div>

              <div className="gitlink-node-field-row">
                <textarea
                  className="gitlink-node-input gitlink-node-task-prompt"
                  value={taskPromptDraft}
                  onChange={(event) => setTaskPromptDraft(event.target.value)}
                  placeholder="Describe the change to make…"
                  aria-label="Task prompt"
                  rows={2}
                  spellCheck
                />
                <div className="gitlink-node-inline-row">
                  <button type="button" disabled={busy || !taskPromptDraft.trim()} onClick={runChangeSet}>
                    Generate Change Set
                  </button>
                  {data.pendingRequestId && (
                    <button type="button" onClick={() => data.onCancel()} title="Cancel Gitlink request">
                      Cancel
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {activeTab === "context" && (
            <div className="gitlink-node-context-tab" role="tabpanel">
              {Object.keys(data.gitlinkContextStats).length > 0 && (
                <div className="gitlink-node-context-stats">
                  {Object.entries(data.gitlinkContextStats).map(([key, value]) => (
                    <div key={key} className="gitlink-node-stat-row">
                      <span className="gitlink-node-stat-key">{key}</span>
                      <span className="gitlink-node-stat-value">{value}</span>
                    </div>
                  ))}
                </div>
              )}
              {data.gitlinkContextSummary ? (
                <>
                  <p className="gitlink-node-context-summary">{data.gitlinkContextSummary}</p>
                  {contextFetchError ? (
                    <>
                      <p className="gitlink-node-banner-error">{contextFetchError}</p>
                      <button type="button" onClick={runContextFetch}>
                        Retry
                      </button>
                    </>
                  ) : (
                    // Machine-generated XML, rendered as plain preformatted
                    // text - never run through the markdown pipeline.
                    <pre className="gitlink-node-context-xml">{fetchedContextXml ?? "Loading context…"}</pre>
                  )}
                </>
              ) : (
                <p className="gitlink-node-empty">No context built yet.</p>
              )}
            </div>
          )}

          {activeTab === "proposal" && (
            <div className="gitlink-node-proposal-tab" role="tabpanel">
              {data.gitlinkProposalMarkdown ? (
                <div className="chat-node-content gitlink-node-proposal-markdown">
                  <NodeMarkdown content={data.gitlinkProposalMarkdown} />
                </div>
              ) : (
                <p className="gitlink-node-empty">No change set generated yet.</p>
              )}

              {data.gitlinkPreviewText && (
                <div className="chat-node-content gitlink-node-proposal-diff">
                  <NodeMarkdown content={toDiffFence(data.gitlinkPreviewText)} />
                </div>
              )}

              {data.gitlinkChangeState === "previewed" && data.gitlinkError && (
                <div className="gitlink-node-banner-error" role="alert">
                  {data.gitlinkError}
                </div>
              )}

              <div className="gitlink-node-apply-row">
                <button
                  type="button"
                  className="gitlink-node-apply-btn"
                  disabled={!canApply || applying}
                  onClick={() => overlays.open(applyOverlayName, "dialog")}
                >
                  Apply
                </button>
              </div>

              <Dialog name={applyOverlayName} title="Apply Changes?" className="gitlink-node-apply-dialog">
                <p>
                  Write {data.gitlinkPendingChanges.length} file{" "}
                  {data.gitlinkPendingChanges.length === 1 ? "change" : "changes"} into{" "}
                  {data.gitlinkLocalRoot}?
                </p>
                <ul className="gitlink-node-apply-file-list">
                  {data.gitlinkPendingChanges.map((change, index) => (
                    <li key={index}>
                      {change.operation} — {change.path}
                    </li>
                  ))}
                </ul>
                <div className="gitlink-node-apply-dialog-actions">
                  <button type="button" onClick={() => overlays.close()}>
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={applying || !data.gitlinkChangeFingerprint}
                    onClick={() => {
                      if (data.gitlinkChangeFingerprint) data.onApply(data.gitlinkChangeFingerprint);
                      overlays.close();
                    }}
                  >
                    Yes
                  </button>
                </div>
              </Dialog>
            </div>
          )}
    </NodeShell>
  );
}

/** ADR-011 stage 11.1 comparator helpers, mirroring WebResearchNodeView's own
 * shape-aware approach: toFlowNodes may mint a fresh array/object for one of
 * these fields on every snapshot even when its contents are unchanged, so a
 * plain `===` would be "too tight" for every such field. Only the fields this
 * view actually reads off each row/entry are compared (`reason`/`content` on
 * GitlinkPendingChangeRow never reach the DOM from this file - only
 * `operation`/`path` do, in the Apply confirmation list). */
function stringArraysEqual(a: readonly string[], b: readonly string[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function gitlinkContextStatsEqual(a: Record<string, string>, b: Record<string, string>): boolean {
  if (a === b) return true;
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  for (const key of aKeys) {
    if (a[key] !== b[key]) return false;
  }
  return true;
}

function gitlinkPendingChangesEqual(
  a: readonly GitlinkPendingChangeRow[],
  b: readonly GitlinkPendingChangeRow[],
): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].operation !== b[i].operation || a[i].path !== b[i].path) return false;
  }
  return true;
}

/** ADR-011 stage 11.1: every prop this view actually reads, compared.
 * `gitlinkScopeMode`, `gitlinkSelectedPaths`, and `gitlinkTaskPrompt` are
 * intentionally OMITTED: per this file's own "state-ownership discipline"
 * module doc, each seeds a local draft `useState` ONCE on mount and is never
 * read again afterward (no effect, no JSX, no closure references the prop
 * itself past that one seed line) - comparing them would only cause spurious
 * re-renders that render byte-identical output, never fix a missed one (same
 * reasoning WebResearchNodeView's own comparator applies to
 * researchActiveSourceId). `gitlinkRepo`/`gitlinkBranch` are NOT in that same
 * bucket despite also seeding drafts - they're read again every render by the
 * repo/branch-change effect that resets the file-tree selection (FIX 7), so
 * they're compared. `gitlinkLocalRoot` is read directly in the Apply dialog's
 * body text, not just as a seed, so it's compared too. */
function gitlinkNodeDataAreEqual(prev: GitlinkNodeData, next: GitlinkNodeData): boolean {
  return (
    prev.gitlinkRepo === next.gitlinkRepo &&
    prev.gitlinkBranch === next.gitlinkBranch &&
    prev.gitlinkLocalRoot === next.gitlinkLocalRoot &&
    stringArraysEqual(prev.gitlinkRepoFilePaths, next.gitlinkRepoFilePaths) &&
    gitlinkContextStatsEqual(prev.gitlinkContextStats, next.gitlinkContextStats) &&
    prev.gitlinkContextSummary === next.gitlinkContextSummary &&
    prev.gitlinkContextVersion === next.gitlinkContextVersion &&
    prev.gitlinkProposalMarkdown === next.gitlinkProposalMarkdown &&
    gitlinkPendingChangesEqual(prev.gitlinkPendingChanges, next.gitlinkPendingChanges) &&
    prev.gitlinkPreviewText === next.gitlinkPreviewText &&
    prev.gitlinkChangeFingerprint === next.gitlinkChangeFingerprint &&
    prev.gitlinkChangeState === next.gitlinkChangeState &&
    prev.gitlinkError === next.gitlinkError &&
    prev.isCollapsed === next.isCollapsed &&
    prev.pendingRequestId === next.pendingRequestId &&
    prev.onFetchRepositories === next.onFetchRepositories &&
    prev.onLoadTree === next.onLoadTree &&
    prev.onSetLocalRoot === next.onSetLocalRoot &&
    prev.onBrowseLocalRoot === next.onBrowseLocalRoot &&
    prev.onImportSnapshot === next.onImportSnapshot &&
    prev.onBuildContext === next.onBuildContext &&
    prev.onFetchContext === next.onFetchContext &&
    prev.onRun === next.onRun &&
    prev.onCancel === next.onCancel &&
    prev.onApply === next.onApply &&
    prev.onToggleCollapse === next.onToggleCollapse &&
    prev.onDelete === next.onDelete
  );
}

/** `id` is read here (it keys the per-instance Apply-confirmation overlay
 * name, `gitlink-apply-${id}`), unlike some sibling views - so it's compared
 * alongside `selected` and every `data` field above. */
function gitlinkNodePropsAreEqual(
  prev: Readonly<NodeProps<GitlinkFlowNode>>,
  next: Readonly<NodeProps<GitlinkFlowNode>>,
): boolean {
  return (
    prev.id === next.id &&
    prev.selected === next.selected &&
    gitlinkNodeDataAreEqual(prev.data, next.data)
  );
}

export const GitlinkNodeView = memo(GitlinkNodeViewImpl, gitlinkNodePropsAreEqual);
