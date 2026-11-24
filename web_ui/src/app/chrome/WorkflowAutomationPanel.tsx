import { useEffect, useState } from "react";
import type { WorkflowDocument } from "../canvas/workflowLibrary";
import { authHeaders } from "../../lib/auth/token";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { Select } from "../ui/Select";
import { TextField } from "../ui/TextField";
import { TextArea } from "../ui/TextArea";
import { InlineAlert } from "../ui/InlineAlert";
import { useConfirm } from "../ui/ConfirmDialog";

type Workspace = { id: number; name: string; archived: boolean };
type Preset = { preset_id: string; name: string; trigger: unknown; cost_usd: number;
  allowed_effects: string[]; request: unknown; revision: string };
type Preview = { fingerprint: string; estimated_cost_usd: number; steps: unknown };
type Pending = { step_id: string; nonce: string; side_effect_class: string; preview_fingerprint: string };
type Run = { run_id: string; preset_id?: string; status: string; error?: string; cost_usd: number;
  spec: { name: string; steps: { step_id: string }[] }; inputs: unknown; outputs: unknown;
  steps: Record<string, unknown>; awaiting_approval?: Pending | null; preview: Preview };

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: { ...authHeaders(), "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await res.json();
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Workflow request failed.");
  return data as T;
}

const effects = ["pure", "local-read", "cloud"];

