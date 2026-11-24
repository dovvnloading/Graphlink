import { useEffect, useState } from "react";
import { authHeaders } from "../../lib/auth/token";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Disclosure } from "../ui/Disclosure";
import { InlineAlert } from "../ui/InlineAlert";
import { Select } from "../ui/Select";
import { TextArea } from "../ui/TextArea";

type Workspace = { id: number; name: string };
type Policy = { execution_lane: string; model_identity: [string, string] | null; cost_usd: number };
type ReviewedStep = [string, string, Policy];
type ControlPreview = {
  paths?: string[]; predicate?: string; message?: string; mode?: string;
  item_count?: number; estimated_cost_usd?: number; child_steps?: ReviewedStep[];
};
type Preview = {
  fingerprint: string; estimated_cost_usd: number; requires_approval: boolean;
  steps: ReviewedStep[];
  control_flow: Record<string, ControlPreview>;
};
type Step = {
  step_id: string; status: string; error: string | null; skipped_outputs?: string[];
  approval?: { status: string; message: string };
  map_items?: Record<string, { status: string; error: string | null; cost_usd: number }>;
};
type Run = {
  run_id: string; spec: { name: string }; status: string; error: string | null;
  steps: Record<string, Step>; preview: Preview; cost_usd: number;
};
const root = "/api/workflows";
function workspacePath(id: number) { return root + "/" + id; }
function runPath(id: number, runId: string) { return workspacePath(id) + "/runs/" + encodeURIComponent(runId); }
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options, headers: { ...authHeaders(), "Content-Type": "application/json" },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "Workflow request failed.");
  }
  return response.json() as Promise<T>;
}
function message(error: unknown) { return error instanceof Error ? error.message : String(error); }
function money(value: number) { return "$" + value.toFixed(6).replace(/0{0,4}$/, ""); }

function ReviewedSteps({ steps }: { steps: ReviewedStep[] }) {
  return <ol>{steps.map(([id, effect, policy]) => <li key={id}>
    {id} · {effect} · {policy.execution_lane}
    {policy.model_identity && " · " + policy.model_identity.join(" / ")}
    {" · " + money(policy.cost_usd)}
  </li>)}</ol>;
}

function PreviewDetails({ preview }: { preview: Preview }) {
  return <section aria-label="Workflow preview">
    <p>Maximum estimated cost: {money(preview.estimated_cost_usd)}.
      {preview.requires_approval ? " Execution requires explicit approval." : " No provider approval required."}</p>
    <ReviewedSteps steps={preview.steps} />
    {Object.entries(preview.control_flow ?? {}).map(([id, control]) => <Card key={id} title={id}>
      {control.paths && <div>Possible paths: {control.paths.join(" / ")} · {control.predicate}</div>}
      {control.mode && <div>Merge: {control.mode === "first" ? "First completed input" : "All non-skipped inputs"}</div>}
      {control.message && <div>Human approval: {control.message} (no timeout)</div>}
      {control.item_count !== undefined && <><div>Map: {control.item_count} items · total estimated cost {money(control.estimated_cost_usd ?? 0)}</div>
        {control.child_steps && <ReviewedSteps steps={control.child_steps} />}</>}
    </Card>)}
  </section>;
}

