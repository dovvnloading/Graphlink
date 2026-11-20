import { memo, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import type { Node, NodeProps } from "@xyflow/react";
import { CollapseToggleButton } from "./CollapseToggleButton";
import type { MenuPosition } from "./menuPosition";
import { NodeMenu } from "./NodeMenu";
import { NodeHeading } from "./NodeHeading";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";
import { HarnessWorkspaceFiles } from "./AgentWorkspaceBrowser";
import type { HarnessWorkspaceListing } from "./harnessWorkspaceFiles";

/**
 * PLAN-2026-08-24 H1: the workspace agent node. Everything rendered here
 * is real backend state off the wire row (HarnessState -> the harness*
 * fields); every button fires a real intent through sceneStore. The
 * conversation itself deliberately never crosses the wire (it lives in
 * the node's workspace transcript) - what this card shows is the LAST
 * task's prompt and reply plus the run's own activity log, the same
 * "render surface only" posture the wire contract documents.
 *
 * Layout and class vocabulary mirror PlanNodeView (status band, detail
 * line, the shared chat-node-tool-invocations activity disclosure,
 * plan-node button classes) rather than inventing a parallel idiom.
 */

export interface HarnessActivityRowData {
  tool: string;
  summary: string;
  outcome: string;
  elapsedMs: number;
}

export interface HarnessPlanStepData {
  text: string;
  status: string;
}

export interface HarnessNodeData extends Record<string, unknown> {
  harnessGoal: string;
  harnessReply: string;
  harnessStatus: string;
  harnessStatusDetail: string;
  harnessRunId: string;
  harnessActivity: HarnessActivityRowData[];
  harnessAwaitingApproval: boolean;
  harnessApprovalToolName: string;
  harnessApprovalSummary: string;
  harnessApprovalId: string;
  harnessApprovalSessionOffered: boolean;
  harnessPlan: HarnessPlanStepData[];
  harnessAwaitingQuestion: boolean;
  harnessQuestion: string;
  harnessContextTokens: number;
  harnessMaxContextTokens: number;
  harnessCompactions: number;
  harnessWorkspacePath: string;
  harnessWorkspaceActive: string;
  harnessShell: string;
  harnessExecutionProfile: string;
  harnessMaxTurns: number;
  harnessSpentTurns: number;
  harnessSpentTokens: number;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onSend: (text: string) => void;
  onCancel: () => void;
  onUndoRun: () => void;
  onApproveTool: (approvalId?: string) => void;
  onApproveToolForSession: () => void;
  onDenyTool: (approvalId?: string) => void;
  onAnswerQuestion: (answer: string) => void;
  onPickWorkspace: () => void;
  onUseScratch: () => void;
  onListWorkspaceFiles: (path: string) => Promise<HarnessWorkspaceListing>;
  onOpenWorkspaceFile: (path: string) => Promise<void>;
  onRevealWorkspace: () => void;
  onSetShell: (shell: string) => void;
  onSetExecutionProfile: (profile: string) => void;
  onGetIsolationEngine: () => Promise<string | null>;
}

export type HarnessFlowNode = Node<HarnessNodeData, "harness">;

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ArtifactNodeMenu/CodeSandboxNodeMenu/PlanNodeMenu/...). */
// -- card-level menu -------------------------------------------------------