export function WorkflowAutomationPanel({ view }: { view: WorkflowDocument | null }) {
  const confirm = useConfirm();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState("");
  const [presets, setPresets] = useState<Preset[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [name, setName] = useState("");
  const [inputs, setInputs] = useState("{}");
  const [parameters, setParameters] = useState("{}");
  const [allowed, setAllowed] = useState(["pure"]);
  const [cap, setCap] = useState("0");
  const [trigger, setTrigger] = useState("manual");
  const [interval, setIntervalText] = useState("60");
  const [daily, setDaily] = useState("09:00");
  const [folder, setFolder] = useState("");
  const [review, setReview] = useState<{ key: string; body: Record<string, unknown>; preview: Preview } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const prefix = "/api/workflows/" + encodeURIComponent(workspace);
  const draftKey = JSON.stringify([workspace, view?.spec.workflow_id, view?.spec.revision,
    name, inputs, parameters, allowed, cap, trigger, interval, daily, folder]);
  const reviewed = review?.key === draftKey ? review : null;

  useEffect(() => {
    let stopped = false;
    void request<{ workspaces: Workspace[] }>("/api/control/workspaces").then(result => {
      if (!stopped) setWorkspaces(result.workspaces);
    }).catch(e => { if (!stopped) setError(String(e)); });
    return () => { stopped = true; };
  }, []);

  useEffect(() => {
    if (!workspace) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const [p, r] = await Promise.all([
          request<Preset[]>(prefix + "/presets"),
          request<Run[]>(prefix + "/runs"),
        ]);
        if (!stopped) { setPresets(p); setRuns(r); }
      } catch (e) { if (!stopped) setError(String(e)); }
      finally { if (!stopped) timer = setTimeout(() => void load(), 2000); }
    }
    void load();
    return () => { stopped = true; clearTimeout(timer); };
  }, [workspace, prefix, refresh]);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); setRefresh(n => n + 1); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  function makeDraft() {
    if (!view) throw new Error("Open a library workflow first.");
    if (!cap.trim() || !Number.isFinite(Number(cap)) || Number(cap) < 0) throw new Error("Enter a nonnegative cost cap.");
    const value = { workflow_id: view.spec.workflow_id, revision: view.spec.revision,
      inputs: JSON.parse(inputs), parameters: JSON.parse(parameters) };
    return { request: value, name, allowed_effects: allowed, cost_usd: Number(cap),
      trigger: trigger === "interval" ? { kind: trigger, seconds: Number(interval) } :
        trigger === "daily" ? { kind: trigger, time: daily } :
        trigger === "folder" ? { kind: trigger, path: folder } : null };
  }

  return <section>
    <h3>Run presets and runs</h3>
    <p>Presets pin a revision and exact inputs. Triggers run only while Graphlink is open, with no catch-up runs.
      Code and file-writing steps always pause for operator approval.</p>
    {error && <InlineAlert variant="error" title="Workflow automation" body={error} />}
    <Select label="Run workspace" value={workspace} disabled={busy} onChange={value => {
      setPresets([]); setRuns([]); setWorkspace(value);
    }} options={[{ value: "", label: "Choose a workspace" }, ...workspaces.map(w =>
      ({ value: String(w.id), label: w.name + (w.archived ? " (archived)" : "") }))]} />
    {workspace && view && <>
      <h4>New preset for {view.spec.name}</h4>
      <TextField label="Preset name" value={name} onValueChange={setName} disabled={busy} />
      <TextArea label="Inputs (JSON object)" description="Use the input names in the workflow definition above." value={inputs} onValueChange={setInputs} disabled={busy} />
      <TextArea label="Parameters (JSON object)" description="Use the promoted parameter names; omit unchanged parameters." value={parameters} onValueChange={setParameters} disabled={busy} />
      <h4>Allowed unattended side effects</h4>
      {effects.map(effect => <Checkbox key={effect} label={effect} disabled={busy} checked={allowed.includes(effect)}
        onChange={e => { const checked = e.currentTarget.checked; setAllowed(old => checked ? [...old, effect] : old.filter(v => v !== effect)); }} />)}
      <TextField label="Maximum run cost (USD)" value={cap} onValueChange={setCap} disabled={busy} />
      <Select label="Trigger" value={trigger} onChange={setTrigger} disabled={busy}
        options={[{value:"manual",label:"Manual / CLI / MCP only"}, {value:"interval",label:"Interval"},
          {value:"daily",label:"Daily (local time)"}, {value:"folder",label:"Watched folder"}]} />
      {trigger === "interval" && <TextField label="Interval in seconds (minimum 10)" value={interval} onValueChange={setIntervalText} disabled={busy} />}
      {trigger === "daily" && <TextField label="Daily local time (HH:MM)" value={daily} onValueChange={setDaily} disabled={busy} />}
      {trigger === "folder" && <TextField label="Existing folder path" value={folder} onValueChange={setFolder} disabled={busy} />}
      <Button disabled={busy || !name.trim()} onClick={() => void act(async () => {
        const body = makeDraft();
        const preview = await request<Preview>(prefix + "/presets/preview", "POST", body.request);
        setReview({ key: draftKey, body, preview });
      })}>Preview preset</Button>
      {reviewed && <>
        <h4>Review exact preset and estimated cost</h4>
        <TextArea label="Preset preview (read-only)" readOnly value={JSON.stringify({ ...reviewed.body, preview: reviewed.preview }, null, 2)} />
        <Button variant="primary" disabled={busy} onClick={() => void act(async () => {
          await request(prefix + "/presets", "POST", { ...reviewed.body, fingerprint: reviewed.preview.fingerprint, approved: true });
          setReview(null);
        })}>Approve and save preset</Button>
      </>}
    </>}
    {workspace && <>
      <h4>Saved presets</h4>
      {!presets.length && <p>No presets in this workspace.</p>}
      <ul className="runner-steps">{presets.map(p => <li key={p.preset_id}>
        <strong>{p.name}</strong> · {p.allowed_effects.join(", ") || "No unattended effects"} · cap ${p.cost_usd}
        <TextArea label="Saved preset (read-only)" readOnly value={JSON.stringify({ id: p.preset_id, trigger: p.trigger, request: p.request }, null, 2)} />
        <div className="runner-commit">
          <Button disabled={busy} onClick={() => void act(async () => {
            await request(prefix + "/presets/" + p.preset_id + "/run", "POST");
          })}>Run saved preset</Button>
          <Button disabled={busy} onClick={() => void act(async () => {
            if (await confirm({ title: "Delete preset?", body: "Future triggers will stop. Existing runs are not cancelled.", confirmLabel: "Delete", destructive: true }))
              await request(prefix + "/presets/" + p.preset_id, "DELETE");
          })}>Delete preset</Button>
        </div>
      </li>)}</ul>
      <h4>Detached runs</h4>
      {!runs.length && <p>No detached runs in this workspace.</p>}
      <ul className="runner-steps">{runs.map(run => {
        const pending = run.awaiting_approval;
        return <li key={run.run_id}>
          <strong>{run.spec.name}</strong> · {pending ? "Waiting for operator approval" : run.status} · ${run.cost_usd}
          {run.error && <p>{run.error}</p>}
          {pending && <InlineAlert variant="warning" title={"Review " + pending.side_effect_class + " step " + pending.step_id}
            body="Nothing in this step has run. Approve only after reviewing its exact configuration and inputs below." />}
          {pending && <TextArea label="Pending step and frozen run inputs (read-only)" readOnly
            value={JSON.stringify({ step: run.spec.steps.find(s => s.step_id === pending.step_id),
              inputs: run.inputs, upstream: run.steps, preview: run.preview }, null, 2)} />}
          <div className="runner-commit">
            {pending && [true, false].map(approved => <Button key={String(approved)} disabled={busy} onClick={() => void act(async () => {
              await request(prefix + "/runs/" + run.run_id + "/step-decision", "POST",
                { step_id: pending.step_id, nonce: pending.nonce, approved });
            })}>{approved ? "Approve this step once" : "Deny and stop run"}</Button>)}
            {(run.status === "running" || run.status === "pending") && <Button disabled={busy} onClick={() => void act(async () => {
              await request(prefix + "/runs/" + run.run_id + "/cancel", "POST");
            })}>Cancel run</Button>}
          </div>
          <TextArea label="Run result (read-only)" readOnly value={JSON.stringify({ id: run.run_id, outputs: run.outputs }, null, 2)} />
        </li>;
      })}</ul>
    </>}
  </section>;
}
