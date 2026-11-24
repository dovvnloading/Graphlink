import type { WorkflowDocument } from "../canvas/workflowLibrary";
import { Disclosure } from "./RunnerDisclosure";

export function WorkflowSpecView({ value }: { value: WorkflowDocument }) {
  const spec = value.spec;
  return <div>
    <p>{spec.name} · {spec.steps.length} steps · {spec.inputs.length} inputs · {spec.outputs.length} outputs</p>
    <Disclosure label="Read-only workflow definition">
      <h4>Inputs</h4>
      <ul>{spec.inputs.map(port => <li key={port.name}>{port.name} · {port.kind}{port.required ? " (required)" : ""}</li>)}</ul>
      <h4>Steps</h4>
      <ol>{spec.steps.map(step => <li key={step.step_id}>
        <strong>{step.step_id} · {step.adapter.adapter_id}@{step.adapter.adapter_version}</strong>
        <div><code>{step.config_json}</code></div>
        {step.bindings.map(binding => <div key={binding.port}>{binding.port} ← {binding.source.kind === "step" ? binding.source.step_id + "." : "Input "}{binding.source.port}</div>)}
      </li>)}</ol>
      <h4>Outputs</h4>
      <ul>{spec.outputs.map(output => <li key={output.port.name}>{output.port.name} · {output.port.kind} ← {output.source.kind === "step" ? output.source.step_id + "." : "Input "}{output.source.port}</li>)}</ul>
      {!!value.parameters.length && <><h4>Parameters</h4><ul>{value.parameters.map(p => <li key={p.name}>{p.name} · {p.kind} · {p.step_id}.{p.setting}</li>)}</ul></>}
    </Disclosure>
  </div>;
}