function HarnessNodeMenu({
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

/** Node settings are portaled to the document, not rendered inside the
 * transformed React Flow node. Otherwise fixed coordinates drift with zoom. */
function AgentSettingsPopover({
  position, shell, onSetShell, profile, onSetExecutionProfile, onGetIsolationEngine, onClose, triggerRef,
}: {
  position: MenuPosition;
  shell: string;
  onSetShell: (shell: string) => void;
  profile: string;
  onSetExecutionProfile: (profile: string) => void;
  onGetIsolationEngine: () => Promise<string | null>;
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState(position);
  // undefined = checking, null = neither Docker nor Podman on the backend PATH.
  const [engine, setEngine] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    let mounted = true;
    void onGetIsolationEngine()
      .then((found) => { if (mounted) setEngine(found); })
      .catch(() => { if (mounted) setEngine(null); });
    return () => { mounted = false; };
  }, [onGetIsolationEngine]);
  const windows = /Windows|Win32|Win64/i.test(
    typeof navigator === "undefined" ? "" : navigator.userAgent + " " + navigator.platform,
  );
  // auto is a persisted, cross-platform choice, resolved at task launch.
  const selected = windows
    ? shell === "cmd" || shell === "git-bash" ? shell : "powershell"
    : "bash";

  useLayoutEffect(() => {
    const rect = panelRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = Math.min(
      Math.max(8, position.x), Math.max(8, window.innerWidth - rect.width - 8),
    );
    const y = position.y + rect.height + 8 > window.innerHeight
      ? Math.max(8, position.y - rect.height - 28)
      : position.y;
    setPlacement({ x, y });
  }, [position.x, position.y, engine]);

  useEffect(() => {
    panelRef.current?.querySelector("select")?.focus();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    const trigger = triggerRef.current;
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
      if (trigger?.isConnected) trigger.focus();
    };
  }, [onClose, triggerRef]);

  return createPortal(
    <div
      ref={panelRef}
      className="harness-node-settings-popover nodrag nowheel"
      role="dialog"
      aria-label="Agent settings"
      style={{ position: "fixed", left: placement.x, top: placement.y }}
    >
      <div className="harness-node-settings-heading">
        <strong>Agent settings</strong>
        <button type="button" aria-label="Close agent settings" onClick={onClose}>×</button>
      </div>
      <label className="harness-node-settings-field">
        <span>Shell</span>
        <select
          className="nodrag nowheel"
          aria-label="Shell"
          value={selected}
          disabled={!windows}
          onChange={(event) => onSetShell(event.target.value)}
        >
          {windows ? (
            <>
              <option value="powershell">PowerShell (default)</option>
              <option value="cmd">Command Prompt (cmd)</option>
              <option value="git-bash">Git Bash</option>
            </>
          ) : (
            <option value="bash">Bash (default)</option>
          )}
        </select>
      </label>
      <label className="harness-node-settings-field">
        <span>Execution profile</span>
        <select
          className="nodrag nowheel"
          aria-label="Execution profile"
          value={profile === "isolated" ? "isolated" : "host"}
          onChange={(event) => onSetExecutionProfile(event.target.value)}
        >
          <option value="host">Host (default)</option>
          <option value="isolated" disabled={engine === null}>Isolated (Docker / Podman)</option>
        </select>
      </label>
      {engine === undefined ? (
        <p role="status">Checking Docker / Podman availability…</p>
      ) : engine === null ? (
        <p role="status">No container engine found. Install Docker Desktop (Windows/macOS), Docker Engine (Linux), or Podman to use Isolated execution. Isolated commands never fall back to Host.</p>
      ) : (
        <p role="status">{engine === "docker" ? "Docker" : "Podman"} detected. Isolated runs shell and Python in a container with 1 CPU, 512 MiB RAM and your workspace mounted at /workspace. Commands have no network access except approved python.install PyPI downloads. Pull python:3.12-slim with {engine} before your first run.</p>
      )}
      <p>Saved with this graph. Changes apply from the next task.</p>
    </div>,
    document.body,
  );
}

const UNDOABLE_RUN_STATUSES = new Set(["done", "failed", "stopped", "interrupted"]);

const STATUS_LABELS: Record<string, string> = {
  idle: "Ready",
  running: "Working…",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
  interrupted: "Interrupted",
};

/**
 * What the status band says. A parked run is still `running` on the wire -
 * correctly, since the run owns its slot and Stop still applies - but
 * "Working…" is the wrong thing to tell someone whose answer is the only
 * reason it is not working. Nothing is happening until they act, and the
 * one line they are already watching should be what says so.
 */
function statusLabel(data: HarnessNodeData): string {
  if (data.harnessStatus === "running") {
    if (data.harnessAwaitingApproval) return "Waiting for your approval";
    if (data.harnessAwaitingQuestion) return "Waiting for your answer";
  }
  return STATUS_LABELS[data.harnessStatus] ?? data.harnessStatus;
}

/** Parked reads as a warning, not as progress - the same semantic the
 * approval panel's own border already uses. */
function statusClass(data: HarnessNodeData): string {
  const parked = data.harnessAwaitingApproval || data.harnessAwaitingQuestion;
  return parked && data.harnessStatus === "running"
    ? "harness-node-status-waiting"
    : `harness-node-status-${data.harnessStatus}`;
}

