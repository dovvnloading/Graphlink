import { memo, useEffect, useRef, useState } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { CollapseToggleButton } from "./CollapseToggleButton";
import type { MenuPosition } from "./menuPosition";
import { NodeMenu } from "./NodeMenu";
import { NodeShell } from "./NodeShell";
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

/**
 * The per-step status marker.
 *
 * Was a table of text glyphs - "○ ◐ ● ✕ –" - rendered at whatever weight
 * and baseline the UI font happened to give them, next to an icon set drawn
 * at a consistent 1.7 stroke everywhere else in the app. "◐" in particular
 * read as a rendering artifact rather than as "this step is running now".
 *
 * The distinctions are carried by SHAPE, not by colour, and that is not a
 * stylistic preference: this app's palette is a deliberate monochrome, and
 * its four semantic status tokens currently resolve to #848484, #919191,
 * #838383 and #828282 - error, warning, success and info are the same grey
 * to within a couple of levels. A done step and a failed step were
 * previously distinguished by a one-level luminance difference and nothing
 * else. A check, a cross, a dash and a ring are unmistakable at any size,
 * on any palette, and to anyone who cannot separate those greys at all.
 */
function StepMarker({ status }: { status: string }) {
  switch (status) {
    case "done":
      return (
        <svg aria-hidden="true" viewBox="0 0 16 16" className="plan-node-step-icon">
          <circle cx="8" cy="8" r="6.25" />
          <path d="m5.2 8.2 2 2 3.6-4.2" />
        </svg>
      );
    case "running":
      return (
        <svg aria-hidden="true" viewBox="0 0 16 16" className="plan-node-step-icon">
          <circle cx="8" cy="8" r="6.25" />
          <circle cx="8" cy="8" r="2.6" fill="currentColor" stroke="none" />
        </svg>
      );
    case "failed":
      return (
        <svg aria-hidden="true" viewBox="0 0 16 16" className="plan-node-step-icon">
          <circle cx="8" cy="8" r="6.25" />
          <path d="m5.8 5.8 4.4 4.4M10.2 5.8l-4.4 4.4" />
        </svg>
      );
    case "skipped":
      return (
        <svg aria-hidden="true" viewBox="0 0 16 16" className="plan-node-step-icon">
          <circle cx="8" cy="8" r="6.25" />
          <path d="M5.4 8h5.2" />
        </svg>
      );
    default:
      return (
        <svg aria-hidden="true" viewBox="0 0 16 16" className="plan-node-step-icon">
          <circle cx="8" cy="8" r="6.25" />
        </svg>
      );
  }
}

/** The three per-row controls in the step editor. Same reason as
 * StepMarker: "↑ ↓ ✕" were font glyphs sitting inside buttons whose every
 * neighbour draws an SVG. */
