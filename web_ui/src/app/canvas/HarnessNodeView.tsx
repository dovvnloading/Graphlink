import { memo, useEffect, useRef, useState } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { CollapseToggleButton } from "./CollapseToggleButton";
import type { MenuPosition } from "./menuPosition";
import { NodeMenu } from "./NodeMenu";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

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
  harnessApprovalSessionOffered: boolean;
  harnessPlan: HarnessPlanStepData[];
  harnessAwaitingQuestion: boolean;
  harnessQuestion: string;
  harnessContextTokens: number;
  harnessMaxContextTokens: number;
  harnessCompactions: number;
  harnessWorkspacePath: string;
  harnessWorkspaceActive: string;
  harnessMaxTurns: number;
  harnessSpentTurns: number;
  harnessSpentTokens: number;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onSend: (text: string) => void;
  onCancel: () => void;
  onApproveTool: () => void;
  onApproveToolForSession: () => void;
  onDenyTool: () => void;
  onAnswerQuestion: (answer: string) => void;
  onPickWorkspace: () => void;
  onUseScratch: () => void;
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

function HarnessNodeViewInner({ data, selected }: NodeProps<HarnessFlowNode>) {
  const collapsed = useLodVisibility() || data.isCollapsed;
  const running = data.harnessStatus === "running";
  const [draft, setDraft] = useState("");
  const [answerDraft, setAnswerDraft] = useState("");
  const denyButtonRef = useRef<HTMLButtonElement>(null);
  const activityDetailsRef = useRef<HTMLDetailsElement>(null);
  const activityListRef = useRef<HTMLDivElement>(null);
  const activityErrorCount = data.harnessActivity.filter((row) => row.outcome !== "ok").length;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  // Deny is the safe default, so focus lands there the moment the panel
  // appears - same effect (and same `collapsed` dependency reasoning) as
  // PlanNodeView's approval panel.
  useEffect(() => {
    if (data.harnessAwaitingApproval && !collapsed) {
      denyButtonRef.current?.focus();
    }
  }, [data.harnessAwaitingApproval, collapsed]);

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
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
      header={
        <div className="scene-node-title plan-node-title">
          <span className="plan-node-badge">Agent</span>
          <span className="plan-node-goal">{data.harnessGoal || "Untitled task"}</span>
          <CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={data.onToggleCollapse} />
        </div>
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
                className={`chat-node-tool-invocation${row.outcome !== "ok" ? " error" : ""}`}
              >
                <div className="chat-node-tool-invocation-name">
                  {row.tool}
                  <span className="plan-node-activity-elapsed">{row.elapsedMs}ms</span>
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
          <pre className="plan-node-approval-summary">{data.harnessApprovalSummary}</pre>
          <div className="plan-node-approval-actions">
            <button type="button" className="plan-node-button nodrag" onClick={data.onApproveTool}>
              Approve once
            </button>
            {/* PLAN §2.4 graded consent. Absent - not merely disabled - for a
                dangerous command: the backend decides that (shell_policy) and
                says so via harnessApprovalSessionOffered, so the broader
                grant is never a button someone can reach for `rm -rf`. */}
            {data.harnessApprovalSessionOffered && (
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
              onClick={data.onDenyTool}
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
    </NodeShell>
  );
}

export const HarnessNodeView = memo(HarnessNodeViewInner);
