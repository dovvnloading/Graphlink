import { useCallback, useEffect, useRef, useState } from "react";
import { useReactFlow, useStore } from "@xyflow/react";
import type { SceneStore } from "../canvas/sceneStore";
import type { WorkflowDocument, WorkflowEntry, WorkflowParameter, WorkflowStarter, WorkflowVersion } from "../canvas/workflowLibrary";
import { workflowPin } from "../canvas/workflowLibrary";
import { downloadTextFile } from "../canvas/downloadTextFile";
import { Dialog } from "../overlays/overlays";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { useConfirm } from "../ui/ConfirmDialog";
import { EmptyState } from "../ui/EmptyState";
import { FieldGroup } from "../ui/Field";
import { Icon } from "../ui/icons/Icon";
import { InlineAlert } from "../ui/InlineAlert";
import { MenuButton, MenuItem } from "../ui/Menu";
import { Select } from "../ui/Select";
import { Tabs } from "../ui/Tabs";
import { Toaster } from "../ui/Toast";
import { TextField } from "../ui/TextField";
import { subscribeApprovalTarget, takeApprovalTarget } from "./WorkflowApprovalNotifications";
import { WorkflowPresetsPanel, type PresetStart } from "./WorkflowPresetsPanel";
import { stepName } from "./WorkflowPreviewSummary";
import { WorkflowDefinition } from "./WorkflowSpecView";
import { WorkflowRunLauncher } from "./WorkflowRunLauncher";
import { WorkflowRunsPanel } from "./WorkflowRunsPanel";
import {
  errorText, plural, runActive, runWaiting, workflowRequest, workspacePath, WorkflowRequestError,
  type DetachedRun, type PresetSummary, type RunSummary, type Workspace,
} from "./workflowApi";
import "./workflow-library.css";

type View =
  | { kind: "list" }
  | { kind: "detail"; entry: WorkflowEntry; doc: WorkflowDocument; versions: WorkflowVersion[] }
  | { kind: "capture"; ids: string[]; doc: WorkflowDocument };
type Tab = "workflows" | "runs" | "presets";

function savedOn(value: string) {
  const date = Date.parse(value);
  return Number.isFinite(date) ? new Date(date).toLocaleDateString(undefined, { dateStyle: "medium" }) : "an unknown date";
}