// A run binds its root once, at the start, and every tool in it is
// confined to that root - so rebinding mid-run is not a thing that can
// mean anything. The controls say that rather than being inertly grey.
const WORKSPACE_LOCKED_HINT = "The workspace is fixed while the agent is working - stop it first";

// Every non-running status accepts a follow-up: the transcript is the
// resume point, so done/failed/stopped/interrupted/idle all continue the
// same conversation (intents_harness.send refuses only a busy node).
function acceptsInput(status: string): boolean {
  return status !== "running";
}

/**
 * Unified diffs get line-level color while new-file previews stay literal:
 * their full content may itself begin with "+" or "-", which is NOT a diff.
 * The output is text-only React children (never interpreted as HTML).
 */
function renderApprovalSummary(summary: string, toolName: string) {
  const firstLineEnd = summary.indexOf("\n");
  if (
    (toolName !== "fs.write" && toolName !== "fs.edit") ||
    firstLineEnd === -1 ||
    !summary.slice(firstLineEnd + 1).startsWith("--- a/")
  ) {
    return summary;
  }
  const lines = summary.split("\n");
  // A large replacement can have tens of thousands of lines. Keep the
  // complete disclosure visible without mounting a span for every line.
  if (lines.length > 2_000) return summary;
  return lines.map((line, index) => {
    let className = "";
    if (line.startsWith("--- ") || line.startsWith("+++ ")) {
      className = "harness-approval-diff-header";
    } else if (line.startsWith("+")) {
      className = "harness-approval-diff-added";
    } else if (line.startsWith("-")) {
      className = "harness-approval-diff-removed";
    } else if (line.startsWith("@@")) {
      className = "harness-approval-diff-hunk";
    }
    return (
      <span key={index} className={className}>
        {line}{index < lines.length - 1 ? "\n" : ""}
      </span>
    );
  });
}