function EditorIcon({ name }: { name: "up" | "down" | "remove" }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="plan-node-step-icon">
      {name === "up" && <path d="M8 12.5v-9M4.5 7 8 3.5 11.5 7" />}
      {name === "down" && <path d="M8 3.5v9M4.5 9 8 12.5 11.5 9" />}
      {name === "remove" && <path d="m4.5 4.5 7 7M11.5 4.5l-7 7" />}
    </svg>
  );
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
  const denyButtonRef = useRef<HTMLButtonElement>(null);
  const activityDetailsRef = useRef<HTMLDetailsElement>(null);
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
    if (running && activityDetailsRef.current?.open) {
      activityListRef.current?.scrollTo({ top: activityListRef.current.scrollHeight });
    }
  }, [data.builderActivity, running]);

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
      denyButtonRef.current?.focus();
    }
  }, [data.builderAwaitingToolApproval, collapsed]);

  return (
    <NodeShell
      kindClassName="plan-node"
      selected={!!selected}
      collapsed={collapsed}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
      header={
        <div className="scene-node-title plan-node-title">
          <span className="plan-node-badge">Build</span>
          <span className="plan-node-goal">{data.planGoal || "Untitled build"}</span>
          <CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={data.onToggleCollapse} />
        </div>
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
                      <input
                        type="text"
                        className="plan-node-step-input nodrag"
                        value={step.title}
                        aria-label={`Step ${index + 1} title`}
                        onChange={(event) => retitleStep(index, event.target.value)}
                      />
                      <button
                        type="button"
                        className="plan-node-step-action nodrag"
                        aria-label={`Move step ${index + 1} up`}
                        disabled={!canMoveUp(index)}
                        onClick={() => moveStep(index, -1)}
                      >
                        <EditorIcon name="up" />
                      </button>
                      <button
                        type="button"
                        className="plan-node-step-action nodrag"
                        aria-label={`Move step ${index + 1} down`}
                        disabled={!canMoveDown(index)}
                        onClick={() => moveStep(index, 1)}
                      >
                        <EditorIcon name="down" />
                      </button>
                      <button
                        type="button"
                        className="plan-node-step-action nodrag"
                        aria-label={`Remove step ${index + 1}`}
                        onClick={() => removeStep(index)}
                      >
                        <EditorIcon name="remove" />
                      </button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="plan-node-step-editor-actions">
            <button type="button" className="plan-node-button nodrag" onClick={addStep}>
              Add step
            </button>
            <button
              type="button"
              className="plan-node-button plan-node-button-start nodrag"
              disabled={!draftIsValid}
              onClick={saveSteps}
            >
              Save plan
            </button>
            <button type="button" className="plan-node-button nodrag" onClick={cancelEditing}>
              Cancel
            </button>
          </div>
          {!draftIsValid && (
            <p className="plan-node-detail" role="alert">
              Every step needs a title.
            </p>
          )}
        </div>
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

      {/* stage 8.7: the build's own visible record of what it did - real
          backend state (PlanState.builder_activity), not a debug aid.
          Reuses ChatNodeView's own "an assistant turn's tool calls,
          disclosed" pattern (.chat-node-tool-invocations) rather than a
          parallel widget, since the content is the same shape (a tool
          name, an outcome, one block of detail text) - just scoped to a
          whole BUILD instead of one turn, and potentially many more rows,
          which is the one thing that needs its own scrollable container. */}
      {data.builderActivity.length > 0 && (
        <details className="chat-node-tool-invocations plan-node-activity" ref={activityDetailsRef}>
          <summary>
            {data.builderActivity.length === 1
              ? "1 activity entry"
              : `${data.builderActivity.length} activity entries`}
            {/* The error count is the only part of this summary worth
                reading at a glance, and it was set in the same muted grey
                as the row count next to it - a build that failed five tool
                calls looked exactly like one that failed none. */}
            {activityErrorCount > 0 && (
              <span className="plan-node-activity-errors">
                {` · ${activityErrorCount} error${activityErrorCount === 1 ? "" : "s"}`}
              </span>
            )}
          </summary>
          <div className="plan-node-activity-list nowheel nodrag" ref={activityListRef}>
            {data.builderActivity.map((row, index) => (
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
          <button
            type="button"
            className="plan-node-button plan-node-button-stop nodrag"
            title="Stop this build. A stopped build cannot be resumed."
            onClick={data.onCancel}
          >
            Stop
          </button>
        )}
        {UNDOABLE.has(data.builderStatus) && data.builderRunId && (
          <button
            type="button"
            className="plan-node-button plan-node-button-danger nodrag"
            onClick={data.onUndoBuild}
          >
            Undo build
          </button>
        )}
        {canEditPlan && !editing && (
          <button type="button" className="plan-node-button nodrag" onClick={startEditing}>
            Edit plan
          </button>
        )}
        {data.builderStatus === "done" && data.planSteps.length > 0 && (
          <button type="button" className="plan-node-button nodrag" onClick={data.onSaveRecipe}>
            Save as recipe
          </button>
        )}
      </div>
    </NodeShell>
  );
}

export const PlanNodeView = memo(PlanNodeViewInner);
