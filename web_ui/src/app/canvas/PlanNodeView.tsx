import { memo, useEffect, useRef } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { useLodVisibility } from "./useLodVisibility";

/**
 * ADR-008 stage 8.3: the Builder's plan node - the checklist the user
 * watches a build construct itself against (the ADR's own "planning is
 * explicit and visible" decision). Everything rendered here is real
 * backend state off the wire row (PlanState -> the builder* fields);
 * every button fires a real intent through sceneStore.
 *
 * The tool-approval panel copies CodeExecutionApprovalPanel's
 * architecture, deliberately NOT the shared Dialog/overlay primitive:
 * zero passive dismissal (no Escape/scrim/X - a dismissal would strand
 * the run's parked approval future forever; the only exits are Approve/
 * Deny/Stop), rendered per-node rather than through the one-surface-at-
 * a-time overlay registry, and zero-argument callbacks closed over the
 * CURRENT snapshot's requestId so the panel structurally cannot approve
 * a different request than the one it shows.
 */

export interface PlanStepData {
  id: string;
  title: string;
  status: string;
  detail: string;
}

export interface PlanNodeData extends Record<string, unknown> {
  planGoal: string;
  planSteps: PlanStepData[];
  builderStatus: string;
  builderMode: string;
  builderRunId: string;
  builderMaxSteps: number;
  builderMaxTokens: number;
  builderMaxWallSeconds: number;
  builderSpentSteps: number;
  builderSpentTokens: number;
  builderSpentWallSeconds: number;
  builderAwaitingToolApproval: boolean;
  builderApprovalToolName: string;
  builderApprovalSummary: string;
  builderStatusDetail: string;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onStartExecution: () => void;
  onCancel: () => void;
  onApproveTool: () => void;
  onDenyTool: () => void;
  onUndoBuild: () => void;
  onSaveRecipe: () => void;
}

export type PlanFlowNode = Node<PlanNodeData, "plan">;

const STEP_MARKERS: Record<string, string> = {
  pending: "○",
  running: "◐",
  done: "●",
  failed: "✕",
  skipped: "–",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  planning: "Planning…",
  awaiting_start: "Plan ready — review, then start",
  running: "Building…",
  awaiting_approval: "Waiting for approval",
  paused: "Paused",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
  interrupted: "Interrupted",
};

// review-fix: "failed" joined the backend's own _RESUMABLE_STATUSES - a
// transient provider fault (rate limit, a 5xx, a network blip) used to be
// a permanent dead end even though the plan node's goal/checklist/spent
// budgets sit right there on the canvas, resumable like any other pause.
const RESUMABLE = new Set(["awaiting_start", "paused", "interrupted", "failed"]);
// ADR-008 stage 8.4: "undo this build" is offered once the run is OVER -
// the domain's own live-run guard would refuse it mid-run anyway
// (Stop-then-undo is the supported sequence), so the button only appears
// on states no run backs. undo_run's reach stops at the first command a
// DIFFERENT actor made after the build - later user edits survive.
const UNDOABLE = new Set(["done", "failed", "stopped", "interrupted", "paused"]);

function PlanNodeViewInner({ data, selected }: NodeProps<PlanFlowNode>) {
  const collapsed = useLodVisibility() || data.isCollapsed;
  const running = data.builderStatus === "running" || data.builderStatus === "planning";
  const resumable = RESUMABLE.has(data.builderStatus);
  const startLabel = data.builderStatus === "awaiting_start" ? "Start build" : "Resume";
  const denyButtonRef = useRef<HTMLButtonElement>(null);

  // The tool-approval panel mounts fresh each time the Builder pauses for
  // approval (see the block-level comment above); Deny is the safe default,
  // so move focus there the moment the panel appears - same UX an autoFocus
  // prop would give, but as an imperative effect so it doesn't trip
  // jsx-a11y/no-autofocus. `collapsed` is ALSO a dependency, not just
  // builderAwaitingToolApproval: the panel is gated by `!collapsed &&
  // data.builderAwaitingToolApproval` below (both must hold for the button
  // to actually be in the DOM), and LOD collapse-on-zoom-out or a manual
  // collapse toggle can flip `collapsed` independently while approval stays
  // pending the whole time - without this, expanding the node back out
  // while already awaiting approval would mount the button for the first
  // time with nothing left to re-fire the effect, leaving it unfocused.
  useEffect(() => {
    if (data.builderAwaitingToolApproval && !collapsed) {
      denyButtonRef.current?.focus();
    }
  }, [data.builderAwaitingToolApproval, collapsed]);

  return (
    <div className={`scene-node plan-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}>
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title plan-node-title">
        <span className="plan-node-badge">Build</span>
        <span className="plan-node-goal">{data.planGoal || "Untitled build"}</span>
      </div>
      {!collapsed && (
        <div className="scene-node-body plan-node-body">
          <div className={`plan-node-status plan-node-status-${data.builderStatus}`}>
            {STATUS_LABELS[data.builderStatus] ?? data.builderStatus}
            {data.builderMode === "autopilot" && (
              <span className="plan-node-mode-chip">autopilot</span>
            )}
          </div>
          {data.builderStatusDetail && (
            <p className="plan-node-detail" role={data.builderStatus === "failed" ? "alert" : undefined}>
              {data.builderStatusDetail}
            </p>
          )}

          {data.planSteps.length > 0 && (
            <ul className="plan-node-steps">
              {data.planSteps.map((step) => (
                <li key={step.id} className={`plan-node-step plan-node-step-${step.status}`}>
                  <span className="plan-node-step-marker" aria-hidden="true">
                    {STEP_MARKERS[step.status] ?? "○"}
                  </span>
                  <span className="plan-node-step-title">{step.title}</span>
                  {step.detail && <span className="plan-node-step-detail">{step.detail}</span>}
                </li>
              ))}
            </ul>
          )}

          <div className="plan-node-budgets">
            <span>
              Steps {data.builderSpentSteps}/{data.builderMaxSteps}
            </span>
            <span>
              Tokens {data.builderSpentTokens.toLocaleString()}/{data.builderMaxTokens.toLocaleString()}
            </span>
            <span>
              Time {data.builderSpentWallSeconds}s/{data.builderMaxWallSeconds}s
            </span>
          </div>

          {data.builderAwaitingToolApproval && (
            <div className="plan-node-approval" role="group" aria-label="Builder tool approval">
              <div className="plan-node-approval-title">
                The Builder wants to run: <code>{data.builderApprovalToolName}</code>
              </div>
              <pre className="plan-node-approval-summary">{data.builderApprovalSummary}</pre>
              <div className="plan-node-approval-actions">
                <button type="button" className="plan-node-button nodrag" onClick={data.onApproveTool}>
                  Approve
                </button>
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

          <div className="plan-node-actions">
            {resumable && (
              <button type="button" className="plan-node-button plan-node-button-start nodrag" onClick={data.onStartExecution}>
                {startLabel}
              </button>
            )}
            {running && (
              <button type="button" className="plan-node-button plan-node-button-stop nodrag" onClick={data.onCancel}>
                Stop
              </button>
            )}
            {UNDOABLE.has(data.builderStatus) && data.builderRunId && (
              <button type="button" className="plan-node-button nodrag" onClick={data.onUndoBuild}>
                Undo build
              </button>
            )}
            {data.builderStatus === "done" && data.planSteps.length > 0 && (
              <button type="button" className="plan-node-button nodrag" onClick={data.onSaveRecipe}>
                Save as recipe
              </button>
            )}
          </div>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
    </div>
  );
}

export const PlanNodeView = memo(PlanNodeViewInner);
