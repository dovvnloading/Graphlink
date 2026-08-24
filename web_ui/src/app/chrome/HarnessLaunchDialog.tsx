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
 * oversight modes (H1 runs read-only tools under a fixed grant - there is
 * nothing to choose an oversight level ABOUT yet), one budget number.
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
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestRequestId = useRef(0);

  function startAgent() {
    const trimmed = task.trim();
    if (!trimmed || starting) return;
    const requestId = ++latestRequestId.current;
    setStarting(true);
    setError(null);
    transport
      .request("harness", "start", [trimmed, maxTurns])
      .then((nodeId) => {
        if (requestId !== latestRequestId.current) return;
        if (nodeId != null) {
          setTask("");
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
          rows={3}
        />
        <p className="builder-launch-hint">
          The agent works in its own private scratch workspace with read-only
          file tools and knowledge search, and replies on the canvas.
        </p>
      </div>

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
