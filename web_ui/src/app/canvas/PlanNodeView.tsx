import { memo, useEffect, useRef, useState } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { CollapseToggleButton } from "./CollapseToggleButton";
import type { MenuPosition } from "./menuPosition";
import { NodeMenu } from "./NodeMenu";
import { NodeHeading } from "./NodeHeading";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";
import { Icon } from "../ui/icons/Icon";
import { IconButton } from "../ui/IconButton";
import { Button } from "../ui/Button";
import { MenuItem } from "../ui/Menu";
import { Badge, StatusPill } from "../ui/Badge";
import { Spinner } from "../ui/Spinner";
import { Disclosure } from "../ui/Disclosure";
import { InlineAlert } from "../ui/InlineAlert";
import { Tooltip } from "../ui/Tooltip";
import { TextField } from "../ui/TextField";
import { formatCount, formatDuration, formatTokens } from "../ui/format";

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

export interface BuilderActivityRowData {
  tool: string;
  summary: string;
  outcome: string;
  stepId: string;
  elapsedMs: number;
}

export interface PlanNodeData extends Record<string, unknown> {
  planGoal: string;
  planSteps: PlanStepData[];
  builderActivity: BuilderActivityRowData[];
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
  onSetPlanSteps: (steps: PlanStepData[]) => void;
}

export type PlanFlowNode = Node<PlanNodeData, "plan">;

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ArtifactNodeMenu/CodeSandboxNodeMenu/...). */
// -- card-level menu -------------------------------------------------------

function PlanNodeMenu({
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
      <MenuItem icon="minimize" label={isCollapsed ? "Expand" : "Collapse"} onSelect={onToggleCollapse} />
      <MenuItem label="Delete node" destructive onSelect={onDelete} />
    </NodeMenu>
  );}

function StepMarker({ status }: { status: string }) {
  if (status === "running") return <Spinner size={16} decorative />;
  const icon = status === "done" ? "status-success"
    : status === "failed" ? "status-error"
    : status === "skipped" ? "status-skipped" : "status-pending";
  return <Icon name={icon} size="md" className="plan-node-step-icon" />;
}

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

// A stopped build is a DEAD END, and the card never said so. "stopped" is
// deliberately absent from RESUMABLE above (mirroring the backend's own
// _RESUMABLE_STATUSES exactly), so Stop - a button that sits where Start
// and Resume sit and reads like a pause - permanently forecloses the run:
// the only ways on from there are undoing it or launching a new build. That
// is a product decision rather than a defect, but it was one the UI made
// silently, at the moment it was already too late to act on.
const TERMINAL_NOTES: Record<string, string> = {
  stopped: "A stopped build can't be resumed - undo it, or start a new build.",
};

