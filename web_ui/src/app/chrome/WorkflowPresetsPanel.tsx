import { useEffect, useRef, useState, type ReactNode } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import type { WorkflowDocument, WorkflowEntry } from "../canvas/workflowLibrary";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { useConfirm } from "../ui/ConfirmDialog";
import { EmptyState } from "../ui/EmptyState";
import { FieldGroup } from "../ui/Field";
import { InlineAlert } from "../ui/InlineAlert";
import { MenuButton, MenuItem } from "../ui/Menu";
import { NumberField } from "../ui/NumberField";
import { SegmentedControl } from "../ui/SegmentedControl";
import { Select } from "../ui/Select";
import { TextField } from "../ui/TextField";
import { initialDrafts, WorkflowInputsForm } from "./WorkflowRunLauncher";
import { stepName, WorkflowPreviewSummary } from "./WorkflowPreviewSummary";
import {
  effectDetail, effectLabel, errorText, money, plainInputs, plural, triggerSummary, typedInputs, workflowRequest,
  workspacePath, type InputDrafts, type Preset, type PresetTrigger, type WorkflowPreview,
} from "./workflowApi";

const unattendedEffects = ["pure", "local-read", "cloud"] as const;
type TriggerKind = "manual" | "interval" | "daily" | "folder";

function presetSummary(preset: Preset) {
  const effects = preset.allowed_effects.length
    ? preset.allowed_effects.map(effect => effectLabel(effect).toLowerCase()).join(", ") + " steps run on their own"
    : "Every step waits for you";
  return [triggerSummary(preset.trigger), effects, "cap " + money(preset.cost_usd)].join(" · ");
}

