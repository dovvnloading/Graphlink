import { memo, useEffect, useState } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { useOverlays } from "../overlays/overlays";
import { Button } from "../ui/Button";
import { TextArea } from "../ui/TextArea";
import { TextField } from "../ui/TextField";
import { NumberField } from "../ui/NumberField";
import { Select } from "../ui/Select";
import { InlineAlert } from "../ui/InlineAlert";
import { useConfirm } from "../ui/ConfirmDialog";
import { NodeShell } from "./NodeShell";
import { NodeHeading } from "./NodeHeading";
import { CollapseToggleButton } from "./CollapseToggleButton";
import { useLodVisibility } from "./useLodVisibility";
import { WorkflowSpecView } from "../chrome/WorkflowSpecView";
import type { SceneStore } from "./sceneStore";
import { readWorkflowNode, workflowPin, type WorkflowDocument, type WorkflowNodeState, type WorkflowVersion } from "./workflowLibrary";

export interface WorkflowNodeData extends Record<string, unknown> {
  title: string;
  raw: string;
  isCollapsed: boolean;
  store: SceneStore;
}
export type WorkflowFlowNode = Node<WorkflowNodeData, "workflow">;

function Editor({ id, state, store, x, y }: { id: string; state: WorkflowNodeState; store: SceneStore; x: number; y: number }) {
  const overlays = useOverlays();
  const confirm = useConfirm();
  const spec = state.document.spec;
  const [inputs, setInputs] = useState<Record<string, string>>(() => Object.fromEntries(spec.inputs.map(p => [
    p.name, p.kind === "text" ? String(state.inputs[p.name] ?? "") : JSON.stringify(state.inputs[p.name] ?? null),
  ])));
  const [parameters, setParameters] = useState<Record<string, string | number>>(state.parameters);
  const [latest, setLatest] = useState<WorkflowVersion | null>(null);
  const [preview, setPreview] = useState<WorkflowDocument | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function checkVersion() {
    const history = await store.workflowLibrary<WorkflowVersion[]>("history", { workflow_id: spec.workflow_id });
    setLatest(history[0] && history[0].version > state.version ? history[0] : null);
  }
  useEffect(() => {
    void checkVersion().catch(e => setError(String(e)));
  }, [store, spec.workflow_id, spec.revision, state.version]);
  async function act(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const values = () => Object.fromEntries(spec.inputs.map(p => [p.name, p.kind === "text" ? inputs[p.name] : JSON.parse(inputs[p.name])]));
  async function save() {
    await store.mutateWorkflowNode("configure", { node_id: id, inputs: values(), parameters });
  }
  return <>
    <p>Workflow · pinned version {state.version}</p>
    <WorkflowSpecView value={state.document} />
    {error && <InlineAlert variant="error" title="Workflow" body={error} />}
    <Button size="sm" disabled={busy} onClick={() => void act(checkVersion)}>Check for newer version</Button>
    {latest && <InlineAlert variant="info" title="Newer version available" body={"Version " + latest.version + " is available. Your pinned version will not change automatically."} />}
    {latest && !preview && <Button size="sm" disabled={busy} onClick={() => void act(async () => {
      setPreview(await store.workflowLibrary<WorkflowDocument>("get", { workflow_id: spec.workflow_id, revision: latest.revision }));
    })}>Preview upgrade</Button>}
    {preview && latest && <section>
      <h4>Upgrade preview</h4>
      <p>Current version {state.version} → version {latest.version}. Upgrading clears saved inputs, parameters and outputs; review the new definition.</p>
      <WorkflowSpecView value={preview} />
      <Button disabled={busy} variant="primary" onClick={() => void act(async () => {
        if (!await confirm({ title: "Upgrade pinned workflow?", body: "The node will use the reviewed version. Existing input values and outputs will be cleared.", confirmLabel: "Upgrade" })) return;
        await store.mutateWorkflowNode("upgrade", { node_id: id, ...workflowPin(preview), expected_revision: spec.revision });
        setPreview(null);
      })}>Upgrade to version {latest.version}</Button>
      <Button onClick={() => setPreview(null)}>Cancel preview</Button>
    </section>}
    {spec.inputs.map(port => <TextArea key={port.name} label={port.name + " · " + port.kind} value={inputs[port.name] ?? ""}
      disabled={busy} onValueChange={value => setInputs(old => ({ ...old, [port.name]: value }))} />)}
    {state.document.parameters.map(p => {
      const step = spec.steps.find(s => s.step_id === p.step_id);
      const defaultValue = step ? JSON.parse(step.config_json)[p.setting] : "";
      const value = parameters[p.name] ?? defaultValue;
      const change = (next: string | number) => setParameters(old => ({ ...old, [p.name]: next }));
      if (p.kind === "choice") return <Select key={p.name} label={p.name} value={String(value)} options={p.choices.map(c => ({ value: c, label: c }))} onChange={change} disabled={busy} />;
      if (p.kind === "number") return <NumberField key={p.name} label={p.name} value={typeof value === "number" ? value : ""} onValueChange={change} disabled={busy} />;
      return <TextField key={p.name} label={p.name} value={String(value)} onValueChange={change} disabled={busy} />;
    })}
    <div className="mcp-actions">
      <Button size="sm" disabled={busy} onClick={() => void act(save)}>Save inputs</Button>
      <Button size="sm" variant="primary" disabled={busy} onClick={() => void act(async () => {
        await save(); store.requestGraphRunnerNodes([id]); overlays.open("graph-runner", "dialog");
      })}>Preview run</Button>
      <Button size="sm" disabled={busy} onClick={() => void act(async () => {
        await store.mutateWorkflowNode("unpack", { ...workflowPin(state.document), inputs: values(), parameters, x: x + 360, y });
      })}>Unpack editable nodes</Button>
    </div>
    {Object.entries(state.outputs).map(([name, output]) => <TextArea key={name} label={name + " · result"}
      value={output.kind === "text" ? output.text : JSON.stringify(output, null, 2)} readOnly />)}
    {state.childRunId && <p>Child run: <code>{state.childRunId}</code></p>}
    <Button size="sm" disabled={busy} onClick={() => void act(async () => {
      if (await confirm({ title: "Delete Workflow node?", body: "The library definition is not deleted.", confirmLabel: "Delete node", destructive: true })) store.removeNodes([id]);
    })}>Delete node</Button>
  </>;
}

export const WorkflowNodeView = memo(function WorkflowNodeView({ id, data, selected, positionAbsoluteX, positionAbsoluteY }: NodeProps<WorkflowFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const state = readWorkflowNode(data.raw);
  return <NodeShell kindClassName="mcp-tool-node" selected={!!selected} collapsed={data.isCollapsed || lodCollapsed}
    header={<><CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={() => data.store.setChatCollapsed(id, !data.isCollapsed)} />
      <NodeHeading>{data.title || "Workflow"}</NodeHeading></>}
    bodyClassName="mcp-tool-body nodrag nopan nowheel">
    {state ? <Editor key={state.document.spec.revision} id={id} state={state} store={data.store} x={positionAbsoluteX} y={positionAbsoluteY} />
      : <InlineAlert variant="error" title="Workflow unavailable" body="The pinned definition could not be read." />}
  </NodeShell>;
});
