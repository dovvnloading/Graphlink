import { useRef, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { Dialog, useOverlays } from "../overlays/overlays";

/**
 * ADR-008 stage 8.3: the Builder's launcher - goal, oversight mode, and
 * the three hard budgets, submitted through `builder/start` (a
 * value-returning intent: it answers with the new plan node's id, so
 * transport.request(), the DiagnosticsDialog/KnowledgeSearchDialog
 * precedent, not fireIntent). On success the dialog closes and the plan
 * node - the visible, editable checklist - takes over on the canvas.
 *
 * Autopilot is a per-run, disclosed choice (the ADR's own decision #3):
 * selecting it surfaces the disclosure sentence inline, before launch -
 * graph edits and code execution will proceed WITHOUT per-step approval
 * (resource caps still apply); network access always still asks.
 */

const DEFAULT_MAX_STEPS = 12;
const DEFAULT_MAX_TOKENS = 150_000;
const DEFAULT_MAX_WALL_SECONDS = 900;

export function BuilderLaunchDialog({ transport }: { transport: WsTransport }) {
  const overlays = useOverlays();
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<"copilot" | "autopilot">("copilot");
  const [maxSteps, setMaxSteps] = useState(DEFAULT_MAX_STEPS);
  const [maxTokens, setMaxTokens] = useState(DEFAULT_MAX_TOKENS);
  const [maxWallSeconds, setMaxWallSeconds] = useState(DEFAULT_MAX_WALL_SECONDS);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestRequestId = useRef(0);

  function startBuild() {
    const trimmed = goal.trim();
    if (!trimmed || starting) return;
    const requestId = ++latestRequestId.current;
    setStarting(true);
    setError(null);
    transport
      .request("builder", "start", [trimmed, mode, maxSteps, maxTokens, maxWallSeconds])
      .then((nodeId) => {
        if (requestId !== latestRequestId.current) return;
        if (nodeId != null) {
          setGoal("");
          overlays.close();
        } else {
          setError("The build could not start - see the notification for details.");
        }
      })
      .catch(() => {
        if (requestId !== latestRequestId.current) return;
        setError("The build could not start - see graphlink.log for details.");
      })
      .finally(() => {
        if (requestId === latestRequestId.current) setStarting(false);
      });
  }

  return (
    <Dialog name="builder-launch" title="Builder" className="builder-launch-dialog">
      <label className="builder-launch-label" htmlFor="builder-goal">
        What should the Builder construct?
      </label>
      <textarea
        id="builder-goal"
        className="builder-launch-goal"
        placeholder="e.g. Research recent solar output trends, compute the growth rate, and chart it"
        value={goal}
        onChange={(event) => setGoal(event.target.value)}
        rows={3}
      />

      <fieldset className="builder-launch-mode">
        <legend>Oversight</legend>
        <label>
          <input
            type="radio"
            name="builder-mode"
            checked={mode === "copilot"}
            onChange={() => setMode("copilot")}
          />
          Co-pilot — approve every mutating step
        </label>
        <label>
          <input
            type="radio"
            name="builder-mode"
            checked={mode === "autopilot"}
            onChange={() => setMode("autopilot")}
          />
          Autopilot — run to completion within the budgets
        </label>
        {mode === "autopilot" && (
          <p className="builder-launch-disclosure" role="note">
            Autopilot will create and edit nodes and execute code without
            asking, within the budgets below. Resource caps still apply, and
            network access will still ask every time.
          </p>
        )}
      </fieldset>

      <fieldset className="builder-launch-budgets">
        <legend>Budgets (hard limits — a breach pauses the build)</legend>
        <label>
          Max steps
          <input
            type="number"
            min={1}
            max={50}
            value={maxSteps}
            onChange={(event) => setMaxSteps(Number(event.target.value) || DEFAULT_MAX_STEPS)}
          />
        </label>
        <label>
          Max tokens
          <input
            type="number"
            min={1000}
            step={10_000}
            value={maxTokens}
            onChange={(event) => setMaxTokens(Number(event.target.value) || DEFAULT_MAX_TOKENS)}
          />
        </label>
        <label>
          Max seconds
          <input
            type="number"
            min={30}
            step={60}
            value={maxWallSeconds}
            onChange={(event) => setMaxWallSeconds(Number(event.target.value) || DEFAULT_MAX_WALL_SECONDS)}
          />
        </label>
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
          onClick={startBuild}
          disabled={starting || !goal.trim()}
        >
          {starting ? "Planning…" : "Plan the build"}
        </button>
      </div>
    </Dialog>
  );
}
