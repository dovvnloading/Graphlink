/* eslint-disable jsx-a11y/no-noninteractive-tabindex -- each result is a scrollable, read-only region that must be keyboard focusable (WCAG 2.1.1). */
import { memo, useEffect, useState, useSyncExternalStore } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { useOverlays } from "../overlays/overlays";
import { Button } from "../ui/Button";
import { TextArea } from "../ui/TextArea";
import { TextField } from "../ui/TextField";
import { NumberField } from "../ui/NumberField";
import { Select } from "../ui/Select";
import { InlineAlert } from "../ui/InlineAlert";
import { useConfirm } from "../ui/ConfirmDialog";
import { Badge } from "../ui/Badge";
import { MenuButton, MenuItem } from "../ui/Menu";
import { NodeShell } from "./NodeShell";
import { NodeHeading } from "./NodeHeading";
import { CollapseToggleButton } from "./CollapseToggleButton";
import { useLodVisibility } from "./useLodVisibility";
import { availableTextSources, textSourceLabel } from "./textInputs";
import { WorkflowSpecView } from "../chrome/WorkflowSpecView";
import { stepName } from "../chrome/WorkflowPreviewSummary";
import type { SceneStore } from "./sceneStore";
import { readWorkflowNode, workflowPin, type WorkflowDocument, type WorkflowNodeState, type WorkflowVersion } from "./workflowLibrary";
import "./McpToolNodeView.css";

type WorkflowConfiguration = {
  inputs: Record<string, unknown>; parameters: Record<string, unknown>; sources: Record<string, unknown>;
};

export interface WorkflowNodeData extends Record<string, unknown> {
  title: string;
  raw: string;
  isCollapsed: boolean;
  store: SceneStore;
}
export type WorkflowFlowNode = Node<WorkflowNodeData, "workflow">;

