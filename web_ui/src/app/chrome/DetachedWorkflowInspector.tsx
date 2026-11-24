import { useEffect, useState } from "react";
import { authHeaders } from "../../lib/auth/token";
import { useOverlays } from "../overlays/overlays";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Disclosure } from "../ui/Disclosure";
import { InlineAlert } from "../ui/InlineAlert";
import { Select } from "../ui/Select";
import { TextArea } from "../ui/TextArea";
import { toast } from "../ui/Toast";

type Gate = {
  workspace_id: number; run_id: string; workflow: string; step_id: string; message: string;
};
type Workspace = { id: number; name: string };
type ControlPreview = {
  paths?: string[]; predicate?: string; message?: string; mode?: string;
  item_count?: number; estimated_cost_usd?: number;
};
type Preview = {
  fingerprint: string; estimated_cost_usd: number; requires_approval: boolean;
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

export function WorkflowApprovalNotifications({ connected }: { connected: boolean }) {
  const overlays = useOverlays();
  // Poll only while connected. The lightweight endpoint does not initialize
  // the detached engine or open the database when there is no active run.
  useEffect(() => {
    if (!connected) return;
    const controller = new AbortController();
    const shown = new Map<string, string>();
    let timer: number | undefined;
    async function poll() {
      try {
        const gates = await request<Gate[]>(root + "/active-approvals", { signal: controller.signal });
        if (controller.signal.aborted || !Array.isArray(gates)) return;
        const pending = new Set<string>();
        for (const gate of gates) {
          const key = JSON.stringify([gate.workspace_id, gate.run_id, gate.step_id]);
          pending.add(key);
          if (!shown.has(key)) {
            shown.set(key, toast.show({
              variant: "warning", title: "Workflow waiting for approval: " + gate.workflow,
              details: gate.message, action: {
                label: "Open run history", onClick: () => overlays.open("run-history", "dialog"),
              },
            }));
          }
        }
        for (const [key, id] of shown) {
          if (!pending.has(key)) { toast.dismiss(id); shown.delete(key); }
        }
      } catch { /* A disconnected client must not fabricate a gate. */ }
      finally {
        if (!controller.signal.aborted) timer = window.setTimeout(() => { void poll(); }, 1500);
      }
    }
    void poll();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      for (const id of shown.values()) toast.dismiss(id);
    };
  }, [connected, overlays]);
  return null;
}

function PreviewDetails({ preview }: { preview: Preview }) {
  return <section aria-label="Workflow preview">
    <p>Maximum estimated cost: {money(preview.estimated_cost_usd)}.
      {preview.requires_approval ? " Execution requires explicit approval." : " No provider approval required."}</p>
    {Object.entries(preview.control_flow ?? {}).map(([id, control]) => <Card key={id} title={id}>
      {control.paths && <div>Possible paths: {control.paths.join(" / ")} · {control.predicate}</div>}
      {control.mode && <div>Merge: {control.mode === "first" ? "First completed input" : "All non-skipped inputs"}</div>}
      {control.message && <div>Human approval: {control.message} (no timeout)</div>}
      {control.item_count !== undefined && <div>Map: {control.item_count} items · total estimated cost {money(control.estimated_cost_usd ?? 0)}</div>}
    </Card>)}
  </section>;
}

function WorkspaceRuns({ workspace }: { workspace: number }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState("");
  const [source, setSource] = useState("");
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
      const body = JSON.parse(source);
      if (preview.requires_approval) {
        const result = await request<{ approval_token: string }>(workspacePath(workspace) + "/approve", {
          method: "POST", body: JSON.stringify({ approved: true, fingerprint: preview.fingerprint }),
        });
        body.approval_token = result.approval_token;
      }
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
    <Disclosure title="Preview and start a typed workflow">
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
        <Badge>{run.status}</Badge><p>Recorded cost: {money(run.cost_usd)}</p>
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

function Inspector() {
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
    {workspace && <WorkspaceRuns key={workspace} workspace={Number(workspace)} />}
  </>;
}

export function DetachedWorkflowInspector() {
  const [open, setOpen] = useState(false);
  return <Disclosure title="Detached workflow runs" variant="section" open={open} onOpenChange={setOpen}>
    {open && <Inspector />}
  </Disclosure>;
}
