import { useEffect, useRef, useState } from "react";
import { useReactFlow } from "@xyflow/react";
import type { WsTransport } from "../../lib/ws/transport";
import type { SceneStore } from "../canvas/sceneStore";
import { motionDuration } from "../reducedMotion";
import { Dialog, useOverlays } from "../overlays/overlays";
import { CustomSelect } from "./CustomSelect";

/**
 * ADR-008 stage 8.3/8.7: the Builder's launcher - goal, oversight mode, and
 * the three hard budgets, submitted through `builder/start` (a
 * value-returning intent: it answers with the new plan node's id, so
 * transport.request(), the DiagnosticsDialog/KnowledgeSearchDialog
 * precedent, not fireIntent). On success the dialog closes, the viewport
 * centers on the new plan node (stage 8.7 - it previously landed wherever
 * the scene's own extent happened to place it, off-screen as often as not),
 * and the plan node - the visible, editable checklist - takes over on the
 * canvas.
 *
 * Autopilot is a per-run, disclosed choice (the ADR's own decision #3):
 * selecting it surfaces the disclosure sentence inline, before launch -
 * graph edits and code execution will proceed WITHOUT per-step approval
 * (resource caps still apply); network access always still asks.
 *
 * stage 8.7 rebuild: this was the last dialog in the app still using a bare
 * `<select>` (CustomSelect exists precisely to retire that - see its own
 * module doc) and three unlabelled-scale number spinners with no sense of
 * what they cost. Now: CustomSelect for the recipe picker, a live preview
 * of a selected recipe's own steps (the payload always carried them -
 * nothing rendered them), named budget TIERS with a plain-language summary
 * line in place of raw numbers, and the exact numbers moved behind an
 * "Advanced" disclosure for whoever wants them. Recipe deletion (builder/
 * deleteRecipe) rounds out the lifecycle saveRecipe started.
 */

const DEFAULT_MAX_STEPS = 12;
const DEFAULT_MAX_TOKENS = 150_000;
const DEFAULT_MAX_WALL_SECONDS = 900;

// Mirrors intents_builder.py's own _MIN/_MAX_*_BUDGET constants exactly -
// the backend clamps to these regardless, but the field should visually
// settle on the value it is actually about to submit.
const MIN_STEPS = 1;
const MAX_STEPS = 50;
const MIN_TOKENS = 1_000;
const MAX_TOKENS = 2_000_000;
const MIN_WALL_SECONDS = 30;
const MAX_WALL_SECONDS = 7_200;

// Named tiers in place of three bare numbers - "150000 tokens" means
// nothing on its own sight-read; a tier name plus a plain-language budget
// line does. Standard matches the launcher's own prior hardcoded defaults
// exactly, so an existing habit of "just launch" is unaffected.
const BUDGET_PRESETS = [
  { id: "quick", label: "Quick", maxSteps: 6, maxTokens: 50_000, maxWallSeconds: 300 },
  { id: "standard", label: "Standard", maxSteps: DEFAULT_MAX_STEPS, maxTokens: DEFAULT_MAX_TOKENS, maxWallSeconds: DEFAULT_MAX_WALL_SECONDS },
  { id: "extended", label: "Extended", maxSteps: 25, maxTokens: 400_000, maxWallSeconds: 1_800 },
] as const;

function clamp(value: number, low: number, high: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.min(Math.max(value, low), high);
}

function formatBudgetLine(steps: number, tokens: number, seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return `${steps} step${steps === 1 ? "" : "s"} · ${Math.round(tokens / 1000).toLocaleString()}k tokens · ${minutes} min`;
}

interface RecipeRow {
  name: string;
  description: string;
  goal: string;
  steps: string[];
  mode: "copilot" | "autopilot";
  builtIn: boolean;
}