function PresetForm({ store, rows, workspace, initialWorkflow, onCancel, onSaved }: {
  store: SceneStore; rows: WorkflowEntry[]; workspace: number; initialWorkflow: string;
  onCancel: () => void; onSaved: () => void;
}) {
  const [workflowId, setWorkflowId] = useState(initialWorkflow || rows[0]?.workflow_id || "");
  const [loaded, setLoaded] = useState<{ revision: string; doc: WorkflowDocument } | null>(null);
  const [name, setName] = useState("");
  const [drafts, setDrafts] = useState<InputDrafts>({});
  const [params, setParams] = useState<Record<string, string | number>>({});
  const [allowed, setAllowed] = useState<string[]>(["pure"]);
  const [cap, setCap] = useState<number | "">(0);
  const [trigger, setTrigger] = useState<TriggerKind>("manual");
  const [seconds, setSeconds] = useState<number | "">(3600);
  const [time, setTime] = useState("09:00");
  const [folder, setFolder] = useState("");
  const [review, setReview] = useState<{ body: Record<string, unknown>; preview: WorkflowPreview } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const entry = rows.find(row => row.workflow_id === workflowId);
  const doc = loaded && entry && loaded.revision === entry.revision ? loaded.doc : null;

  useEffect(() => {
    if (!entry) return;
    let live = true;
    store.workflowLibrary<WorkflowDocument>("get", { workflow_id: entry.workflow_id, revision: entry.revision }).then(next => {
      if (!live) return;
      setLoaded({ revision: entry.revision, doc: next });
      setDrafts(initialDrafts(next.spec.inputs, next.format === "graphlink-starter-workflow" ? next.example_inputs : undefined));
      setParams({});
      setName(current => current || next.spec.name);
    }).catch(err => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [store, entry]);

  function draft() {
    if (!doc || !entry) throw new Error("Choose a workflow.");
    if (!name.trim()) throw new Error("Give the preset a name.");
    if (cap === "" || cap < 0) throw new Error("Enter a cost cap of $0 or more.");
    if (trigger === "interval" && (seconds === "" || seconds < 10)) throw new Error("Run at most every 10 seconds.");
    if (trigger === "daily" && !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) throw new Error("Enter the daily time as HH:MM, for example 09:00.");
    if (trigger === "folder" && !folder.trim()) throw new Error("Enter the folder to watch.");
    const request = doc.format === "graphlink-workflow"
      ? { workflow_id: entry.workflow_id, revision: entry.revision, inputs: plainInputs(doc.spec.inputs, drafts), parameters: params }
      : { spec: doc.spec, parameters: typedInputs(doc.spec.inputs, drafts) };
    const when: PresetTrigger = trigger === "interval" ? { kind: "interval", seconds: Number(seconds) }
      : trigger === "daily" ? { kind: "daily", time } : trigger === "folder" ? { kind: "folder", path: folder.trim() } : null;
    return { request, name: name.trim(), allowed_effects: allowed, cost_usd: Number(cap), trigger: when };
  }
  const changed = (fn: () => void) => { fn(); setReview(null); };
  async function preview() {
    setBusy(true); setError(""); setReview(null);
    try {
      const body = draft();
      setReview({ body, preview: await workflowRequest<WorkflowPreview>(workspacePath(workspace) + "/presets/preview", { method: "POST", body: body.request }) });
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!review) return;
    setBusy(true); setError("");
    try {
      await workflowRequest(workspacePath(workspace) + "/presets", { method: "POST",
        body: { ...review.body, fingerprint: review.preview.fingerprint, approved: true } });
      onSaved();
    } catch (err) { setError(errorText(err)); setReview(null); }
    finally { setBusy(false); }
  }

  return <div className="workflow-form">
    <div className="workflow-form-grid">
      <Select label="Workflow" value={workflowId} disabled={busy} onChange={value => changed(() => { setWorkflowId(value); setName(""); setError(""); })}
        options={rows.map(row => ({ value: row.workflow_id, label: row.name + " · version " + row.version }))} />
      <TextField label="Preset name" value={name} disabled={busy} onValueChange={value => changed(() => setName(value))} />
    </div>
    {doc && <FieldGroup title="Inputs" description="The values every run of this preset uses.">
      <WorkflowInputsForm ports={doc.spec.inputs} drafts={drafts} disabled={busy} onChange={next => changed(() => setDrafts(next))} />
      {doc.parameters.map(param => param.kind === "choice"
        ? <Select key={param.name} label={stepName(param.name)}
            value={typeof params[param.name] === "string" ? "choice:" + params[param.name] : ""} disabled={busy}
            onChange={value => changed(() => setParams(old => {
              const next = { ...old };
              if (value === "") delete next[param.name];
              else next[param.name] = value.slice("choice:".length);
              return next;
            }))}
            // Prefix actual choices so even an empty string is distinct from the reset option.
            options={[{ value: "", label: "Keep the saved setting" },
              ...param.choices.map(choice => ({ value: "choice:" + choice, label: choice || "(Empty string)" }))]} />
        : param.kind === "number"
          ? <NumberField key={param.name} label={stepName(param.name)} description="Leave empty to keep the saved setting." disabled={busy}
              value={typeof params[param.name] === "number" ? params[param.name] as number : ""}
              onValueChange={value => changed(() => setParams(old => { const next = { ...old }; if (value === "") delete next[param.name]; else next[param.name] = value; return next; }))} />
          : <TextField key={param.name} label={stepName(param.name)} description="Leave empty to keep the saved setting." disabled={busy}
              value={String(params[param.name] ?? "")}
              onValueChange={value => changed(() => setParams(old => { const next = { ...old }; if (!value) delete next[param.name]; else next[param.name] = value; return next; }))} />)}
    </FieldGroup>}
    {!doc && !error && <p className="workflow-muted" role="status">Loading the workflow…</p>}
    <FieldGroup title="What it may do without you"
      description="Steps that run code or write files always pause and wait for you, whatever you choose here.">
      {unattendedEffects.map(effect => <Checkbox key={effect} label={effectLabel(effect)} description={effectDetail(effect)}
        disabled={busy} checked={allowed.includes(effect)} onChange={event => {
          const checked = event.currentTarget.checked;
          changed(() => setAllowed(old => checked ? [...old, effect] : old.filter(item => item !== effect)));
        }} />)}
      <NumberField label="Cost cap per run" unit="USD" min={0} step={0.01} value={cap} disabled={busy}
        description="The run stops before it would spend more than this." onValueChange={value => changed(() => setCap(value))} />
    </FieldGroup>
    <FieldGroup title="When it runs" description="Only while Graphlink is open. Missed times are skipped, not caught up.">
      <SegmentedControl label="When it runs" value={trigger} disabled={busy} onChange={value => changed(() => setTrigger(value as TriggerKind))}
        options={[{ value: "manual", label: "When I run it" }, { value: "interval", label: "On an interval" },
          { value: "daily", label: "Daily" }, { value: "folder", label: "When a folder changes" }]} />
      {trigger === "manual" && <p className="workflow-muted">Run it from this list, the command line or MCP.</p>}
      {trigger === "interval" && <NumberField label="Every" unit="seconds" min={10} step={1} value={seconds} disabled={busy}
        description="At least 10 seconds. A new run waits until the previous one finishes." onValueChange={value => changed(() => setSeconds(value))} />}
      {trigger === "daily" && <TextField label="Time of day" description="Local time, as HH:MM." value={time} disabled={busy}
        onValueChange={value => changed(() => setTime(value))} />}
      {trigger === "folder" && <TextField label="Folder to watch" description="An existing folder on this computer." value={folder} disabled={busy}
        mono onValueChange={value => changed(() => setFolder(value))} />}
    </FieldGroup>
    {error && <InlineAlert variant="error" title="Couldn't preview or save this preset" body={error} />}
    {review && <WorkflowPreviewSummary preview={review.preview} spec={doc?.spec} label="Preset preview" />}
    <div className="workflow-actions">
      {review
        ? <Button variant="primary" loading={busy} onClick={() => void save()}>Approve and save preset</Button>
        : <Button variant="primary" loading={busy} disabled={!doc} onClick={() => void preview()}>Preview preset</Button>}
      <Button variant="ghost" disabled={busy} onClick={onCancel}>Cancel</Button>
    </div>
  </div>;
}

/** A request to open the new-preset form for one workflow; a fresh object each time. */
export type PresetStart = { workflowId: string };

export function WorkflowPresetsPanel({ store, workspace, presets, loaded, rows, startWith, onFormClosed, onChanged, onRunStarted, toolbar }: {
  store: SceneStore; workspace: number | null; presets: Preset[]; loaded: boolean; rows: WorkflowEntry[];
  startWith: PresetStart | null; onFormClosed: () => void; onChanged: () => void; onRunStarted: (runId: string) => void;
  toolbar?: ReactNode;
}) {
  const askConfirm = useConfirm();
  const [form, setForm] = useState<string | null>(startWith?.workflowId ?? null);
  const [seenStart, setSeenStart] = useState(startWith);
  if (startWith !== seenStart) {
    setSeenStart(startWith);
    if (startWith) setForm(startWith.workflowId);
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return; }
    heading.current?.focus();
  }, [form]);
  // Closing the form also clears the request that opened it, so returning to this tab shows the list.
  const openForm = (value: string | null) => { setForm(value); if (value === null) onFormClosed(); };
  async function act(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); onChanged(); } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  const workflowName = (preset: Preset) => rows.find(row => row.workflow_id === preset.workflow_id)?.name
    ?? (preset.request as { spec?: { name?: string } }).spec?.name ?? "Workflow no longer in the library";

  if (form !== null && workspace !== null) {
    return <div className="workflow-panel">
      <Button variant="ghost" iconStart="chevron-left" className="workflow-back" onClick={() => openForm(null)}>All presets</Button>
      <h3 ref={heading} tabIndex={-1}>New preset</h3>
      <p className="workflow-muted">A preset saves a workflow's inputs, what it may do without you, and when it runs.</p>
      {rows.length
        ? <PresetForm key={form} store={store} rows={rows} workspace={workspace} initialWorkflow={form}
            onCancel={() => openForm(null)} onSaved={() => { openForm(null); onChanged(); }} />
        : <EmptyState icon="layers" title="No workflows to use" body="Add a starter or make a workflow in the Workflows tab first." />}
    </div>;
  }
  return <div className="workflow-panel">
    <div className="workflow-toolbar">
      <h3 ref={heading} tabIndex={-1} className="workflow-toolbar-title">Presets</h3>
      <span className="workflow-muted">{loaded ? plural(presets.length, "preset") : "Loading presets…"}</span>
      <span className="workflow-toolbar-end">
        {toolbar}
        <Button variant="primary" iconStart="plus" disabled={workspace === null} onClick={() => openForm("")}>New preset</Button>
      </span>
    </div>
    {error && <InlineAlert variant="error" title="That didn't go through" body={error} />}
    {loaded && !presets.length && <EmptyState icon="clock" title="No presets yet"
      body="Save a workflow's inputs once, then run it with one click, on a schedule, or when a folder changes." />}
    {!!presets.length && <ul className="workflow-list" aria-label="Presets">
      {presets.map(preset => <li key={preset.preset_id} className="workflow-row">
        <span className="workflow-row-main is-static">
          <span className="workflow-row-copy">
            <span className="workflow-row-title">{preset.name}</span>
            <span className="workflow-row-meta">{workflowName(preset)}</span>
            <span className="workflow-row-meta">{presetSummary(preset)}</span>
          </span>
        </span>
        <span className="workflow-row-actions">
          <Button size="sm" variant="secondary" iconStart="play" disabled={busy || workspace === null}
            onClick={() => void act(async () => {
              const { run_id } = await workflowRequest<{ run_id: string }>(workspacePath(workspace!) + "/presets/" + preset.preset_id + "/run", { method: "POST" });
              onRunStarted(run_id);
            })}>Run now</Button>
          <MenuButton label="More" menuLabel={"More actions for " + preset.name} disabled={busy}>
            <MenuItem label="Delete preset…" icon="trash" destructive onSelect={() => void act(async () => {
              if (!await askConfirm({ title: "Delete preset?", body: "Its schedule stops. Runs it already started are kept.", confirmLabel: "Delete", destructive: true })) return;
              await workflowRequest(workspacePath(workspace!) + "/presets/" + preset.preset_id, { method: "DELETE" });
            })} />
          </MenuButton>
        </span>
      </li>)}
    </ul>}
  </div>;
}
