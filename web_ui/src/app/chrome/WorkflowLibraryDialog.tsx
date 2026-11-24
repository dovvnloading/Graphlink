import { useEffect, useState } from "react";
import { useReactFlow } from "@xyflow/react";
import type { SceneStore } from "../canvas/sceneStore";
import type { WorkflowDocument, WorkflowEntry, WorkflowParameter, WorkflowStarter, WorkflowVersion } from "../canvas/workflowLibrary";
import { workflowPin } from "../canvas/workflowLibrary";
import { downloadTextFile } from "../canvas/downloadTextFile";
import { Dialog } from "../overlays/overlays";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { TextField } from "../ui/TextField";
import { Select } from "../ui/Select";
import { InlineAlert } from "../ui/InlineAlert";
import { useConfirm } from "../ui/ConfirmDialog";
import { WorkflowSpecView } from "./WorkflowSpecView";
import { DetachedWorkflowInspector } from "./DetachedWorkflowInspector";
import "./GraphRunnerDialog.css";

function Library({ store }: { store: SceneStore }) {
  const rf = useReactFlow();
  const askConfirm = useConfirm();
  const [rows, setRows] = useState<WorkflowEntry[]>([]);
  const [starters, setStarters] = useState<WorkflowStarter[]>([]);
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [entry, setEntry] = useState<WorkflowEntry | null>(null);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [view, setView] = useState<WorkflowDocument | null>(null);
  const [capture, setCapture] = useState<{ ids: string[]; doc: WorkflowDocument } | null>(null);
  const [outputs, setOutputs] = useState<string[]>([]);
  const [params, setParams] = useState<WorkflowParameter[]>([]);
  const [append, setAppend] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function refresh() {
    const entries = await store.workflowLibrary<WorkflowEntry[]>("list");
    setRows(entries);
    return entries;
  }
  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      store.workflowLibrary<WorkflowEntry[]>("list"),
      store.workflowLibrary<WorkflowStarter[]>("starters"),
    ])
      .then(([entries, catalog]) => {
        if (!cancelled) { setRows(entries); setStarters(catalog); }
      })
      .catch(e => {
        if (!cancelled) setError(String(e));
      });
    return () => { cancelled = true; };
  }, [store]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function open(row: WorkflowEntry, revision = row.revision) {
    setEntry(row);
    setName(row.name);
    setVersions(await store.workflowLibrary<WorkflowVersion[]>("history", { workflow_id: row.workflow_id }));
    setView(await store.workflowLibrary<WorkflowDocument>("get", { workflow_id: row.workflow_id, revision }));
  }
  function make() {
    void act(async () => {
      const ids = rf.getNodes().filter(n => n.selected).map(n => n.id).sort();
      const doc = await store.workflowLibrary<WorkflowDocument>("preview", { ids });
      setCapture({ ids, doc });
      setOutputs(doc.spec.outputs.map(o => o.port.name));
      setParams([]);
      setName("");
      setAppend("");
    });
  }
  function importStarter(starter: WorkflowStarter) {
    void act(async () => {
      const pin = await store.workflowLibrary<{ workflow_id: string; revision: string }>(
        "importStarter", { starter_id: starter.id },
      );
      const entries = await refresh();
      const row = entries.find(item => item.workflow_id === pin.workflow_id);
      if (row) await open(row, pin.revision);
    });
  }
  function importFile() {
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = ".graphlink-workflow";
    fileInput.onchange = () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      void act(async () => {
        if (file.size > 1_000_000) throw new Error("Workflow files must be at most 1 MB.");
        await store.workflowLibrary("import", { text: await file.text() });
        await refresh();
      });
    };
    fileInput.click();
  }
  const position = () => rf.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
  return <>
    <p className="runner-intro">Workflows are global, immutable versioned definitions. Capture connected text operations, import a built-in starter, or import a portable workflow file. Canvas nodes keep their pinned versions even after a library entry is deleted.</p>
    {error && <InlineAlert variant="error" title="Workflow Library" body={error} />}
    <div className="runner-commit">
      <Button variant="primary" disabled={busy} onClick={make}>Make workflow from selection</Button>
      <Button disabled={busy} onClick={importFile}>Import .graphlink-workflow</Button>
      <Button disabled={busy} onClick={() => void act(refresh)}>Refresh</Button>
    </div>
    {capture && <section>
      <h3>Make workflow</h3>
      <TextField label="Workflow name" value={name} onValueChange={setName} disabled={busy} />
      <Select label="Save as" value={append} onChange={value => {
        setAppend(value); setName(rows.find(row => row.workflow_id === value)?.name ?? "");
      }} options={[{ value: "", label: "New workflow" }, ...rows.map(row => ({ value: row.workflow_id, label: row.name + " · next version" }))]} />
      <p>Unbound inputs: {capture.doc.spec.inputs.map(p => p.name + " (" + p.kind + ")").join(", ") || "None"}</p>
      <h4>Expose outputs</h4>
      {capture.doc.spec.outputs.map(o => <Checkbox key={o.port.name} label={o.port.name + " from " + (o.source.kind === "step" ? o.source.step_id : o.source.port)}
        checked={outputs.includes(o.port.name)} onChange={event => { const checked = event.currentTarget.checked; setOutputs(old => checked ? [...old, o.port.name] : old.filter(n => n !== o.port.name)); }} />)}
      <h4>Promote settings to parameters</h4>
      {capture.doc.spec.steps.map(step => {
        const parameterName = step.step_id + "_operation";
        return <Checkbox key={parameterName} label={step.step_id + " · operation (choice)"}
          checked={params.some(p => p.name === parameterName)} onChange={event => {
            const checked = event.currentTarget.checked;
            setParams(old => checked ? [...old, { name: parameterName, step_id: step.step_id, setting: "operation", kind: "choice", choices: ["copy", "word_count"] }] : old.filter(p => p.name !== parameterName));
          }} />;
      })}
      <WorkflowSpecView value={capture.doc} />
      <div className="runner-commit">
        <Button variant="primary" disabled={busy || !name.trim() || !outputs.length} onClick={() => void act(async () => {
          const previous = rows.find(row => row.workflow_id === append);
          await store.workflowLibrary("make", { ids: capture.ids, name, outputs, parameters: params, expected_spec: capture.doc.spec,
            ...(previous ? { workflow_id: previous.workflow_id, expected_revision: previous.revision } : {}) });
          setCapture(null); setName(""); await refresh();
        })}>Save workflow version</Button>
        <Button onClick={() => setCapture(null)}>Cancel</Button>
      </div>
    </section>}
    <section aria-label="Built-in starter workflows">
      <h3>Built-in starters</h3>
      <p>Import an independent copy with sample inputs. Model steps use local Ollama and require a reviewed run.</p>
      <ul className="runner-steps">
        {starters.map(starter => <li key={starter.id}>
          <div className="runner-step-heading"><strong>{starter.name}</strong></div>
          <p>{starter.description}</p>
          <Button size="sm" disabled={busy || !!capture} onClick={() => importStarter(starter)}>Import {starter.name}</Button>
        </li>)}
      </ul>
    </section>
    <TextField label="Search workflows" type="search" value={query} onValueChange={setQuery} />
    <ul className="runner-steps">
      {rows.filter(row => row.name.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map(row => <li key={row.workflow_id}>
        <div className="runner-step-heading"><strong>{row.name}</strong><span>Version {row.version}</span></div>
        <Button size="sm" disabled={busy || !!capture} onClick={() => void act(() => open(row))}>Open / version history</Button>
      </li>)}
    </ul>
    {!rows.length && <p>No workflows saved yet.</p>}
    {entry && view && !capture && <section>
      <h3>{entry.name}</h3>
      <Select label="Version history" value={view.spec.revision} disabled={busy}
        options={versions.map(v => ({ value: v.revision, label: "Version " + v.version + " · " + new Date(v.created_at).toLocaleDateString() }))}
        onChange={revision => void act(() => open(entry, revision))} />
      <WorkflowSpecView value={view} />
      <TextField label="Library name / duplicate name" value={name} onValueChange={setName} />
      <div className="runner-commit">
        <Button disabled={busy || !name.trim()} onClick={() => void act(async () => {
          await store.workflowLibrary("rename", { workflow_id: entry.workflow_id, name });
          setEntry({ ...entry, name }); await refresh();
        })}>Rename</Button>
        <Button disabled={busy || !name.trim()} onClick={() => void act(async () => {
          await store.workflowLibrary("duplicate", { ...workflowPin(view), name });
          await refresh();
        })}>Duplicate version</Button>
        <Button disabled={busy} onClick={() => void act(async () => {
          if (!await askConfirm({ title: "Delete workflow?", body: "Existing pinned nodes keep their versions. The entry will disappear from the library.", confirmLabel: "Delete", destructive: true })) return;
          await store.workflowLibrary("delete", { workflow_id: entry.workflow_id });
          setEntry(null); setView(null); await refresh();
        })}>Delete</Button>
      </div>
      {view.format === "graphlink-starter-workflow" && <>
        <p>Review or edit the synthetic example inputs and model IDs in the request before running.
          Install the pinned local models or select installed IDs; the endpoint must match your loopback Ollama configuration.</p>
        <DetachedWorkflowInspector key={view.spec.revision}
          initialSource={JSON.stringify({ spec: view.spec, parameters: view.example_inputs }, null, 2)} />
      </>}
      <div className="runner-commit">
        {view.format === "graphlink-workflow" && <>
          <Button variant="primary" disabled={busy} onClick={() => void act(async () => {
            await store.mutateWorkflowNode("insert", { ...workflowPin(view), ...position() });
          })}>Add pinned Workflow node</Button>
          <Button disabled={busy} onClick={() => void act(async () => {
            await store.mutateWorkflowNode("unpack", { ...workflowPin(view), ...position() });
          })}>Unpack editable nodes</Button>
        </>}
        <Button disabled={busy} onClick={() => void act(async () => {
          const contents = await store.workflowLibrary<string>("export", workflowPin(view));
          downloadTextFile(contents, entry.name.replace(/[^a-zA-Z0-9_-]/g, "_") + ".graphlink-workflow");
        })}>Export version</Button>
      </div>
    </section>}
  </>;
}

export function WorkflowLibraryDialog({ store }: { store: SceneStore }) {
  return <Dialog name="workflow-library" title="Workflow Library" size="lg" className="graph-runner-dialog">
    <Library store={store} />
  </Dialog>;
}