function HarnessNodeViewInner({ data, selected }: NodeProps<HarnessFlowNode>) {
  const collapsed = useLodVisibility() || data.isCollapsed;
  const running = data.harnessStatus === "running";
  const [draft, setDraft] = useState("");
  const [answerDraft, setAnswerDraft] = useState("");
  const [submittedApprovalId, setSubmittedApprovalId] = useState("");
  const submittedApprovalRef = useRef("");
  const denyButtonRef = useRef<HTMLButtonElement>(null);
  const activityDetailsRef = useRef<HTMLDetailsElement>(null);
  const activityListRef = useRef<HTMLDivElement>(null);
  const activityErrorCount = data.harnessActivity.filter((row) => row.outcome === "error").length;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const [settingsPosition, setSettingsPosition] = useState<MenuPosition | null>(null);
  const settingsTriggerRef = useRef<HTMLButtonElement>(null);
  const boundApproval = ["python.exec", "python.install", "web.fetch", "web.search"].includes(data.harnessApprovalToolName) || data.harnessApprovalToolName.startsWith("fal.");
  const fileApproval = data.harnessApprovalToolName === "fs.write" || data.harnessApprovalToolName === "fs.edit";
  // Never ask someone to approve an unseen mutation. The backend explains
  // when it cannot safely read the existing file to build the preview.
  const previewUnavailable = fileApproval &&
    data.harnessApprovalSummary.slice(data.harnessApprovalSummary.indexOf("\n") + 1)
      .startsWith("Preview unavailable:");
  const decisionPending = boundApproval && (
    !data.harnessApprovalId || submittedApprovalId === data.harnessApprovalId
  );

  function decideTool(approved: boolean): void {
    const decide = approved ? data.onApproveTool : data.onDenyTool;
    if (!boundApproval) {
      decide();
      return;
    }
    const approvalId = data.harnessApprovalId;
    if (!approvalId || submittedApprovalRef.current === approvalId) return;
    submittedApprovalRef.current = approvalId;
    setSubmittedApprovalId(approvalId);
    decide(approvalId);
  }

  // Deny is the safe default, so focus lands there the moment the panel
  // appears - same effect (and same `collapsed` dependency reasoning) as
  // PlanNodeView's approval panel.
  useEffect(() => {
    if (data.harnessAwaitingApproval && !collapsed) {
      denyButtonRef.current?.focus();
    }
  }, [data.harnessAwaitingApproval, data.harnessApprovalId, collapsed]);

  // "What is it doing?" is the question a running agent raises, and the
  // answer was one click away behind a closed <details>. Opened once, on
  // the first activity of a run - once only, so a deliberate collapse
  // stays collapsed for the rest of that run instead of springing back
  // open on the next tool call.
  const autoOpenedForRun = useRef<string | null>(null);
  useEffect(() => {
    const details = activityDetailsRef.current;
    if (!details || !running || data.harnessActivity.length === 0) return;
    if (autoOpenedForRun.current === data.harnessRunId) return;
    autoOpenedForRun.current = data.harnessRunId;
    details.open = true;
  }, [data.harnessActivity.length, data.harnessRunId, running]);

  // Same follow-the-log behavior as PlanNodeView's activity list: pinned
  // to the newest row only while running and only while open.
  useEffect(() => {
    if (running && activityDetailsRef.current?.open) {
      activityListRef.current?.scrollTo({ top: activityListRef.current.scrollHeight });
    }
  }, [data.harnessActivity, running]);

  function sendDraft(): void {
    const text = draft.trim();
    if (!text || !acceptsInput(data.harnessStatus)) return;
    data.onSend(text);
    setDraft("");
  }

  function sendAnswer(): void {
    const text = answerDraft.trim();
    if (!text) return;
    data.onAnswerQuestion(text);
    setAnswerDraft("");
  }

  return (
    <NodeShell
      kindClassName="harness-node"
      selected={!!selected}
      collapsed={collapsed}
      onMenuChange={(position) => {
        setMenuPosition(position);
        setSettingsPosition(null);
      }}
      header={
        <NodeHeading className="plan-node-title">
          <span className="plan-node-badge">Agent</span>
          <span className="plan-node-goal">{data.harnessGoal || "Untitled task"}</span>
          <button
            ref={settingsTriggerRef}
            type="button"
            className="harness-node-settings-trigger nodrag"
            aria-label="Agent settings"
            title="Agent settings"
            aria-haspopup="dialog"
            aria-expanded={Boolean(settingsPosition)}
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              setMenuPosition(null);
              setSettingsPosition((current) => current ? null : { x: rect.left, y: rect.bottom + 4 });
            }}
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M10 2h4l.6 2.4 2.1.9 2.2-1.2 2.8 2.8-1.2 2.2.9 2.1L24 12l-2.6.8-.9 2.1 1.2 2.2-2.8 2.8-2.2-1.2-2.1.9L14 22h-4l-.6-2.4-2.1-.9-2.2 1.2-2.8-2.8 1.2-2.2-.9-2.1L0 12l2.6-.8.9-2.1-1.2-2.2 2.8-2.8 2.2 1.2 2.1-.9z"
                transform="translate(2 2) scale(.83)" />
            </svg>
          </button>
          <CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={data.onToggleCollapse} />
        </NodeHeading>
      }
      bodyClassName="plan-node-body"
      menu={
        menuPosition && (
          <HarnessNodeMenu
            position={menuPosition}
            isCollapsed={data.isCollapsed}
            onToggleCollapse={data.onToggleCollapse}
            onDelete={data.onDelete}
            onClose={() => setMenuPosition(null)}
          />
        )
      }
      footer={
        settingsPosition && (
          <AgentSettingsPopover
            position={settingsPosition}
            shell={data.harnessShell}
            onSetShell={data.onSetShell}
            profile={data.harnessExecutionProfile}
            onSetExecutionProfile={data.onSetExecutionProfile}
            onGetIsolationEngine={data.onGetIsolationEngine}
            onClose={() => setSettingsPosition(null)}
            triggerRef={settingsTriggerRef}
          />
        )
      }
    >
      <div className={`plan-node-status ${statusClass(data)}`}>{statusLabel(data)}</div>
      {data.harnessStatusDetail && (
        <p className="plan-node-detail" role={data.harnessStatus === "failed" ? "alert" : undefined}>
          {data.harnessStatusDetail}
        </p>
      )}

      {/* Workspace binding. harnessWorkspacePath is the REQUEST (what the
          node asks for); a run only honors it if the folder is trusted, and
          reports the root it actually bound in harnessWorkspaceActive. So a
          NON-EMPTY active dir that does not match the request means a run
          happened and the grant did not apply on this machine. An empty one
          means no run has bound this node since the folder was picked -
          nothing has been refused yet, so nothing is warned about. */}
      <div className="harness-node-workspace">
        {data.harnessWorkspacePath ? (
          <>
            <span
              className="harness-node-workspace-dir"
              title={data.harnessWorkspacePath}
            >
              📁 {data.harnessWorkspacePath}
              {!running &&
                data.harnessWorkspaceActive !== "" &&
                data.harnessWorkspaceActive !== data.harnessWorkspacePath &&
                " (not trusted on this machine — using scratch)"}
            </span>
            <button
              type="button"
              className="plan-node-button nodrag"
              onClick={data.onUseScratch}
              disabled={running}
              title={running ? WORKSPACE_LOCKED_HINT : undefined}
            >
              Use scratch
            </button>
          </>
        ) : (
          <>
            <span className="harness-node-workspace-dir">Scratch workspace</span>
            <button
              type="button"
              className="plan-node-button nodrag"
              onClick={data.onPickWorkspace}
              disabled={running}
              title={running ? WORKSPACE_LOCKED_HINT : undefined}
            >
              Choose folder…
            </button>
          </>
        )}
      </div>
      <HarnessWorkspaceFiles
        key={data.harnessWorkspacePath}
        onList={data.onListWorkspaceFiles}
        onOpen={data.onOpenWorkspaceFile}
        onReveal={data.onRevealWorkspace}
      />

      {data.harnessReply && (
        <div className="harness-node-reply nowheel nodrag">{data.harnessReply}</div>
      )}

      <div className="plan-node-budgets">
        <span>
          Turns {data.harnessSpentTurns}
          {data.harnessMaxTurns > 0 ? ` (max ${data.harnessMaxTurns}/task)` : ""}
        </span>
        <span>Tokens {data.harnessSpentTokens.toLocaleString()}</span>
        {data.harnessMaxContextTokens > 0 && (
          <span
            title={
              data.harnessCompactions > 0
                ? `History summarized ${data.harnessCompactions} time${data.harnessCompactions === 1 ? "" : "s"} to stay within the context budget`
                : "How much of the context budget this agent's history currently fills"
            }
          >
            Context {Math.min(100, Math.round((data.harnessContextTokens / data.harnessMaxContextTokens) * 100))}%
            {data.harnessCompactions > 0 && ` · compacted ${data.harnessCompactions}×`}
          </span>
        )}
      </div>

      {/* §2.3's plan.update surface. Always expanded, unlike the activity
          log below: the checklist is the thing a person watching a long run
          actually wants visible, and it is short by construction (capped at
          20 rows backend-side). */}
      {data.harnessPlan.length > 0 && (
        <div className="harness-node-plan" role="list" aria-label="Agent checklist">
          {data.harnessPlan.map((step, index) => (
            <div
              key={`${index}-${step.text}`}
              role="listitem"
              className={`harness-node-plan-step harness-node-plan-step-${step.status}`}
            >
              <span className="harness-node-plan-marker" aria-hidden="true">
                {step.status === "done" ? "✓" : step.status === "active" ? "▸" : "○"}
              </span>
              <span className="harness-node-plan-text">{step.text}</span>
            </div>
          ))}
        </div>
      )}

      {data.harnessActivity.length > 0 && (
        <details className="chat-node-tool-invocations plan-node-activity" ref={activityDetailsRef}>
          <summary>
            {data.harnessActivity.length === 1
              ? "1 activity entry"
              : `${data.harnessActivity.length} activity entries`}
            {activityErrorCount > 0 &&
              ` · ${activityErrorCount} error${activityErrorCount === 1 ? "" : "s"}`}
          </summary>
          <div className="plan-node-activity-list nowheel nodrag" ref={activityListRef}>
            {data.harnessActivity.map((row, index) => (
              <div
                key={index}
                className={`chat-node-tool-invocation${row.outcome === "error" ? " error" : ""}`}
              >
                <div className="chat-node-tool-invocation-name">
                  {row.tool}
                  <span className="plan-node-activity-elapsed">
                    {row.outcome === "running" ? "Running · " : row.outcome === "stopped" ? "Stopped · " : ""}
                    {row.elapsedMs}ms
                  </span>
                </div>
                <pre className="chat-node-tool-invocation-arguments">{row.summary}</pre>
              </div>
            ))}
          </div>
        </details>
      )}

      {data.harnessAwaitingApproval && (
        <div className="plan-node-approval" role="group" aria-label="Agent tool approval">
          <div className="plan-node-approval-title">
            The agent wants to run: <code>{data.harnessApprovalToolName}</code>
          </div>
          <pre
            className="plan-node-approval-summary nowheel nodrag"
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- the scrollable disclosure must be keyboard reachable, matching CodeExecutionApprovalPanel's code region
            tabIndex={0}
            role="region"
            aria-label="Proposed tool call"
          >{renderApprovalSummary(data.harnessApprovalSummary, data.harnessApprovalToolName)}</pre>
          {previewUnavailable && (
            <p role="alert" className="harness-approval-preview-warning">
              This file change could not be previewed. Deny the request and ask the agent to retry.
            </p>
          )}
          <div className="plan-node-approval-actions">
            <button
              type="button"
              className="plan-node-button nodrag"
              onClick={() => decideTool(true)}
              disabled={decisionPending || previewUnavailable}
            >
              Approve once
            </button>
            {data.harnessApprovalSessionOffered && !boundApproval && !previewUnavailable && (
              <button
                type="button"
                className="plan-node-button nodrag"
                onClick={data.onApproveToolForSession}
                title="Stop asking for this tool for the rest of this agent's session"
              >
                Always allow this tool
              </button>
            )}
            <button
              type="button"
              className="plan-node-button plan-node-button-deny nodrag"
              onClick={() => decideTool(false)}
              disabled={decisionPending}
              ref={denyButtonRef}
            >
              Deny
            </button>
          </div>
        </div>
      )}

      {/* §2.3's user.ask: the run is parked on a human answer. Rendered as
          its own surface rather than reusing the composer below, which is
          for starting the NEXT task - answering is not sending. */}
      {data.harnessAwaitingQuestion && (
        <div className="plan-node-approval" role="group" aria-label="Agent question">
          <div className="plan-node-approval-title">The agent is asking:</div>
          <pre className="plan-node-approval-summary">{data.harnessQuestion}</pre>
          <div className="harness-node-composer">
            <textarea
              className="harness-node-input nodrag nowheel"
              placeholder="Type your answer…"
              value={answerDraft}
              rows={2}
              aria-label="Answer to the agent's question"
              onChange={(event) => setAnswerDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  sendAnswer();
                }
              }}
            />
            <div className="plan-node-approval-actions">
              <button
                type="button"
                className="plan-node-button plan-node-button-start nodrag"
                disabled={!answerDraft.trim()}
                onClick={sendAnswer}
              >
                Answer
              </button>
              <button
                type="button"
                className="plan-node-button plan-node-button-deny nodrag"
                onClick={() => {
                  setAnswerDraft("");
                  data.onAnswerQuestion("");
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        </div>
      )}

      {acceptsInput(data.harnessStatus) && (
        <div className="harness-node-composer">
          <textarea
            className="harness-node-input nodrag nowheel"
            placeholder="Send a follow-up…"
            value={draft}
            rows={2}
            aria-label="Follow-up message"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                sendDraft();
              }
            }}
          />
          <button
            type="button"
            className="plan-node-button plan-node-button-start nodrag"
            disabled={!draft.trim()}
            onClick={sendDraft}
          >
            Send
          </button>
        </div>
      )}

      {running && (
        <div className="plan-node-actions">
          <button
            type="button"
            className="plan-node-button plan-node-button-stop nodrag"
            onClick={data.onCancel}
          >
            Stop
          </button>
        </div>
      )}
      {!running && data.harnessRunId && UNDOABLE_RUN_STATUSES.has(data.harnessStatus) && (
        <div className="plan-node-actions">
          <button
            type="button"
            className="plan-node-button plan-node-button-danger nodrag"
            onClick={data.onUndoRun}
          >
            Undo run
          </button>
        </div>
      )}
    </NodeShell>
  );
}

export const HarnessNodeView = memo(HarnessNodeViewInner);
