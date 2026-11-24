/* eslint-disable jsx-a11y/no-noninteractive-tabindex -- the definition JSON is a scrollable, read-only region that must be keyboard focusable (WCAG 2.1.1). */
import type { WorkflowDocument } from "../canvas/workflowLibrary";
import { Badge } from "../ui/Badge";
import { Disclosure } from "../ui/Disclosure";
import { Disclosure as LazyDisclosure } from "./RunnerDisclosure";
import { stepName } from "./WorkflowPreviewSummary";
import { adapterLabel, effectLabel, plural } from "./workflowApi";
import "./workflow-library.css";

/** How the workflow is built, readable without opening JSON. */
export function WorkflowDefinition({ doc }: { doc: WorkflowDocument }) {
  const spec = doc.spec;
  const source = (binding: { source: { kind: string; port: string; step_id?: string } }) =>
    binding.source.kind === "step" ? stepName(binding.source.step_id ?? "") : "input “" + stepName(binding.source.port) + "”";
  return <div className="workflow-definition">
    <section aria-label="Inputs">
      <h4>Inputs</h4>
      {spec.inputs.length
        ? <ul className="workflow-facts">{spec.inputs.map(port => <li key={port.name}>
            <span>{stepName(port.name)}</span><span className="workflow-muted">{port.kind}{port.required ? "" : " · optional"}</span>
          </li>)}</ul>
        : <p className="workflow-muted">None. Every value comes from inside the workflow.</p>}
    </section>
    <section aria-label="Steps">
      <h4>Steps</h4>
      <ol className="workflow-plan">{spec.steps.map((step, index) => <li key={step.step_id} className="workflow-plan-step">
        <span className="workflow-plan-index" aria-hidden="true">{index + 1}</span>
        <span className="workflow-plan-copy">
          <span className="workflow-plan-title">{stepName(step.step_id)}</span>
          <span className="workflow-plan-meta">{adapterLabel(step.adapter.adapter_id)}
            {step.bindings.length > 0 && " · uses " + step.bindings.map(source).join(", ")}</span>
        </span>
        <span className="workflow-plan-effect"><Badge>{effectLabel(step.adapter.side_effect_class)}</Badge></span>
      </li>)}</ol>
    </section>
    <section aria-label="Outputs">
      <h4>Outputs</h4>
      <ul className="workflow-facts">{spec.outputs.map(output => <li key={output.port.name}>
        <span>{stepName(output.port.name)}</span>
        <span className="workflow-muted">{output.port.kind} · from {source({ source: output.source })}</span>
      </li>)}</ul>
    </section>
    {doc.parameters.length > 0 && <section aria-label="Parameters">
      <h4>Settings you can change</h4>
      <ul className="workflow-facts">{doc.parameters.map(param => <li key={param.name}>
        <span>{stepName(param.name)}</span>
        <span className="workflow-muted">{param.kind === "choice" ? param.choices.join(" or ") : param.kind} · {stepName(param.step_id)}</span>
      </li>)}</ul>
    </section>}
    <Disclosure title="Definition as JSON">
      <pre className="workflow-pre" tabIndex={0} role="region" aria-label="Workflow definition JSON">{JSON.stringify(spec, null, 2)}</pre>
    </Disclosure>
  </div>;
}


/** Compact summary for a pinned Workflow node; the definition opens on demand. */
export function WorkflowSpecView({ value }: { value: WorkflowDocument }) {
  const spec = value.spec;
  return <div className="workflow-spec-summary">
    <p className="workflow-muted">{plural(spec.steps.length, "step")} · {plural(spec.inputs.length, "input")} · {plural(spec.outputs.length, "output")}</p>
    <LazyDisclosure label="How it works"><WorkflowDefinition doc={value} /></LazyDisclosure>
  </div>;
}
