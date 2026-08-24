import { memo, useEffect, useRef, useState } from "react";
import type { Node, NodeProps } from "@xyflow/react";
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

export interface HarnessNodeData extends Record<string, unknown> {
  harnessGoal: string;
  harnessReply: string;
  harnessStatus: string;
  harnessStatusDetail: string;
  harnessRunId: string;
  harnessActivity: HarnessActivityRowData[];
  harnessMaxTurns: number;
  harnessSpentTurns: number;
  harnessSpentTokens: number;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onSend: (text: string) => void;
  onCancel: () => void;
}

export type HarnessFlowNode = Node<HarnessNodeData, "harness">;

const STATUS_LABELS: Record<string, string> = {
  idle: "Ready",
  running: "Working…",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
  interrupted: "Interrupted",
};

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
  const activityDetailsRef = useRef<HTMLDetailsElement>(null);
  const activityListRef = useRef<HTMLDivElement>(null);
  const activityErrorCount = data.harnessActivity.filter((row) => row.outcome !== "ok").length;

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

  return (
    <NodeShell
      kindClassName="harness-node"
      selected={!!selected}
      collapsed={collapsed}
      header={
        <div className="scene-node-title plan-node-title">
          <span className="plan-node-badge">Agent</span>
          <span className="plan-node-goal">{data.harnessGoal || "Untitled task"}</span>
        </div>
      }
      bodyClassName="plan-node-body"
    >
      <div className={`plan-node-status harness-node-status-${data.harnessStatus}`}>
        {STATUS_LABELS[data.harnessStatus] ?? data.harnessStatus}
      </div>
      {data.harnessStatusDetail && (
        <p className="plan-node-detail" role={data.harnessStatus === "failed" ? "alert" : undefined}>
          {data.harnessStatusDetail}
        </p>
      )}

      {data.harnessReply && (
        <div className="harness-node-reply nowheel nodrag">{data.harnessReply}</div>
      )}

      <div className="plan-node-budgets">
        <span>
          Turns {data.harnessSpentTurns}
          {data.harnessMaxTurns > 0 ? ` (max ${data.harnessMaxTurns}/task)` : ""}
        </span>
        <span>Tokens {data.harnessSpentTokens.toLocaleString()}</span>
      </div>

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