function WorkspaceRuns({ workspace, initialSource }: { workspace: number; initialSource?: string }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState("");
  const [source, setSource] = useState(initialSource ?? "");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    async function poll() {
      try {
        const result = await request<Run[]>(workspacePath(workspace) + "/runs", { signal: controller.signal });
        if (!controller.signal.aborted && Array.isArray(result)) setRuns(result);
      } catch (err) { if (!controller.signal.aborted) setError(message(err)); }
      finally {
        if (!controller.signal.aborted) timer = window.setTimeout(() => { void poll(); }, 1500);
      }
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [workspace, refresh]);
  async function review() {
    setBusy(true); setError(""); setPreview(null);
    try {
      const body = JSON.parse(source);
      setPreview(await request<Preview>(workspacePath(workspace) + "/preview", {
        method: "POST", body: JSON.stringify(body),
      }));
    } catch (err) { setError(message(err)); }
    finally { setBusy(false); }
  }
  async function start() {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      const parsed = JSON.parse(source);
      const { approval_token } = await request<{ approval_token: string }>(workspacePath(workspace) + "/approve", {
        method: "POST", body: JSON.stringify({ approved: true, fingerprint: preview.fingerprint }),
      });
      const body = { spec: parsed.spec, parameters: parsed.parameters, approval_token };
      const result = await request<{ run_id: string }>(workspacePath(workspace) + "/runs", {
        method: "POST", body: JSON.stringify(body),
      });
      setSelected(result.run_id); setPreview(null); setRefresh(value => value + 1);
    } catch (err) { setError(message(err)); setPreview(null); }
    finally { setBusy(false); }
  }
  async function decide(run: Run, step: Step, approved: boolean) {
    setBusy(true); setError("");
    try {
      await request(runPath(workspace, run.run_id) + "/step-approval", {
        method: "POST", body: JSON.stringify({ step_id: step.step_id, approved }),
      });
      setRefresh(value => value + 1);
    } catch (err) { setError(message(err)); }
    finally { setBusy(false); }
  }
  async function cancel(run: Run) {
    setBusy(true); setError("");
    try {
      await request(runPath(workspace, run.run_id) + "/cancel", { method: "POST" });
      setRefresh(value => value + 1);
    } catch (err) { setError(message(err)); }
    finally { setBusy(false); }
  }
  const run = runs.find(item => item.run_id === selected);
  return <>
    <Disclosure title="Preview and start a typed workflow" defaultOpen={!!initialSource}>
      <TextArea label="Workflow request JSON (spec and parameters)" value={source} disabled={busy}
        onValueChange={value => { setSource(value); setPreview(null); }} minRows={4} />
      <Button disabled={!source.trim() || busy} onClick={() => { void review(); }}>Preview workflow</Button>
      {preview && <><PreviewDetails preview={preview} />
        <Button disabled={busy} onClick={() => { void start(); }}>
          {preview.requires_approval ? "Approve preview and start" : "Start reviewed workflow"}
        </Button></>}
    </Disclosure>
    {error && <InlineAlert title="Workflow request" variant="error" body={error} />}
    <Select label="Detached run" value={selected} onChange={setSelected}
      options={[{ value: "", label: "Select a run" }, ...runs.map(item => ({
        value: item.run_id, label: item.spec.name + " · " + item.status + " · " + item.run_id,
      }))]} />
    {!runs.length && <p>No detached workflow runs in this workspace.</p>}
    {run && <section aria-label="Detached workflow inspector">
      <Card title={run.spec.name}>
        <Badge>{Object.values(run.steps).some(step => step.approval?.status === "pending") && run.status === "running"
          ? "Waiting for approval" : run.status}</Badge><p>Recorded cost: {money(run.cost_usd)}</p>
        {run.error && <InlineAlert title="Run reason" body={run.error} variant="warning" />}
        {(run.status === "running" || run.status === "pending") &&
          <Button disabled={busy} variant="secondary" onClick={() => { void cancel(run); }}>Cancel run</Button>}
        <Disclosure title="Reviewed preview"><PreviewDetails preview={run.preview} /></Disclosure>
      </Card>
      {Object.values(run.steps).map(step => <Card key={step.step_id} title={step.step_id}>
        <Badge>{step.status}</Badge>
        {step.error && <p>{step.error}</p>}
        {step.skipped_outputs?.length ? <p>Skipped outputs: {step.skipped_outputs.join(", ")}</p> : null}
        {step.approval && <><p>Approval: {step.approval.status} · {step.approval.message}</p>
          {step.approval.status === "pending" && run.status === "running" && <>
            <Button disabled={busy} onClick={() => { void decide(run, step, true); }}>Approve step</Button>
            <Button disabled={busy} variant="secondary" onClick={() => { void decide(run, step, false); }}>Reject step</Button>
          </>}</>}
        {step.map_items && <Disclosure title="Map item results">
          <ol>{Object.entries(step.map_items).sort(([a], [b]) => Number(a) - Number(b)).map(([index, item]) =>
            <li key={index}>Item {Number(index) + 1}: {item.status} · {money(item.cost_usd)} {item.error ?? ""}</li>)}</ol>
        </Disclosure>}
      </Card>)}
    </section>}
  </>;
}

function Inspector({ initialSource }: { initialSource?: string }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void request<Workspace[]>(root + "/workspaces", { signal: controller.signal }).then(rows => {
      if (!controller.signal.aborted && Array.isArray(rows)) {
        setWorkspaces(rows); setWorkspace(rows.length ? String(rows[0].id) : "");
      }
    }).catch(err => { if (!controller.signal.aborted) setError(message(err)); });
    return () => controller.abort();
  }, []);
  return <>
    {error && <InlineAlert title="Could not load workspaces" body={error} variant="error" />}
    <Select label="Workspace" value={workspace} onChange={setWorkspace}
      options={workspaces.map(item => ({ value: String(item.id), label: item.name }))} />
    {workspace && <WorkspaceRuns key={workspace} workspace={Number(workspace)} initialSource={initialSource} />}
  </>;
}

export function DetachedWorkflowInspector({ initialSource }: { initialSource?: string } = {}) {
  const [open, setOpen] = useState(!!initialSource);
  return <Disclosure title="Detached workflow runs" variant="section" open={open} onOpenChange={setOpen}>
    {open && <Inspector initialSource={initialSource} />}
  </Disclosure>;
}