function Library({ store }: { store: SceneStore }) {
  const rf = useReactFlow();
  const selectedCount = useStore(state => state.nodes.reduce((count, node) => count + (node.selected ? 1 : 0), 0));
  const askConfirm = useConfirm();
  const [tab, setTab] = useState<Tab>("workflows");
  const [rows, setRows] = useState<WorkflowEntry[]>([]);
  const [starters, setStarters] = useState<WorkflowStarter[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>({ kind: "list" });
  const [editing, setEditing] = useState<null | "rename" | "duplicate">(null);
  const [nameDraft, setNameDraft] = useState("");
  const [capture, setCapture] = useState({ name: "", appendTo: "", outputs: [] as string[], params: [] as WorkflowParameter[] });
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState<number | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [presets, setPresets] = useState<PresetSummary[]>([]);
  const [runsLoaded, setRunsLoaded] = useState(false);
  const [presetsLoaded, setPresetsLoaded] = useState(false);
  const [selectedRun, setSelectedRun] = useState("");
  const [runDetail, setRunDetail] = useState<DetachedRun | null>(null);
  const [detailLoaded, setDetailLoaded] = useState(false);
  const [presetStart, setPresetStart] = useState<PresetStart | null>(null);
  const [runPoke, setRunPoke] = useState(0);
  const [presetPoke, setPresetPoke] = useState(0);
  const navigation = useRef(0);
  const [navigationId, setNavigationId] = useState(0);
  const [runsViewKey, setRunsViewKey] = useState(0);
  const [fromNotification, setFromNotification] = useState(false);

  // Invalidate both in-flight workspace reads and callbacks from the old form
  // immediately, before React commits the new workspace or unmounts that form.
  const navigate = useCallback((nextWorkspace: number, runId: string, notification = false) => {
    setNavigationId(++navigation.current);
    if (notification) setRunsViewKey(n => n + 1);
    setRuns([]); setPresets([]); setRunsLoaded(false); setPresetsLoaded(false);
    setRunDetail(null); setDetailLoaded(false);
    setError(""); setPresetStart(null);
    setWorkspace(nextWorkspace); setSelectedRun(runId); setFromNotification(notification);
  }, []);
  const heading = useRef<HTMLHeadingElement>(null);
  const focusHeading = useRef(false);

  const refreshRows = useCallback(async () => {
    const entries = await store.workflowLibrary<WorkflowEntry[]>("list");
    setRows(entries);
    return entries;
  }, [store]);
  useEffect(() => {
    let live = true;
    Promise.all([store.workflowLibrary<WorkflowEntry[]>("list"), store.workflowLibrary<WorkflowStarter[]>("starters")])
      .then(([entries, catalog]) => { if (live) { setRows(entries); setStarters(catalog); } })
      .catch(err => { if (live) setError(errorText(err)); })
      .finally(() => { if (live) setLoading(false); });
    workflowRequest<Workspace[]>("/api/workflows/workspaces")
      .then(list => { if (live) { setWorkspaces(list); setWorkspace(current => current ?? list[0]?.id ?? null); } })
      .catch(err => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [store]);
  // Tab badges need counts, not receipts. Refresh immediately on navigation
  // and mutations, then use a slower cadence off the Runs tab or when idle.
  // The independent active-approvals notification poll remains responsive.
  useEffect(() => {
    if (workspace === null) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const live = () => !controller.signal.aborted && navigation.current === navigationId;
    async function load() {
      let delay = 15000;
      try {
        const next = await workflowRequest<RunSummary[]>(workspacePath(workspace!) + "/runs?summary=true", { signal: controller.signal });
        if (!live()) return;
        if (!Array.isArray(next)) throw new Error("The workflow service returned an invalid run list.");
        setRuns(next);
        setRunsLoaded(true);
        if (tab === "runs" && next.some(runActive)) delay = 2000;
      } catch (err) {
        if (live()) setError(errorText(err));
      } finally {
        if (live()) timer = window.setTimeout(() => void load(), delay);
      }
    }
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [workspace, runPoke, navigationId, tab]);
  // Presets change infrequently. Update their badge on tab/workspace entry,
  // and only keep polling while the Presets tab itself is visible.
  useEffect(() => {
    if (workspace === null) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const live = () => !controller.signal.aborted && navigation.current === navigationId;
    async function load() {
      try {
        const next = await workflowRequest<PresetSummary[]>(workspacePath(workspace!) + "/presets?summary=true", { signal: controller.signal });
        if (!live()) return;
        if (!Array.isArray(next)) throw new Error("The workflow service returned an invalid preset list.");
        setPresets(next);
        setPresetsLoaded(true);
      } catch (err) {
        if (live()) setError(errorText(err));
      } finally {
        if (live() && tab === "presets") timer = window.setTimeout(() => void load(), 15000);
      }
    }
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [workspace, presetPoke, navigationId, tab]);
  // Only the open receipt needs full inputs, outputs, preview and approval
  // evidence. Stop polling a terminal receipt, and abort on selection change.
  useEffect(() => {
    if (workspace === null || tab !== "runs" || !selectedRun) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const live = () => !controller.signal.aborted && navigation.current === navigationId;
    async function load() {
      let retry = false;
      try {
        const next = await workflowRequest<DetachedRun>(workspacePath(workspace!) + "/runs/" + encodeURIComponent(selectedRun), { signal: controller.signal });
        if (!live()) return;
        if (next?.run_id !== selectedRun) throw new Error("The workflow service returned an unexpected run.");
        setRunDetail(next);
        setDetailLoaded(true);
        retry = runActive(next);
      } catch (err) {
        if (!live()) return;
        setRunDetail(null);
        const missing = err instanceof WorkflowRequestError && err.status === 404;
        setDetailLoaded(missing);
        if (!missing) { setError(errorText(err)); retry = true; }
      } finally {
        if (live() && retry) timer = window.setTimeout(() => void load(), 2000);
      }
    }
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [workspace, selectedRun, runPoke, navigationId, tab]);
  // A "waiting for approval" notification opens this dialog on that run.
  useEffect(() => {
    function follow() {
      const target = takeApprovalTarget();
      if (!target) return;
      navigate(target.workspaceId, target.runId, true);
      setTab("runs");
    }
    follow();
    return subscribeApprovalTarget(follow);
  }, [navigate]);
  useEffect(() => {
    if (focusHeading.current) { focusHeading.current = false; heading.current?.focus(); }
  }, [view]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  function go(next: View) { focusHeading.current = true; setEditing(null); setView(next); }
  async function open(entry: WorkflowEntry, revision = entry.revision) {
    const [versions, doc] = await Promise.all([
      store.workflowLibrary<WorkflowVersion[]>("history", { workflow_id: entry.workflow_id }),
      store.workflowLibrary<WorkflowDocument>("get", { workflow_id: entry.workflow_id, revision }),
    ]);
    if (view.kind === "detail" && view.entry.workflow_id === entry.workflow_id) setView({ kind: "detail", entry, doc, versions });
    else go({ kind: "detail", entry, doc, versions });
  }
  function importStarter(starter: WorkflowStarter) {
    void act(async () => {
      const pin = await store.workflowLibrary<{ workflow_id: string; revision: string }>("importStarter", { starter_id: starter.id });
      const entry = (await refreshRows()).find(row => row.workflow_id === pin.workflow_id);
      if (entry) await open(entry, pin.revision);
    });
  }
  function importFile() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".graphlink-workflow";
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) return;
      void act(async () => {
        if (file.size > 1_000_000) throw new Error("That file is over 1 MB. Workflow files must be at most 1 MB.");
        await store.workflowLibrary("import", { text: await file.text() });
        await refreshRows();
      });
    };
    input.click();
  }
  function startCapture() {
    void act(async () => {
      const ids = rf.getNodes().filter(node => node.selected).map(node => node.id).sort();
      const doc = await store.workflowLibrary<WorkflowDocument>("preview", { ids });
      setCapture({ name: "", appendTo: "", outputs: doc.spec.outputs.map(output => output.port.name), params: [] });
      go({ kind: "capture", ids, doc });
    });
  }
  const center = () => rf.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
  const showRun = (runId: string) => {
    // A previously submitted request may finish after a notification was
    // followed. It must not take the user away from the run they chose.
    if (navigation.current !== navigationId || workspace === null) return;
    navigate(workspace, runId);
    setTab("runs");
  };
  const selectRun = (runId: string) => {
    if (navigation.current !== navigationId) return;
    if (runId && !runs.some(run => run.run_id === runId)) { showRun(runId); return; }
    setRunDetail(null); setDetailLoaded(false);
    setSelectedRun(runId); setFromNotification(false);
  };
  const workspaceControl = workspaces.length > 1
    ? <Select ariaLabel="Workspace" value={workspace === null ? "" : String(workspace)} className="workflow-workspace"
        onChange={value => navigate(Number(value), "")}
        options={workspaces.map(item => ({ value: String(item.id), label: item.name }))} />
    : null;
  const waiting = runs.filter(runWaiting).length;
  const filtered = rows.filter(row => row.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));

  let workflowsPanel;
  if (view.kind === "capture") {
    const { doc } = view;
    const toggle = (list: string[], item: string, on: boolean) => on ? [...list, item] : list.filter(value => value !== item);
    workflowsPanel = <div className="workflow-panel">
      <Button variant="ghost" iconStart="chevron-left" className="workflow-back" onClick={() => go({ kind: "list" })}>All workflows</Button>
      <h3 ref={heading} tabIndex={-1}>Make a workflow from {plural(view.ids.length, "selected node")}</h3>
      <p className="workflow-muted">Unconnected inputs become the workflow's inputs. Choose what it returns and which settings stay adjustable.</p>
      <div className="workflow-form">
        <div className="workflow-form-grid">
          <TextField label="Workflow name" value={capture.name} disabled={busy} onValueChange={name => setCapture(c => ({ ...c, name }))} />
          <Select label="Save as" value={capture.appendTo} disabled={busy}
            onChange={appendTo => setCapture(c => ({ ...c, appendTo, name: rows.find(row => row.workflow_id === appendTo)?.name ?? c.name }))}
            options={[{ value: "", label: "A new workflow" }, ...rows.map(row => ({ value: row.workflow_id, label: "Next version of " + row.name }))]} />
        </div>
        <FieldGroup title="What it returns" description="Pick at least one output.">
          {doc.spec.outputs.map(output => <Checkbox key={output.port.name} disabled={busy}
            label={stepName(output.port.name)}
            description={"From " + (output.source.kind === "step" ? stepName(output.source.step_id) : "input " + stepName(output.source.port))}
            checked={capture.outputs.includes(output.port.name)}
            onChange={event => { const on = event.currentTarget.checked; setCapture(c => ({ ...c, outputs: toggle(c.outputs, output.port.name, on) })); }} />)}
        </FieldGroup>
        <FieldGroup title="Settings to keep adjustable" description="Each becomes a choice you can change per run or preset.">
          {doc.spec.steps.map(step => {
            const name = step.step_id + "_operation";
            return <Checkbox key={name} disabled={busy} label={stepName(step.step_id) + " operation"}
              description="Copy the text or count its words"
              checked={capture.params.some(param => param.name === name)}
              onChange={event => {
                const on = event.currentTarget.checked;
                setCapture(c => ({ ...c, params: on
                  ? [...c.params, { name, step_id: step.step_id, setting: "operation", kind: "choice", choices: ["copy", "word_count"] }]
                  : c.params.filter(param => param.name !== name) }));
              }} />;
          })}
        </FieldGroup>
        <WorkflowDefinition doc={doc} />
        <div className="workflow-actions">
          <Button variant="primary" loading={busy} disabled={!capture.name.trim() || !capture.outputs.length}
            disabledReason={!capture.name.trim() ? "Name the workflow first." : !capture.outputs.length ? "Pick at least one output." : undefined}
            onClick={() => void act(async () => {
              const previous = rows.find(row => row.workflow_id === capture.appendTo);
              await store.workflowLibrary("make", { ids: view.ids, name: capture.name.trim(), outputs: capture.outputs,
                parameters: capture.params, expected_spec: doc.spec,
                ...(previous ? { workflow_id: previous.workflow_id, expected_revision: previous.revision } : {}) });
              await refreshRows();
              go({ kind: "list" });
            })}>Save workflow</Button>
          <Button variant="ghost" disabled={busy} onClick={() => go({ kind: "list" })}>Cancel</Button>
        </div>
      </div>
    </div>;
  } else if (view.kind === "detail") {
    const { entry, doc, versions } = view;
    const starter = doc.format === "graphlink-starter-workflow";
    const selectedVersion = versions.find(version => version.revision === doc.spec.revision)
      ?? (entry.revision === doc.spec.revision ? entry : undefined);
    workflowsPanel = <div className="workflow-panel">
      <Button variant="ghost" iconStart="chevron-left" className="workflow-back" onClick={() => go({ kind: "list" })}>All workflows</Button>
      <header className="workflow-detail-header">
        <div className="workflow-detail-title">
          <h3 ref={heading} tabIndex={-1}>{entry.name}</h3>
          {starter && <Badge>Starter</Badge>}
        </div>
        <div className="workflow-actions">
          <Select label="Version history" value={doc.spec.revision} disabled={busy} className="workflow-version"
            options={versions.map(version => ({ value: version.revision, label: "Version " + version.version + " · " + savedOn(version.created_at) }))}
            onChange={revision => void act(() => open(entry, revision))} />
          {!starter && <Button variant="primary" iconStart="plus" disabled={busy}
            onClick={() => void act(() => store.mutateWorkflowNode("insert", { ...workflowPin(doc), ...center() }))}>Add to canvas</Button>}
          <MenuButton label="More" menuLabel={"More actions for " + entry.name} disabled={busy}>
            {!starter && <MenuItem label="Unpack as editable nodes" icon="layers"
              onSelect={() => void act(() => store.mutateWorkflowNode("unpack", { ...workflowPin(doc), ...center() }))} />}
            <MenuItem label="Save as a preset…" icon="clock" onSelect={() => {
              setPresetStart({ workflowId: entry.workflow_id, revision: doc.spec.revision, version: selectedVersion?.version ?? null });
              setTab("presets");
            }} />
            <MenuItem label="Rename…" icon="edit" onSelect={() => { setNameDraft(entry.name); setEditing("rename"); }} />
            <MenuItem label="Duplicate…" icon="copy" onSelect={() => { setNameDraft(entry.name + " copy"); setEditing("duplicate"); }} />
            <MenuItem label="Export file" icon="download" onSelect={() => void act(async () => {
              const contents = await store.workflowLibrary<string>("export", workflowPin(doc));
              downloadTextFile(contents, entry.name.replace(/[^a-zA-Z0-9_-]/g, "_") + ".graphlink-workflow");
            })} />
            <MenuItem label="Delete…" icon="trash" destructive onSelect={() => void act(async () => {
              if (!await askConfirm({ title: "Delete " + entry.name + "?", body: "Workflow nodes already on a canvas keep their pinned version.", confirmLabel: "Delete", destructive: true })) return;
              await store.workflowLibrary("delete", { workflow_id: entry.workflow_id });
              await refreshRows();
              go({ kind: "list" });
            })} />
          </MenuButton>
        </div>
      </header>
      {editing && <div className="workflow-inline-editor">
        <TextField label={editing === "rename" ? "New name" : "Name for the copy"} value={nameDraft} disabled={busy} onValueChange={setNameDraft} />
        <Button variant="primary" disabled={busy || !nameDraft.trim()} onClick={() => void act(async () => {
          if (editing === "rename") {
            await store.workflowLibrary("rename", { workflow_id: entry.workflow_id, name: nameDraft.trim() });
            setView({ ...view, entry: { ...entry, name: nameDraft.trim() } });
          } else {
            await store.workflowLibrary("duplicate", { ...workflowPin(doc), name: nameDraft.trim() });
          }
          setEditing(null);
          await refreshRows();
        })}>{editing === "rename" ? "Rename" : "Duplicate"}</Button>
        <Button variant="ghost" disabled={busy} onClick={() => setEditing(null)}>Cancel</Button>
      </div>}
      <p className="workflow-detail-meta">
        {plural(doc.spec.steps.length, "step")} · {plural(doc.spec.inputs.length, "input")} · {plural(doc.spec.outputs.length, "output")} · {selectedVersion
          ? <>version {selectedVersion.version} · saved {savedOn(selectedVersion.created_at)}</>
          : "version information unavailable"}
      </p>
      <div className="workflow-detail-body">
        <WorkflowDefinition doc={doc} />
        <section className="workflow-card workflow-run-card" aria-label="Run this workflow">
          <Select label="Target workspace" value={workspace === null ? "" : String(workspace)}
            options={workspaces.map(item => ({ value: String(item.id), label: item.name }))}
            disabled={!workspaces.length}
            onChange={value => navigate(Number(value), "")} />
          <p className="workflow-muted">Runs are stored in the selected library workspace, which may differ from the current canvas.</p>
          <WorkflowRunLauncher key={doc.spec.revision} draftKey={"run:" + doc.spec.revision} title="Run it"
            spec={doc.spec} workspace={workspace}
            note={starter ? <p className="workflow-muted">Filled with a sample. Model steps use the local Ollama model named in each step.</p>
              : doc.parameters.length > 0 ? <p className="workflow-muted">Runs use the saved settings of this version. Change them in a preset.</p> : null}
            examples={starter ? doc.example_inputs : undefined} onStarted={showRun} />
        </section>
      </div>
    </div>;
  } else {
    workflowsPanel = <div className="workflow-panel">
      <div className="workflow-toolbar">
        <TextField label="Search workflows" type="search" size="sm" prefixIcon="search" suffix={{ type: "clear" }}
          placeholder="Search workflows…" value={query} onValueChange={setQuery} autoComplete="off" />
        <span className="workflow-toolbar-end">
          <Button variant="secondary" iconStart="plus" disabled={busy || loading || !selectedCount}
            disabledReason={!selectedCount ? "Select connected nodes on the canvas first." : undefined} onClick={startCapture}>
            Make from selection</Button>
          <Button variant="secondary" iconStart="upload" disabled={busy || loading} onClick={importFile}>Import file</Button>
        </span>
      </div>
      {!loading && rows.length > 0 && <section className="workflow-group" aria-label="Your workflows">
        <h3 className="workflow-overline">Your workflows</h3>
        {filtered.length
          ? <ul className="workflow-list">{filtered.map(row => <li key={row.workflow_id} className="workflow-row">
              <button type="button" className="workflow-row-main" disabled={busy} onClick={() => void act(() => open(row))}
                aria-label={"Open " + row.name}>
                <Icon name="node-runner" size="md" className="workflow-row-icon" />
                <span className="workflow-row-copy">
                  <span className="workflow-row-title">{row.name}</span>
                  <span className="workflow-row-meta">Version {row.version} · saved {savedOn(row.created_at)}</span>
                </span>
                <Icon name="chevron-right" size="sm" className="workflow-row-icon" />
              </button>
            </li>)}</ul>
          : <p className="workflow-muted">No workflows match “{query.trim()}”.</p>}
        <p className="visually-hidden" role="status">{filtered.length ? plural(filtered.length, "matching workflow") : "No workflows match your search."}</p>
      </section>}
      {!loading && !rows.length && <EmptyState icon="node-runner" title="No workflows yet"
        body="Add a starter below, select connected nodes and choose Make from selection, or import a .graphlink-workflow file." />}
      <section className="workflow-group" aria-label="Built-in starter workflows">
        <h3 className="workflow-overline">Starters</h3>
        <ul className="workflow-starters">{starters.map(starter => <li key={starter.id} className="workflow-starter">
          <span className="workflow-row-title">{starter.name}</span>
          <span className="workflow-starter-body">{starter.description}</span>
          <Button size="sm" variant="secondary" iconStart="plus" disabled={busy} aria-label={"Add " + starter.name + " to the library"}
            onClick={() => importStarter(starter)}>Add to library</Button>
        </li>)}</ul>
      </section>
    </div>;
  }

  return <div className="workflow-library">
    <p className="visually-hidden" role="status">{loading ? "Loading workflows…" : busy ? "Updating the workflow library…" : ""}</p>
    {error && <InlineAlert variant="error" title="Something in the workflow library failed" body={error} />}
    <Toaster inlineScope="workflow-library" />
    <Tabs label="Workflow library sections" value={tab} items={[
      { id: "workflows", label: `Workflows (${rows.length})`, content: workflowsPanel },
      { id: "runs", label: waiting ? `Runs (${waiting} waiting)` : `Runs (${runs.length})`, content:
        <WorkflowRunsPanel key={runsViewKey} workspace={workspace} runs={runs} loaded={runsLoaded || workspace === null} presets={presets}
          detail={runDetail} detailLoaded={detailLoaded} selected={selectedRun} fromNotification={fromNotification}
          onSelect={selectRun} onChanged={() => setRunPoke(n => n + 1)} toolbar={workspaceControl} /> },
      { id: "presets", label: `Presets (${presets.length})`, content:
        <WorkflowPresetsPanel store={store} workspace={workspace} presets={presets} loaded={presetsLoaded || workspace === null} rows={rows}
          startWith={presetStart} onFormClosed={() => setPresetStart(null)} onChanged={() => setPresetPoke(n => n + 1)}
          onRunStarted={showRun} toolbar={workspaceControl} /> },
    ]} onValueChange={value => {
      if (value !== tab) { setRunDetail(null); setDetailLoaded(false); }
      setTab(value as Tab);
      // Tab panels unmount when hidden; a pending "Save as a preset…" request must not reopen the form later.
      if (value !== "presets") setPresetStart(null);
    }} />
  </div>;
}

export function WorkflowLibraryDialog({ store }: { store: SceneStore }) {
  return <Dialog name="workflow-library" title="Workflow library" size="xl" className="workflow-library-dialog">
    <Library store={store} />
  </Dialog>;
}