function Editor({ id, state, store, x, y }: { id: string; state: WorkflowNodeState; store: SceneStore; x: number; y: number }) {
  const overlays = useOverlays();
  const askConfirm = useConfirm();
  const spec = state.document.spec;
  // The authoritative configuration and the form it seeds (#1154). A clean
  // form follows authoritative changes (undo/redo, another window); a dirty
  // one keeps the user's edits and shows a conflict instead.
  const authoritative: WorkflowConfiguration = {
    inputs: state.inputs, parameters: state.parameters, sources: state.sources ?? {},
  };
  const authoritativeKey = JSON.stringify(authoritative);
  const formOf = (config: WorkflowConfiguration) => ({
    inputs: Object.fromEntries(spec.inputs.map(p => [
      p.name, p.kind === "text" ? String(config.inputs[p.name] ?? "") : JSON.stringify(config.inputs[p.name] ?? null),
    ])) as Record<string, string>,
    parameters: config.parameters as Record<string, string | number>,
    sources: config.sources as Record<string, string>,
  });
  const [seed, setSeed] = useState(() => ({
    key: authoritativeKey, config: authoritative, form: JSON.stringify(formOf(authoritative)),
  }));
  const [inputs, setInputs] = useState<Record<string, string>>(() => formOf(authoritative).inputs);
  const [parameters, setParameters] = useState<Record<string, string | number>>(state.parameters);
  const [sources, setSources] = useState<Record<string, string>>(state.sources ?? {});
  const dirty = JSON.stringify({ inputs, parameters, sources }) !== seed.form;
  if (authoritativeKey !== seed.key && !dirty) {
    const fresh = formOf(authoritative);
    setInputs(fresh.inputs);
    setParameters(fresh.parameters);
    setSources(fresh.sources);
    setSeed({ key: authoritativeKey, config: authoritative, form: JSON.stringify(fresh) });
  }
  const configConflict = authoritativeKey !== seed.key && dirty;
  function reloadConfiguration() {
    const fresh = formOf(authoritative);
    setInputs(fresh.inputs);
    setParameters(fresh.parameters);
    setSources(fresh.sources);
    setSeed({ key: authoritativeKey, config: authoritative, form: JSON.stringify(fresh) });
  }
  function keepMyEdits() {
    // An explicit choice to replace the newer values: the baseline becomes
    // the current configuration, and the edits stay unsaved until Save.
    setSeed((current) => ({ key: authoritativeKey, config: authoritative, form: current.form }));
  }
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const sourceChoices = availableTextSources(scene.nodes, id);
  const [latest, setLatest] = useState<WorkflowVersion | null>(null);
  const [preview, setPreview] = useState<WorkflowDocument | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function checkVersion() {
    const history = await store.workflowLibrary<WorkflowVersion[]>("history", { workflow_id: spec.workflow_id });
    setLatest(history[0] && history[0].version > state.version ? history[0] : null);
  }
  useEffect(() => {
    let cancelled = false;
    void store.workflowLibrary<WorkflowVersion[]>("history", { workflow_id: spec.workflow_id })
      .then(history => {
        if (!cancelled) setLatest(history[0] && history[0].version > state.version ? history[0] : null);
      })
      .catch(e => {
        if (!cancelled) setError(String(e));
      });
    return () => { cancelled = true; };
  }, [store, spec.workflow_id, spec.revision, state.version]);
  async function act(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const values = () => Object.fromEntries(spec.inputs.filter(p => !sources[p.name]).map(p => [p.name, p.kind === "text" ? inputs[p.name] : JSON.parse(inputs[p.name])]));
  async function save() {
    if (configConflict) throw new Error("This node's inputs changed. Reload them or keep your edits first.");
    const saved = await store.mutateWorkflowNode("configure", { node_id: id, expected_revision: spec.revision,
      expected_configuration: seed.config, inputs: values(), parameters,
      sources: Object.fromEntries(Object.entries(sources).filter(([, source]) => !!source)) });
    if (!saved) throw new Error("Another graph operation is in progress. Try again.");
  }
  const outputs = Object.entries(state.outputs);
  const label = (name: string, kind?: string) => stepName(name) + (kind && kind !== "text" ? " (" + kind + ")" : "");
  return <div className="workflow-node">
    <div className="workflow-node-summary">
      <Badge>Version {state.version}</Badge>
      <WorkflowSpecView value={state.document} />
    </div>
    {error && <InlineAlert variant="error" title="This workflow node failed" body={error} />}
    {configConflict && <div className="workflow-node-conflict">
      <InlineAlert variant="warning" title="Inputs changed"
        body="This node's inputs changed while you were editing, for example by undo or in another window. Your edits are kept; reload to see the current values, or keep your edits to save over them." />
      <div className="workflow-actions">
        <Button size="sm" variant="secondary" onClick={reloadConfiguration}>Reload inputs</Button>
        <Button size="sm" variant="ghost" onClick={keepMyEdits}>Keep my edits</Button>
      </div>
    </div>}
    {latest && !preview && <InlineAlert title={"Version " + latest.version + " is available"}
      body={"This node stays on version " + state.version + " until you upgrade it."}
      action={{ label: "Review upgrade", onClick: () => void act(async () => {
        setPreview(await store.workflowLibrary<WorkflowDocument>("get", { workflow_id: spec.workflow_id, revision: latest.revision }));
      }) }} />}
    {preview && latest && <section className="workflow-node-upgrade" aria-label="Upgrade preview">
      <h4>Upgrade to version {latest.version}</h4>
      <p className="workflow-muted">Upgrading clears the inputs, settings and results on this node. Review the new definition first.</p>
      <WorkflowSpecView value={preview} />
      <div className="workflow-actions">
        <Button size="sm" variant="primary" disabled={busy} onClick={() => void act(async () => {
          if (!await askConfirm({ title: "Upgrade this workflow node?", body: "It will use version " + latest.version + ". Its inputs and results are cleared.", confirmLabel: "Upgrade" })) return;
          await store.mutateWorkflowNode("upgrade", { node_id: id, ...workflowPin(preview), expected_revision: spec.revision });
          setPreview(null);
        })}>Upgrade to version {latest.version}</Button>
        <Button size="sm" variant="ghost" onClick={() => setPreview(null)}>Cancel</Button>
      </div>
    </section>}
    {(spec.inputs.length > 0 || state.document.parameters.length > 0) && <div className="workflow-node-inputs">
      {spec.inputs.map(port => <div key={port.name} className="workflow-node-input">
        {port.kind === "text" && <Select label={label(port.name) + " comes from"} value={sources[port.name] ?? ""}
          options={[{ value: "", label: "Text typed here" }, ...sourceChoices.map(node => ({ value: node.id, label: textSourceLabel(node) })),
            ...(sources[port.name] && !sourceChoices.some(n => n.id === sources[port.name]) ? [{ value: sources[port.name], label: "Missing source", disabled: true }] : [])]}
          onChange={value => setSources(old => ({ ...old, [port.name]: value }))} disabled={busy} />}
        {!sources[port.name] && <TextArea label={port.kind === "text" ? label(port.name) : label(port.name, port.kind)} minRows={2} maxRows={8}
          value={inputs[port.name] ?? ""} disabled={busy} onValueChange={value => setInputs(old => ({ ...old, [port.name]: value }))} />}
      </div>)}
      {state.document.parameters.map(p => {
        const step = spec.steps.find(s => s.step_id === p.step_id);
        const defaultValue = step ? JSON.parse(step.config_json)[p.setting] : "";
        const value = parameters[p.name] ?? defaultValue;
        const change = (next: string | number) => setParameters(old => ({ ...old, [p.name]: next }));
        if (p.kind === "choice") return <Select key={p.name} label={label(p.name)} value={String(value)} options={p.choices.map(c => ({ value: c, label: c }))} onChange={change} disabled={busy} />;
        if (p.kind === "number") return <NumberField key={p.name} label={label(p.name)} value={typeof value === "number" ? value : ""} onValueChange={change} disabled={busy} />;
        return <TextField key={p.name} label={label(p.name)} value={String(value)} onValueChange={change} disabled={busy} />;
      })}
    </div>}
    <div className="workflow-actions">
      <Button size="sm" variant="primary" iconStart="play" disabled={busy || configConflict} onClick={() => void act(async () => {
        await save(); store.requestGraphRunnerNodes([id]); overlays.open("graph-runner", "dialog");
      })}>Preview run</Button>
      <Button size="sm" variant="secondary" disabled={busy || configConflict} onClick={() => void act(save)}>Save inputs</Button>
      <MenuButton label="More" menuLabel="More actions for this workflow node" disabled={busy}>
        <MenuItem label="Check for a newer version" icon="refresh" onSelect={() => void act(checkVersion)} />
        <MenuItem label="Unpack as editable nodes" icon="layers" onSelect={() => void act(async () => {
          await store.mutateWorkflowNode("unpack", { ...workflowPin(state.document), inputs: values(), parameters,
            sources: Object.fromEntries(Object.entries(sources).filter(([, source]) => !!source)), x: x + 360, y });
        })} />
        <MenuItem label="Delete node…" icon="trash" destructive onSelect={() => void act(async () => {
          if (await askConfirm({ title: "Delete this workflow node?", body: "The workflow stays in the library.", confirmLabel: "Delete node", destructive: true })) store.removeNodes([id]);
        })} />
      </MenuButton>
    </div>
    {outputs.length > 0 && <section className="workflow-node-results" aria-label="Results">
      <h4>Results</h4>
      {outputs.map(([name, output]) => <div key={name} className="workflow-value">
        <span className="workflow-value-name">{stepName(name)}</span>
        <pre className="workflow-pre" tabIndex={0} role="region" aria-label={stepName(name) + " result"}>
          {output.kind === "text" ? output.text : JSON.stringify(output.kind === "json" ? output.value : output, null, 2)}
        </pre>
      </div>)}
    </section>}
  </div>;
}

export const WorkflowNodeView = memo(function WorkflowNodeView({ id, data, selected, positionAbsoluteX, positionAbsoluteY }: NodeProps<WorkflowFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const state = readWorkflowNode(data.raw);
  return <NodeShell kindClassName="mcp-tool-node" selected={!!selected} collapsed={data.isCollapsed || lodCollapsed}
    textOutput={state?.document.spec.outputs.filter(o => o.port.kind === "text").length === 1}
    header={<><CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={() => data.store.setChatCollapsed(id, !data.isCollapsed)} />
      <NodeHeading>{data.title || "Workflow"}</NodeHeading></>}
    bodyClassName="mcp-tool-body nodrag nopan nowheel">
    {state ? <Editor key={state.document.spec.revision} id={id} state={state} store={data.store} x={positionAbsoluteX} y={positionAbsoluteY} />
      : <InlineAlert variant="error" title="Workflow unavailable" body="The pinned definition could not be read." />}
  </NodeShell>;
});
