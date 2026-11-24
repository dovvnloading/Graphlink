import type { WorkflowSpec } from "../../lib/workflow-contract/generated/workflow";
import { Badge } from "../ui/Badge";
import { Icon } from "../ui/icons/Icon";
import { adapterLabel, effectDetail, effectLabel, money, plural, type ControlPreview, type WorkflowPreview } from "./workflowApi";

/** "rewrite_prompt" → "Rewrite prompt". Step IDs are stable keys; this is only their label. */
export function stepName(stepId: string) {
  const text = stepId.replace(/[_-]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : stepId;
}

function controlNote(control: ControlPreview): string | null {
  if (control.message) return "Pauses for your approval: " + control.message;
  if (control.paths?.length) return "Continues on " + control.paths.join(" or ") + (control.predicate ? " (" + control.predicate + ")" : "");
  if (control.mode) return control.mode === "first" ? "Uses the first input to finish" : "Waits for every input that runs";
  if (control.item_count !== undefined) {
    return `Repeats for ${plural(control.item_count, "item")} · estimated ${money(control.estimated_cost_usd ?? 0)} in total`;
  }
  return null;
}

/** What a run will do, shown before anything runs. */
export function WorkflowPreviewSummary({ preview, spec, label = "Run preview" }: {
  preview: WorkflowPreview; spec?: WorkflowSpec; label?: string;
}) {
  const adapters = new Map(spec?.steps.map(step => [step.step_id, step.adapter.adapter_id]) ?? []);
  const external = preview.steps.filter(([, effect]) => effect !== "pure" && effect !== "local-read").length;
  return <section className="workflow-preview" aria-label={label}>
    <div className="workflow-preview-summary">
      <span>{plural(preview.steps.length, "step")}</span>
      <span>Estimated cost {money(preview.estimated_cost_usd)}</span>
      <span>{preview.requires_approval ? "Needs your approval to start" : "Starts without approval"}</span>
    </div>
    {external > 0 && <p className="workflow-muted">
      {plural(external, "step")} {external === 1 ? "reaches" : "reach"} beyond this graph. Check the model and cost of each before you approve.
    </p>}
    <ol className="workflow-plan">
      {preview.steps.map(([id, effect, policy], index) => {
        const note = preview.control_flow?.[id] ? controlNote(preview.control_flow[id]) : null;
        const adapter = adapters.get(id);
        return <li key={id} className="workflow-plan-step">
          <span className="workflow-plan-index" aria-hidden="true">{index + 1}</span>
          <span className="workflow-plan-copy">
            <span className="workflow-plan-title">{stepName(id)}</span>
            <span className="workflow-plan-meta">
              {adapter ? adapterLabel(adapter) : "Step"}
              {policy.model_identity && <> · <Icon name="node-model" size="sm" /> {policy.model_identity.join(" · ")}</>}
              {policy.cost_usd > 0 && <> · {money(policy.cost_usd)}</>}
            </span>
            {note && <span className="workflow-plan-note">{note}</span>}
          </span>
          <span className="workflow-plan-effect" aria-label={effectLabel(effect) + ": " + effectDetail(effect)}>
            <Badge>{effectLabel(effect)}</Badge>
          </span>
        </li>;
      })}
    </ol>
  </section>;
}
