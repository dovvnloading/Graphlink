import { useEffect, useRef, useState } from "react";
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

interface RecipeRow {
  name: string;
  description: string;
  builtIn: boolean;
}

export function BuilderLaunchDialog({ transport }: { transport: WsTransport }) {
  const overlays = useOverlays();
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<"copilot" | "autopilot">("copilot");
  const [maxSteps, setMaxSteps] = useState(DEFAULT_MAX_STEPS);
  const [maxTokens, setMaxTokens] = useState(DEFAULT_MAX_TOKENS);
  const [maxWallSeconds, setMaxWallSeconds] = useState(DEFAULT_MAX_WALL_SECONDS);
  const [recipes, setRecipes] = useState<RecipeRow[]>([]);
  const [recipe, setRecipe] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestRequestId = useRef(0);
  const open = overlays.isOpen("builder-launch");

  useEffect(() => {
    if (!open) return;
    let stale = false;
    transport
      .request("builder", "listRecipes", [])
      .then((value) => {
        if (stale) return;
        const payload = value as { recipes: RecipeRow[] };
        setRecipes(payload.recipes ?? []);
      })
      .catch(() => {
        // Recipes are a convenience - a failed list never blocks a
        // from-scratch launch.
      });
    return () => {
      stale = true;
    };
  }, [open, transport]);

  function startBuild() {
    const trimmed = goal.trim();
    // A recipe carries its own goal; from-scratch needs one typed.
    if ((!trimmed && !recipe) || starting) return;
    const requestId = ++latestRequestId.current;
    setStarting(true);
    setError(null);
    transport
      .request("builder", "start", [trimmed, mode, maxSteps, maxTokens, maxWallSeconds, recipe || null])
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

  // The selected recipe's own description - listRecipes has always
  // returned it, but nothing ever rendered it, so the picker gave no clue
  // what a recipe actually builds until after launching it.
  const selectedRecipe = recipes.find((r) => r.name === recipe) ?? null;

  return (
    <Dialog name="builder-launch" title="Builder" className="builder-launch-dialog">
      <div className="builder-launch-field">
        <label className="builder-launch-label" htmlFor="builder-recipe">
          Recipe
        </label>
        <select
          id="builder-recipe"
          className="builder-launch-select"
          value={recipe}
          onChange={(event) => setRecipe(event.target.value)}
        >
          <option value="">Start from scratch</option>
          {recipes.map((r) => (
            <option key={r.name} value={r.name}>
              {r.name}
              {r.builtIn ? " (built-in)" : ""}
            </option>
          ))}
        </select>
        <p className="builder-launch-hint">
          {selectedRecipe?.description || "Describe a build yourself, or pick a saved recipe."}
        </p>
      </div>

      <div className="builder-launch-field">
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
        {recipe && (
          <p className="builder-launch-hint">
            Optional - the recipe carries its own goal. Anything typed here is added to it.
          </p>
        )}
      </div>

      <fieldset className="builder-launch-fieldset">
        <legend>Oversight</legend>
        <label className={`builder-launch-choice${mode === "copilot" ? " selected" : ""}`}>
          <input
            type="radio"
            name="builder-mode"
            checked={mode === "copilot"}
            onChange={() => setMode("copilot")}
          />
          <span className="builder-launch-choice-title">Co-pilot</span>
          <span className="builder-launch-choice-desc">Approve every mutating step.</span>
        </label>
        <label className={`builder-launch-choice${mode === "autopilot" ? " selected" : ""}`}>
          <input
            type="radio"
            name="builder-mode"
            checked={mode === "autopilot"}
            onChange={() => setMode("autopilot")}
          />
          <span className="builder-launch-choice-title">Autopilot</span>
          <span className="builder-launch-choice-desc">Run to completion within the budgets.</span>
        </label>
        {mode === "autopilot" && (
          <p className="builder-launch-disclosure" role="note">
            Autopilot will create and edit nodes and execute code without
            asking, within the budgets below. Resource caps still apply, and
            network access will still ask every time.
          </p>
        )}
      </fieldset>

      <fieldset className="builder-launch-fieldset">
        <legend>Budgets</legend>
        <p className="builder-launch-hint">Hard limits - a breach pauses the build.</p>
        <div className="builder-launch-budgets">
          <label className="builder-launch-budget">
            <span className="builder-launch-budget-label">Max steps</span>
            <input
              className="builder-launch-number"
              type="number"
              min={1}
              max={50}
              value={maxSteps}
              onChange={(event) => setMaxSteps(Number(event.target.value) || DEFAULT_MAX_STEPS)}
            />
          </label>
          <label className="builder-launch-budget">
            <span className="builder-launch-budget-label">Max tokens</span>
            <input
              className="builder-launch-number"
              type="number"
              min={1000}
              step={10_000}
              value={maxTokens}
              onChange={(event) => setMaxTokens(Number(event.target.value) || DEFAULT_MAX_TOKENS)}
            />
          </label>
          <label className="builder-launch-budget">
            <span className="builder-launch-budget-label">Max seconds</span>
            <input
              className="builder-launch-number"
              type="number"
              min={30}
              step={60}
              value={maxWallSeconds}
              onChange={(event) => setMaxWallSeconds(Number(event.target.value) || DEFAULT_MAX_WALL_SECONDS)}
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
          onClick={startBuild}
          disabled={starting || (!goal.trim() && !recipe)}
        >
          {starting ? "Planning…" : recipe ? "Start from recipe" : "Plan the build"}
        </button>
      </div>
    </Dialog>
  );
}
