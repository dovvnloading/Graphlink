import { useEffect, useRef, useState } from "react";
import { useReactFlow } from "@xyflow/react";
import type { WsTransport } from "../../lib/ws/transport";
import type { SceneStore } from "../canvas/sceneStore";
import { motionDuration } from "../reducedMotion";
import { Dialog, useOverlays } from "../overlays/overlays";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Disclosure } from "../ui/Disclosure";
import { InlineAlert } from "../ui/InlineAlert";
import { NumberField } from "../ui/NumberField";
import { RadioGroup } from "../ui/RadioGroup";
import { SegmentedControl } from "../ui/SegmentedControl";
import { Select } from "../ui/Select";
import { TextArea } from "../ui/TextArea";
import { formatCount } from "../ui/format";
import "./BuilderLaunchDialog.css";

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
 * FORM ORDER. The goal comes first and the recipe picker second, which is
 * the reverse of how this launcher shipped. A recipe is an optional
 * shortcut past the goal; leading with it put the exception before the
 * rule, and it also stranded the one line explaining the whole dialog
 * ("Describe a build yourself, or pick a saved recipe") underneath the
 * recipe field, where it read as help text for the picker rather than as
 * the choice the form is actually offering. That sentence is the dialog's
 * intro now, and each field carries help about itself.
 *
 * SECTIONS. Oversight and Budgets are still real <fieldset>/<legend>
 * elements - a radio group needs the grouping semantics and the legend is
 * its accessible name - but they no longer render as browser-default
 * fieldsets. The notched 1px box was the only instance of that treatment in
 * the app; the legend is styled as a section title, exactly like
 * ViewPopover's and Settings' own section headings (styles.css), so this
 * dialog reads as part of the same product as everything around it.
 *
 * SHARED VOCABULARY. Every `builder-launch-*` class here is also used by
 * HarnessLaunchDialog (the workspace-agent launcher), which is why the
 * work of this pass is mostly in styles.css rather than in this file: the
 * two launchers are one design, and fixing the shared classes fixes both.
 * Anything genuinely specific to the Builder - the recipe preview card, the
 * oversight choice cards - stays scoped to its own class here.
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

function formatBudgetLine(steps: number, tokens: number, seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return `${formatCount(steps)} step${steps === 1 ? "" : "s"} · ${formatCount(tokens)} tokens · ${minutes} min`;
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
      .then((deleted) => {
        // A resolved `false` (unknown name, or a refused built-in) is NOT a
        // rejected promise - deleteRecipe's own backend already surfaced
        // why via its own notification (backend/api/intents_builder.py).
        // Refreshing the list and clearing the selection here regardless
        // would silently claim success on a call that changed nothing.
        if (!deleted) return;
        return transport.request("builder", "listRecipes", []).then((value) => {
          const payload = value as { recipes: RecipeRow[] };
          setRecipes(payload.recipes ?? []);
          setRecipe("");
        });
      })
      .catch(() => {
        setError("The recipe could not be deleted - see graphlink.log for details.");
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
  const canStart = Boolean(goal.trim() || recipe);

  return (
    <Dialog name="builder-launch" {...{ title: "Builder" }} size="md" className="builder-launch-dialog"
      status={!canStart ? "Describe a goal, or pick a recipe." : "Review the plan before anything runs."}
      footer={<Button variant="primary" onClick={startBuild} disabled={starting || !canStart} loading={starting}>
        Plan the build
      </Button>}>
      <p className="builder-launch-intro">
        The Builder plans a job as a checklist, then works through it on the canvas.
        Describe what you want built, or start from a saved recipe.
      </p>
      <TextArea id="builder-goal" label="Goal" value={goal} onValueChange={setGoal}
        minRows={3} maxRows={8}
        placeholder="Research recent solar output trends, compute the growth rate, and chart it"
        description={recipe
          ? "Optional — the recipe carries its own goal. Anything typed here is added to it."
          : "One or two sentences. Review the plan before anything runs."} />
      <Select label="Recipe" value={recipe} onChange={setRecipe}
        options={[{ value: "", label: "Start from scratch" }, ...recipes.map((row) => ({
          value: row.name, label: row.builtIn ? row.name + " (built-in)" : row.name,
          description: row.description || undefined,
        }))]} />
      {selectedRecipe && <Card {...{ title: selectedRecipe.name }} className="builder-launch-recipe-preview">
        {selectedRecipe.description && <p>{selectedRecipe.description}</p>}
        {selectedRecipe.steps.length > 0 && <>
          <p>Starts with {formatCount(selectedRecipe.steps.length)} steps</p>
          <ol>{selectedRecipe.steps.map((name, index) => <li key={index}>{name}</li>)}</ol>
        </>}
        {!selectedRecipe.builtIn && <Button destructive size="sm" variant="ghost"
          onClick={deleteSelectedRecipe} disabled={deletingRecipe} loading={deletingRecipe}>
          Delete this recipe
        </Button>}
      </Card>}
      <RadioGroup label="Oversight" variant="card" value={mode} onValueChange={(next) => setMode(next as typeof mode)}
        options={[
          { value: "copilot", label: "Co-pilot", description: "Approve every mutating step." },
          { value: "autopilot", label: "Autopilot", description: "Run to completion within the budgets." },
        ]} />
      {mode === "autopilot" && <InlineAlert variant="warning" {...{ title: "Autopilot approval" }}
        body="Autopilot creates and edits nodes and executes code without asking, within the limits below. Network access still requires approval." />}
      <div className="builder-launch-budgets-control">
        <h3>Budgets</h3>
        <SegmentedControl label="Budget preset" value={selectedPresetId ?? "custom"}
          options={[...BUDGET_PRESETS.map((preset) => ({ value: preset.id, label: preset.label })),
            ...(selectedPresetId ? [] : [{ value: "custom", label: "Custom" }])]}
          onChange={(id) => {
            const preset = BUDGET_PRESETS.find((item) => item.id === id);
            if (!preset) return;
            setMaxSteps(preset.maxSteps);
            setMaxTokens(preset.maxTokens);
            setMaxWallSeconds(preset.maxWallSeconds);
          }} />
        <p className="builder-launch-budget-summary">{formatBudgetLine(maxSteps, maxTokens, maxWallSeconds)}</p>
        <p className="builder-launch-hint">Hard limits — a breach pauses the build.</p>
        <Disclosure {...{ title: "Set exact limits" }}>
          <div className="builder-launch-budgets">
            <NumberField label="Max steps" min={MIN_STEPS} max={MAX_STEPS} value={maxSteps}
              onValueChange={(value) => setMaxSteps(value === "" ? DEFAULT_MAX_STEPS : value)} />
            <NumberField label="Max tokens" min={MIN_TOKENS} max={MAX_TOKENS} step={10000} value={maxTokens}
              onValueChange={(value) => setMaxTokens(value === "" ? DEFAULT_MAX_TOKENS : value)} />
            <NumberField label="Max seconds" min={MIN_WALL_SECONDS} max={MAX_WALL_SECONDS} step={60}
              value={maxWallSeconds} onValueChange={(value) => setMaxWallSeconds(value === "" ? DEFAULT_MAX_WALL_SECONDS : value)} />
          </div>
        </Disclosure>
      </div>
      {error && <InlineAlert variant="error" {...{ title: "Couldn't plan the build" }} body={error} />}
    </Dialog>
  );
}