export function BuilderLaunchDialog({ transport, store }: { transport: WsTransport; store: SceneStore }) {
  const overlays = useOverlays();
  const reactFlow = useReactFlow();
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<"copilot" | "autopilot">("copilot");
  const [maxSteps, setMaxSteps] = useState<number>(DEFAULT_MAX_STEPS);
  const [maxTokens, setMaxTokens] = useState<number>(DEFAULT_MAX_TOKENS);
  const [maxWallSeconds, setMaxWallSeconds] = useState<number>(DEFAULT_MAX_WALL_SECONDS);
  const [recipes, setRecipes] = useState<RecipeRow[]>([]);
  const [recipe, setRecipe] = useState("");
  const [starting, setStarting] = useState(false);
  const [deletingRecipe, setDeletingRecipe] = useState(false);
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
          // The launcher has no canvas anchor of its own, so the plan node
          // can land anywhere the scene's own extent happens to place it -
          // often off the current viewport entirely. intents_builder.start()
          // already awaited publish_scene() before answering with this id,
          // so the node's position is already in the store by the time this
          // callback fires. Center is approximate (the node's real rendered
          // size isn't known until React Flow measures it) - this only
          // needs to bring it into view, not frame it exactly.
          const node = store.getScene().nodes.find((n) => n.id === nodeId);
          if (node) {
            reactFlow.setCenter(node.x + 180, node.y + 90, { duration: motionDuration(300) });
          }
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

  function deleteSelectedRecipe() {
    if (!recipe || deletingRecipe) return;
    setDeletingRecipe(true);
    transport
      .request("builder", "deleteRecipe", [recipe])
      .then(() => transport.request("builder", "listRecipes", []))
      .then((value) => {
        const payload = value as { recipes: RecipeRow[] };
        setRecipes(payload.recipes ?? []);
        setRecipe("");
      })
      .finally(() => setDeletingRecipe(false));
  }

  // The selected recipe's own description AND steps - listRecipes has
  // always returned both, but nothing ever rendered either, so the picker
  // gave no clue what a recipe actually builds until after launching it.
  const selectedRecipe = recipes.find((r) => r.name === recipe) ?? null;
  const selectedPresetId =
    BUDGET_PRESETS.find(
      (preset) =>
        preset.maxSteps === maxSteps &&
        preset.maxTokens === maxTokens &&
        preset.maxWallSeconds === maxWallSeconds,
    )?.id ?? null;

  return (
    <Dialog name="builder-launch" title="Builder" className="builder-launch-dialog">
      <div className="builder-launch-field">
        {/* A plain heading, not a <label>: CustomSelect is not a native
            form control with an id to associate via htmlFor, and it
            already carries its own accessible name via ariaLabel below -
            the same "visible section text + CustomSelect's own ariaLabel,
            no separate <label>" shape ViewPopover's font-family picker
            uses. */}
        <p className="builder-launch-label">Recipe</p>
        <CustomSelect
          value={recipe}
          options={[
            { id: "", label: "Start from scratch" },
            ...recipes.map((r) => ({
              id: r.name,
              label: r.builtIn ? `${r.name} (built-in)` : r.name,
              description: r.description || undefined,
            })),
          ]}
          onChange={setRecipe}
          ariaLabel="Recipe"
        />
        {selectedRecipe ? (
          <div className="builder-launch-recipe-preview">
            {selectedRecipe.description && (
              <p className="builder-launch-hint">{selectedRecipe.description}</p>
            )}
            {selectedRecipe.steps.length > 0 && (
              <ol className="builder-launch-recipe-steps">
                {selectedRecipe.steps.map((title, index) => (
                  <li key={index}>{title}</li>
                ))}
              </ol>
            )}
            {!selectedRecipe.builtIn && (
              <button
                type="button"
                className="builder-launch-delete-recipe"
                onClick={deleteSelectedRecipe}
                disabled={deletingRecipe}
              >
                {deletingRecipe ? "Deleting…" : "Delete this recipe"}
              </button>
            )}
          </div>
        ) : (
          <p className="builder-launch-hint">Describe a build yourself, or pick a saved recipe.</p>
        )}
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
        <div className="view-segment" role="group" aria-label="Budget preset">
          {BUDGET_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              className={"view-segment-btn" + (selectedPresetId === preset.id ? " active" : "")}
              aria-pressed={selectedPresetId === preset.id}
              onClick={() => {
                setMaxSteps(preset.maxSteps);
                setMaxTokens(preset.maxTokens);
                setMaxWallSeconds(preset.maxWallSeconds);
              }}
            >
              {preset.label}
            </button>
          ))}
        </div>
        <p className="builder-launch-hint">{formatBudgetLine(maxSteps, maxTokens, maxWallSeconds)}</p>

        <details className="builder-launch-advanced">
          <summary>Advanced</summary>
          <div className="builder-launch-budgets">
            <label className="builder-launch-budget">
              <span className="builder-launch-budget-label">Max steps</span>
              <input
                className="builder-launch-number"
                type="number"
                min={MIN_STEPS}
                max={MAX_STEPS}
                value={maxSteps}
                onChange={(event) => setMaxSteps(Number(event.target.value))}
                onBlur={(event) =>
                  setMaxSteps(clamp(Number(event.target.value), MIN_STEPS, MAX_STEPS, DEFAULT_MAX_STEPS))
                }
              />
            </label>
            <label className="builder-launch-budget">
              <span className="builder-launch-budget-label">Max tokens</span>
              <input
                className="builder-launch-number"
                type="number"
                min={MIN_TOKENS}
                max={MAX_TOKENS}
                step={10_000}
                value={maxTokens}
                onChange={(event) => setMaxTokens(Number(event.target.value))}
                onBlur={(event) =>
                  setMaxTokens(clamp(Number(event.target.value), MIN_TOKENS, MAX_TOKENS, DEFAULT_MAX_TOKENS))
                }
              />
            </label>
            <label className="builder-launch-budget">
              <span className="builder-launch-budget-label">Max seconds</span>
              <input
                className="builder-launch-number"
                type="number"
                min={MIN_WALL_SECONDS}
                max={MAX_WALL_SECONDS}
                step={60}
                value={maxWallSeconds}
                onChange={(event) => setMaxWallSeconds(Number(event.target.value))}
                onBlur={(event) =>
                  setMaxWallSeconds(
                    clamp(Number(event.target.value), MIN_WALL_SECONDS, MAX_WALL_SECONDS, DEFAULT_MAX_WALL_SECONDS),
                  )
                }
              />
            </label>
          </div>
        </details>
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
