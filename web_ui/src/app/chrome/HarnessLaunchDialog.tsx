import { useRef, useState } from "react";
import { useReactFlow } from "@xyflow/react";
import type { WsTransport } from "../../lib/ws/transport";
import type { SceneStore } from "../canvas/sceneStore";
import { motionDuration } from "../reducedMotion";
import { Dialog, useOverlays } from "../overlays/overlays";

/**
 * PLAN-2026-08-24 H1: the workspace agent's launcher - a task and a turn
 * budget, submitted through `harness/start` (a value-returning intent
 * answering with the new node's id - the BuilderLaunchDialog precedent
 * exactly, including the close-then-center-viewport sequence; see that
 * dialog's own comments for why request(), not fireIntent, and why the
 * center is approximate).
 *
 * Deliberately smaller than the Builder's launcher: no recipes, no
 * oversight modes (the grant set is fixed and every mutating tool asks
 * for approval individually, so there is no oversight LEVEL to choose),
 * one budget number and one workspace choice.
 *
 * The workspace is chosen HERE rather than only on the node afterwards:
 * binding a folder after the fact meant letting a scratch run finish,
 * rebinding, and re-sending the same task, so the first run of every
 * real piece of work was wasted. The pick itself is the grant (the
 * backend adds the folder to the trust list when the picker returns),
 * and the run re-checks that list when it binds.
 */

const DEFAULT_MAX_TURNS = 16;
// Mirrors intents_harness.py's own _MIN/_MAX_TURNS exactly - the backend
// clamps regardless; the field should settle on what it will submit.
const MIN_TURNS = 1;
const MAX_TURNS = 64;

function clamp(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_MAX_TURNS;
  return Math.min(Math.max(Math.round(value), MIN_TURNS), MAX_TURNS);
}

export function HarnessLaunchDialog({ transport, store }: { transport: WsTransport; store: SceneStore }) {
  const overlays = useOverlays();
  const reactFlow = useReactFlow();
  const [task, setTask] = useState("");
  const [maxTurns, setMaxTurns] = useState<number>(DEFAULT_MAX_TURNS);
  const [workspace, setWorkspace] = useState("");
  const [picking, setPicking] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestRequestId = useRef(0);

  function pickWorkspace() {
    if (picking || starting) return;
    setPicking(true);
    transport
      .request("harness", "pickLaunchWorkspace", [])
      .then((folder) => {
        if (typeof folder === "string" && folder) setWorkspace(folder);
      })
      .catch(() => setError("Could not open the folder picker."))
      .finally(() => setPicking(false));
  }

  function startAgent() {
    const trimmed = task.trim();
    if (!trimmed || starting) return;
    const requestId = ++latestRequestId.current;
    setStarting(true);
    setError(null);
    transport
      .request("harness", "start", [trimmed, maxTurns, workspace])
      .then((nodeId) => {
        if (requestId !== latestRequestId.current) return;
        if (nodeId != null) {
          setTask("");
          setWorkspace("");
          overlays.close();
          const node = store.getScene().nodes.find((n) => n.id === nodeId);
          if (node) {
            reactFlow.setCenter(node.x + 180, node.y + 90, { duration: motionDuration(300) });
          }
        } else {
          setError("The agent could not start - see the notification for details.");
        }
      })
      .catch(() => {
        if (requestId !== latestRequestId.current) return;
        setError("The agent could not start - see graphlink.log for details.");
      })
      .finally(() => {
        if (requestId === latestRequestId.current) setStarting(false);
      });
  }

  return (
    <Dialog name="harness-launch" title="Agent" className="builder-launch-dialog">
      <div className="builder-launch-field">
        <label className="builder-launch-label" htmlFor="harness-task">
          What should the agent work on?
        </label>
        <textarea
          id="harness-task"
          className="builder-launch-goal"
          placeholder="e.g. Read the files in the workspace and summarize what they contain"
          value={task}
          onChange={(event) => setTask(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter breaks the line - the same contract
            // the agent card's own follow-up composer uses, so the habit
            // formed in one place works in the other.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              startAgent();
            }
          }}
          rows={3}
        />
        <p className="builder-launch-hint">
          The agent reads and writes files in its workspace, runs shell and
          Python there, and searches your knowledge. It asks before anything
          that changes your machine, and replies on the canvas.
        </p>
      </div>

      <fieldset className="builder-launch-fieldset">
        <legend>Workspace</legend>
        <p className="builder-launch-hint">
          Where the agent works. A private scratch folder unless you choose
          one of your own - choosing it here is what grants access to it.
        </p>
        <div className="harness-launch-workspace">
          <span className="harness-launch-workspace-dir" title={workspace || undefined}>
            {workspace || "Private scratch folder"}
          </span>
          <button
            type="button"
            className="builder-launch-secondary"
            onClick={pickWorkspace}
            disabled={picking || starting}
          >
            {picking ? "Choosing…" : "Choose folder…"}
          </button>
          {workspace && (
            <button
              type="button"
              className="builder-launch-secondary"
              onClick={() => setWorkspace("")}
              disabled={starting}
            >
              Use scratch
            </button>
          )}
        </div>
      </fieldset>

      <fieldset className="builder-launch-fieldset">
        <legend>Budget</legend>
        <p className="builder-launch-hint">Hard limit - the run stops when it is reached.</p>
        <div className="builder-launch-budgets">
          <label className="builder-launch-budget">
            <span className="builder-launch-budget-label">Max turns</span>
            <input
              className="builder-launch-number"
              type="number"
              min={MIN_TURNS}
              max={MAX_TURNS}
              value={maxTurns}
              onChange={(event) => setMaxTurns(Number(event.target.value))}
              onBlur={(event) => setMaxTurns(clamp(Number(event.target.value)))}
            />
          </label>
        </div>
      </fieldset>

      {error && (
        <p className="builder-launch-error" role="alert">
          {error}
        </p>
      )}

      <div className="builder-launch-actions">
        <button
          type="button"
          className="builder-launch-start"
          onClick={startAgent}
          disabled={starting || !task.trim()}
        >
          {starting ? "Starting…" : "Start the agent"}
        </button>
      </div>
    </Dialog>
  );
}