function PlanNodeViewInner({ data, selected }: NodeProps<PlanFlowNode>) {
  const collapsed = useLodVisibility() || data.isCollapsed;
  const running = data.builderStatus === "running" || data.builderStatus === "planning";
  const resumable = RESUMABLE.has(data.builderStatus);
  const startLabel = data.builderStatus === "awaiting_start" ? "Start build" : "Resume";
  // An automatic disclosure must not add a second node-view commit when activity updates.
  const [activityChoice, setActivityChoice] = useState<{ runId: string; open: boolean } | null>(null);
  const activityOpen = activityChoice?.runId === data.builderRunId
    ? activityChoice.open : false;
  const setActivityOpen = (open: boolean) => setActivityChoice({ runId: data.builderRunId, open });
  const activityListRef = useRef<HTMLDivElement>(null);
  const activityErrorCount = data.builderActivity.filter((row) => row.outcome !== "ok").length;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  // Keeps the log pinned to its newest row while the build is actively
  // producing more of them - only while the disclosure is actually open
  // (a native <details>'s own `open` attribute IS the expand/collapse
  // state here, so no separate React state exists to gate this on) and
  // only while running, so a landed build's log stops moving under the
  // user once there is nothing left to follow.
  useEffect(() => {
    if (running && activityOpen) {
      activityListRef.current?.scrollTo({ top: activityListRef.current.scrollHeight });
    }
  }, [data.builderActivity, running, activityOpen]);

  // ADR-021 stage 21.3: plan editing is local-draft-then-commit, not
  // per-keystroke intent firing - a checklist edit is a single undoable
  // document command server-side (setPlanSteps records one), so streaming
  // every character would spray the undo stack and the wire both.
  //
  // The draft carries a signature of the step list it was seeded from, and
  // is treated as closed the moment that signature changes. That is what
  // keeps an open editor honest without an effect syncing state: if the
  // build runs a step, replans, or lands while the editor is open, the
  // draft silently stops applying rather than letting the user save an
  // edit against a plan that has since moved (which set_plan_steps would
  // reject anyway, since it refuses to rewrite a step that has run).
  const [draft, setDraft] = useState<{ seededFrom: string; steps: PlanStepData[] } | null>(null);
  // The separators are written as escapes, not as literal control
  // characters. They used to be real U+0000 and U+0001 bytes sitting in
  // this file, which made git classify the whole module as BINARY - no
  // diff, no review, no blame on any line of it. Same runtime value,
  // same "cannot collide with a step id or title" property, in a file
  // that can now be read.
  const planSignature = data.planSteps
    .map((step) => `${step.id}\u0000${step.status}\u0000${step.title}`)
    .join("\u0001");
  // Editable exactly when the build is startable/resumable: those are the
  // moments a plan change can still affect what runs. RESUMABLE is reused
  // rather than a second, subtly-different set - "you can edit the plan
  // whenever you can start it" is one rule, not two.
  const canEditPlan = resumable && data.planSteps.length > 0;
  const editing = draft !== null && canEditPlan && draft.seededFrom === planSignature;
  const draftSteps = editing ? draft.steps : null;

  const draftIsValid =
    draftSteps !== null && draftSteps.every((step) => step.title.trim().length > 0);

  function startEditing(): void {
    setDraft({
      seededFrom: planSignature,
      steps: data.planSteps.map((step) => ({ ...step })),
    });
  }

  function cancelEditing(): void {
    setDraft(null);
  }

  function updateDraft(update: (steps: PlanStepData[]) => PlanStepData[]): void {
    setDraft((current) =>
      current === null ? current : { ...current, steps: update(current.steps) },
    );
  }

  function retitleStep(index: number, title: string): void {
    updateDraft((steps) => steps.map((step, i) => (i === index ? { ...step, title } : step)));
  }

  function removeStep(index: number): void {
    updateDraft((steps) => steps.filter((_, i) => i !== index));
  }

  function addStep(): void {
    // A blank id lets the backend mint one: set_plan_steps derives "s{n}"
    // for any step arriving without one and rejects duplicates, so the
    // client never invents an id that could collide with a replan's own
    // minting.
    updateDraft((steps) => [...steps, { id: "", title: "", status: "pending", detail: "" }]);
  }

  // Reordering only ever swaps two PENDING steps: a step that has run is
  // history and must keep both its content and its place, so a pending step
  // cannot hop over one.
  function canMoveUp(index: number): boolean {
    return draftSteps !== null && index > 0 && draftSteps[index - 1].status === "pending";
  }

  function canMoveDown(index: number): boolean {
    return (
      draftSteps !== null &&
      index < draftSteps.length - 1 &&
      draftSteps[index + 1].status === "pending"
    );
  }

  function moveStep(index: number, delta: number): void {
    updateDraft((steps) => {
      const target = index + delta;
      if (target < 0 || target >= steps.length) return steps;
      const next = [...steps];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function saveSteps(): void {
    if (draftSteps === null || !draftIsValid) return;
    data.onSetPlanSteps(draftSteps.map((step) => ({ ...step, title: step.title.trim() })));
    setDraft(null);
  }

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
      document.getElementById("plan-node-deny-approval")?.focus();
    }
  }, [data.builderAwaitingToolApproval, collapsed]);

  return (
    <NodeShell
      kindClassName="plan-node"
      selected={!!selected}
      collapsed={collapsed}
      onMenuChange={setMenuPosition}
      header={
        <NodeHeading className="plan-node-title">
          <Badge className="plan-node-badge">Build</Badge>
          <span className="plan-node-goal">{data.planGoal || "Untitled build"}</span>
          <CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={data.onToggleCollapse} />
        </NodeHeading>
      }
      bodyClassName="plan-node-body"
      menu={
        menuPosition && (
          <PlanNodeMenu
            position={menuPosition}
            isCollapsed={data.isCollapsed}
            onToggleCollapse={data.onToggleCollapse}
            onDelete={data.onDelete}
            onClose={() => setMenuPosition(null)}
          />
        )
      }
    >
      <div className={`plan-node-status plan-node-status-${data.builderStatus}`}>
        {(["running", "planning"].includes(data.builderStatus)) ? <StatusPill status="Running" />
          : data.builderStatus === "done" ? <StatusPill status="Done" />
          : data.builderStatus === "failed" ? <StatusPill status="Failed" />
          : data.builderStatus === "awaiting_approval" ? <StatusPill status="Waiting for approval" />
          : data.builderStatus === "stopped" ? <StatusPill status="Cancelled" />
          : <Badge>{STATUS_LABELS[data.builderStatus] ?? data.builderStatus}</Badge>}
        {data.builderMode.toLowerCase() === "autopilot" && <Badge className="plan-node-mode-chip">Autopilot</Badge>}
      </div>
      {data.builderStatusDetail && (
        <p className="plan-node-detail" role={data.builderStatus === "failed" ? "alert" : undefined}>
          {data.builderStatusDetail}
        </p>
      )}
      {TERMINAL_NOTES[data.builderStatus] && (
        <p className="plan-node-detail">{TERMINAL_NOTES[data.builderStatus]}</p>
      )}

      {data.planSteps.length > 0 && draftSteps === null && (
        <ul className="plan-node-steps">
          {data.planSteps.map((step) => (
            <li key={step.id} className={`plan-node-step plan-node-step-${step.status}`}>
              <span className="plan-node-step-marker" aria-hidden="true">
                <StepMarker status={step.status} />
              </span>
              <span className="plan-node-step-title">{step.title}</span>
              {step.detail && <span className="plan-node-step-detail">{step.detail}</span>}
            </li>
          ))}
        </ul>
      )}

      {/* ADR-021 stage 21.3: the checklist edit ADR-008 decided on ("a
          checklist the user sees and can edit before execution proceeds")
          and shipped without - scene/setPlanSteps and the store method
          existed since 8.3 with no caller. Only PENDING steps are editable;
          a step that has already run is immutable history, rendered
          read-only here and rejected server-side by set_plan_steps if a
          client ever tried otherwise, so this UI expresses the domain rule
          rather than being trusted to enforce it. */}
      {draftSteps !== null && (
        <div className="plan-node-step-editor" role="group" aria-label="Edit plan steps">
          <ul className="plan-node-steps">
            {draftSteps.map((step, index) => {
              const frozen = step.status !== "pending";
              return (
                <li key={step.id} className={`plan-node-step plan-node-step-${step.status}`}>
                  <span className="plan-node-step-marker" aria-hidden="true">
                    <StepMarker status={step.status} />
                  </span>
                  {frozen ? (
                    <span className="plan-node-step-title">{step.title}</span>
                  ) : (
                    <>
                      <TextField size="sm" className="plan-node-step-input nodrag"
                        value={step.title} label={`Step ${index + 1} title`}
                        onValueChange={(value) => retitleStep(index, value)} />
                      <IconButton size="sm" className="plan-node-step-action nodrag" icon="arrow-up"
                        label={`Move step ${index + 1} up`} disabled={!canMoveUp(index)}
                        onClick={() => moveStep(index, -1)} />
                      <IconButton size="sm" className="plan-node-step-action nodrag" icon="arrow-down"
                        label={`Move step ${index + 1} down`} disabled={!canMoveDown(index)}
                        onClick={() => moveStep(index, 1)} />
                      <IconButton size="sm" className="plan-node-step-action nodrag" icon="close"
                        label={`Remove step ${index + 1}`} onClick={() => removeStep(index)} />
                    </>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="plan-node-step-editor-actions">
            <Button size="sm" className="nodrag" iconStart="plus" onClick={addStep}>Add step</Button>
            <Button size="sm" variant="primary" className="nodrag"
              disabled={!draftIsValid} onClick={saveSteps}>Save plan</Button>
            <Button size="sm" variant="ghost" className="nodrag" onClick={cancelEditing}>Cancel</Button>
          </div>
          {!draftIsValid && (
            <p className="plan-node-detail" role="alert">
              Every step needs a title.
            </p>
          )}
        </div>
      )}

      <div className="plan-node-budgets">
        {formatCount(data.builderSpentSteps)} of {formatCount(data.builderMaxSteps)} steps ·{" "}
        {formatTokens(data.builderSpentTokens)} / {formatTokens(data.builderMaxTokens)} tokens ·{" "}
        {formatDuration(data.builderSpentWallSeconds)} / {formatDuration(data.builderMaxWallSeconds)}
      </div>
      {data.builderStatus === "done" && data.planSteps.length > data.builderSpentSteps && (
        <Tooltip label={data.builderStatusDetail || "The Builder finished before all planned steps were executed."}>
          <span className="plan-node-skipped">
            <Icon name="status-skipped" size="sm" />
            {formatCount(data.planSteps.length - data.builderSpentSteps)} steps skipped
          </span>
        </Tooltip>
      )}

      {data.builderActivity.length > 0 && (
        <Disclosure className="chat-node-tool-invocations plan-node-activity"
          open={activityOpen} onOpenChange={setActivityOpen}
          {...{ title: `${data.builderActivity.length} activity ${data.builderActivity.length === 1 ? "entry" : "entries"}${activityErrorCount ? ` · ${activityErrorCount} ${activityErrorCount === 1 ? "error" : "errors"}` : ""}` }}>
          <div className="plan-node-activity-list nowheel nodrag" ref={activityListRef}>
            {data.builderActivity.map((row, index) => (
              <div key={index}
                className={`chat-node-tool-invocation${row.outcome !== "ok" ? " error" : ""}`}>
                <div className="chat-node-tool-invocation-name">{row.tool}
                  <span className="plan-node-activity-elapsed">{formatDuration(row.elapsedMs / 1000)}</span>
                </div>
                <pre className="chat-node-tool-invocation-arguments">{row.summary}</pre>
              </div>
            ))}
          </div>
        </Disclosure>
      )}

      {data.builderAwaitingToolApproval && (
        <div className="plan-node-approval" role="group" aria-label="Builder tool approval">
          <InlineAlert variant="warning" {...{ title: `The Builder wants to run: ${data.builderApprovalToolName}` }}
            body={<pre className="plan-node-approval-summary">{data.builderApprovalSummary}</pre>} />
          <div className="plan-node-approval-actions">
            <Button size="sm" variant="secondary" className="nodrag" onClick={data.onApproveTool}>Approve</Button>
            <Button size="sm" id="plan-node-deny-approval" className="nodrag" onClick={data.onDenyTool}>Deny</Button>
          </div>
        </div>
      )}

      <div className="plan-node-actions">
        {resumable && <Button size="sm" variant="primary" className="nodrag"
          onClick={data.onStartExecution}>{startLabel}</Button>}
        {running && (
          <Tooltip label="Stop this build. A stopped build cannot be resumed.">
            <Button size="sm" variant="secondary" className="nodrag" onClick={data.onCancel}>Stop</Button>
          </Tooltip>
        )}
        {UNDOABLE.has(data.builderStatus) && data.builderRunId && (
          <Button size="sm" variant="secondary" className="nodrag" onClick={data.onUndoBuild}
            iconStart="undo">Undo build</Button>
        )}
        {canEditPlan && !editing && <Button size="sm" className="nodrag" onClick={startEditing}>Edit plan</Button>}
        {data.builderStatus === "done" && data.planSteps.length > 0 && (
          <Button size="sm" variant="ghost" iconStart="bookmark" className="nodrag"
            onClick={data.onSaveRecipe}>Save as recipe</Button>
        )}
      </div>
    </NodeShell>
  );
}

export const PlanNodeView = memo(PlanNodeViewInner);
