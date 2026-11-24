import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { alignWorkflowSteps, changedWorkflowFields, type WorkflowHistoryPage,
  type WorkflowRunDetail, type WorkflowStepReceipt } from "../canvas/workflowRunTypes";
import { Dialog, useOverlays } from "../overlays/overlays";
import { Badge, StatusPill, type StatusWord } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Disclosure } from "./RunnerDisclosure";
import { EmptyState } from "../ui/EmptyState";
import { InlineAlert } from "../ui/InlineAlert";
import { Select } from "../ui/Select";
import { Spinner } from "../ui/Spinner";
import { formatCount, formatDuration } from "../ui/format";
import { FullWorkflowContents } from "./WorkflowContents";
import { DetachedWorkflowInspector } from "./DetachedWorkflowInspector";
import "./GraphRunnerDialog.css";
import "./WorkflowHistoryDialog.css";

const labels: Record<string, string> = {
  pending: "Queued", current: "Skipped (already current)", completed: "Completed",
  partial: "Completed with issues", blocked: "Blocked", failed: "Failed",
  cancelled: "Cancelled", running: "Running", interrupted: "Interrupted",
  "reconciliation-required": "Reconciliation required",
};
const statusWords: Record<string, StatusWord> = {
  pending: "Queued", current: "Skipped", completed: "Done", failed: "Failed",
  cancelled: "Cancelled", running: "Running", "reconciliation-required": "Failed",
};
function statusLabel(status: string) { return labels[status] ?? status; }
function time(value: string | null) {
  return value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : "Not recorded";
}
function count(value: number | null) { return value === null ? "Not recorded" : formatCount(value); }
function duration(value: number | null) { return value === null ? "Not recorded" : formatDuration(value / 1000); }
function cost(value: number | null) {
  if (value === null) return "Not recorded";
  if (value > 0 && value < 0.000001) return "Less than $0.000001";
  return "$" + value.toFixed(6).replace(/(\.\d{2}?)0+$/, "$1");
}
function StepStatus({ step }: { step: WorkflowStepReceipt }) {
  const status = step.approval === "pending" && (step.status === "pending" || step.status === "running")
    ? "Waiting for approval" : statusWords[step.status];
  return status ? <StatusPill status={status} announce={false} /> : <Badge>{statusLabel(step.status)}</Badge>;
}

function Receipt({ run, step, names, store, onRetry }: {
  run: WorkflowRunDetail; step: WorkflowStepReceipt; names: Map<string, string>; store: SceneStore;
  onRetry?: () => void;
}) {
  const name = step.nodeId && names.get(step.nodeId);
  return <div className="workflow-receipt">
    <div className="runner-step-heading">
      <strong>Step {step.step_index + 1}: {name || step.nodeId || step.input_ref.destinationNodeId || step.operation}</strong>
      <StepStatus step={step} />
    </div>
    <div>{step.kind} · {step.operation} · {statusLabel(step.status)}</div>
    <div>Inputs: {step.sourceIds.map(id => names.get(id) || id).filter(Boolean).join(", ") || "No recorded source"}</div>
    <div>Provider: {step.provider ?? "Not recorded"} · Model: {step.model ?? "Not recorded"}</div>
    <div>Tokens: {count(step.input_tokens)} input · {count(step.output_tokens)} output · {count(step.total_tokens)} total</div>
    <div>Cost (USD): {cost(step.cost_usd)} · Retries: {formatCount(step.retry_count)}</div>
    <div>Approval: {step.approval ?? "Not recorded"} {step.approval_id && <code>{step.approval_id}</code>}</div>
    <div>Started: {time(step.started_at)} · Finished: {time(step.finished_at)} · Duration: {duration(step.duration_ms)}</div>
    <Disclosure label="Input and output references">
      <div>Input fingerprint: <code>{step.input_ref.sha256 ?? "Not recorded"}</code></div>
      <div>Configuration: <code>{step.input_ref.configuration ?? "Not recorded"}</code></div>
      <div>Output fingerprint: <code>{step.output_ref?.sha256 ?? "Not recorded"}</code></div>
      <div>Output version: {step.output_ref?.version ?? "Not recorded"}</div>
      {step.request_id && <div>Request: <code>{step.request_id}</code> · {step.attempt_phase ?? "Not recorded"}</div>}
    </Disclosure>
    <FullWorkflowContents key={run.run_id + ":" + step.step_index + ":" + step.input_ref.sha256 + ":" + step.output_ref?.sha256}
      load={() => store.showWorkflowStep(run.graph_id, run.run_id, step.step_index)} />
    {onRetry && step.retryable && <Button size="sm" onClick={onRetry}>Re-run from this step</Button>}
  </div>;
}

function RunDetail({ run, other, store, names, retry }: {
  run: WorkflowRunDetail; other: WorkflowRunDetail | null; store: SceneStore; names: Map<string, string>;
  retry: (ids: string[], mode: "from" | "failed", runId: string) => void;
}) {
  const failed = run.steps.filter(step => step.status === "failed");
  const canRetryFailed = failed.length > 0 && failed.every(step => step.retryable && step.nodeId);
  return <section aria-label="Run detail">
    <div className="runner-section-header"><h2>Run detail</h2><Badge>{statusLabel(run.status)}</Badge></div>
    <p>Run <code>{run.run_id}</code> · {time(run.started_at)} · {duration(run.duration_ms)}</p>
    {!run.retained && <InlineAlert title="Archived plan" body="The durable receipts are retained. Full bodies and retry controls require a matching live plan." />}
    {run.status === "reconciliation-required" && <InlineAlert variant="warning" title="Uncertain external request"
      body="The provider may already have executed or charged for this request. It cannot be retried until reconciled." />}
    {canRetryFailed && <Button onClick={() => retry(failed.map(step => step.nodeId!), "failed", run.run_id)}>Run failed steps only</Button>}
    {other ? <section aria-label="Step comparison">
      <div className="runner-section-header"><h2>Step-by-step comparison</h2></div>
      <p>Comparing {run.run_id} with {other.run_id} · {statusLabel(other.status)}</p>
      {(!run.retained || !other.retained) && <InlineAlert title="Position-based alignment"
        body="A pruned plan has no reliable live node mapping. Steps are aligned by position, kind and operation." />}
      <ol className="workflow-timeline">{alignWorkflowSteps(run, other).map(({ first, second }, index) =>
        <li key={index}>
          <Card title={"Comparison step " + (index + 1)}>
            <Badge>{changedWorkflowFields(first, second).join(", ") || "Unchanged"}</Badge>
            <div className="workflow-compare">
              <div><h4>Selected run</h4>{first ? <Receipt run={run} step={first} names={names} store={store} /> : <p>Not present</p>}</div>
              <div><h4>Comparison run</h4>{second ? <Receipt run={other} step={second} names={names} store={store} /> : <p>Not present</p>}</div>
            </div>
          </Card>
        </li>)}</ol>
    </section> : <ol className="workflow-timeline runner-timeline">{run.steps.map(step => <li key={step.step_index}>
      <Card><Receipt run={run} step={step} names={names} store={store}
        onRetry={step.nodeId ? () => retry([step.nodeId!], "from", run.run_id) : undefined} /></Card>
    </li>)}</ol>}
    <Disclosure label={"Recorded events (" + run.events.length + ")"}>
      <ol className="runner-steps">{run.events.map(event => <li key={event.event_id}>
        {time(event.recorded_at)} · {event.step_index === null ? "Run" : "Step " + (event.step_index + 1)}
        {" · "}{statusLabel(event.status)}
        {event.approval && <> · Approval: {event.approval}</>}
        {event.retry_count !== null && <> · Retries: {event.retry_count}</>}
        {event.attempt_phase && <> · {event.attempt_phase}</>}
      </li>)}</ol>
    </Disclosure>
  </section>;
}

function History({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const [history, setHistory] = useState<WorkflowHistoryPage | null>(null);
  const [pages, setPages] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [selection, setSelection] = useState<{ graphId: number; runId: string } | null>(null);
  const [compare, setCompare] = useState("");
  const [details, setDetails] = useState<{ run: WorkflowRunDetail; other: WorkflowRunDetail | null } | null>(null);
  const [error, setError] = useState("");
  const navigationHeading = useRef<HTMLHeadingElement>(null);
  const moveFocus = useRef(false);
  const graphId = history?.graphId;
  const runId = selection && selection.graphId === graphId ? selection.runId : "";
  useEffect(() => {
    if (moveFocus.current) {
      moveFocus.current = false;
      navigationHeading.current?.focus();
    }
  }, [runId]);

  useEffect(() => {
    let active = true;
    let loading = false;
    let dirty = false;
    let running = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let revision = store.getScene().revision;

    // Coalesce scene changes while a request is in flight. A 100-step run
    // can publish much faster than the journal is read; restarting the
    // effect for every revision discards useful replies and floods the bus.
    function schedule() {
      if (active && !loading && timer === undefined) {
        timer = setTimeout(() => { timer = undefined; void load(); }, 1200);
      }
    }
    async function load() {
      if (!active || loading) return;
      loading = true;
      dirty = false;
      try {
        const first = await store.getWorkflowHistory(0);
        let page = first;
        const runs = [...first.runs];
        for (let i = 1; active && i < pages && page.hasMore; i++) {
          page = await store.getWorkflowHistory(i * 50);
          if (page.graphId !== first.graphId) throw new Error("The open graph changed. Refresh history.");
          runs.push(...page.runs);
        }
        if (!active) return;
        running = runs.some(run => run.status === "running");
        setHistory({ ...first, runs, hasMore: page.hasMore });
        if (first.graphId != null && selection?.graphId === first.graphId) {
          const [run, other] = await Promise.all([
            store.getWorkflowRunDetail(first.graphId, selection.runId),
            compare ? store.getWorkflowRunDetail(first.graphId, compare) : Promise.resolve(null),
          ]);
          if (active) setDetails({ run, other });
        } else if (active) {
          setDetails(null);
        }
        if (active) setError("");
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : String(err));
      } finally {
        loading = false;
        if (dirty || running) schedule();
      }
    }
    const unsubscribe = store.subscribe(() => {
      const next = store.getScene().revision;
      if (next === revision) return;
      revision = next;
      dirty = true;
      schedule();
    });
    void load();
    return () => { active = false; clearTimeout(timer); unsubscribe(); };
  }, [store, pages, refresh, selection, compare]);

  const names = useMemo(() => new Map(scene.nodes.map(node => [node.id, node.title])), [scene.nodes]);
  const detail = details?.run.graph_id === graphId && details?.run.run_id === runId &&
    (details.other?.run_id ?? "") === compare ? details : null;
  function retry(ids: string[], mode: "from" | "failed", sourceRunId: string) {
    store.requestGraphRunnerNodes(ids, mode, sourceRunId);
    overlays.open("graph-runner", "dialog");
  }
  return <>
    <div className="runner-controls">
      {runId && <Button onClick={() => { moveFocus.current = true; setSelection(null); setCompare(""); setDetails(null); }}>Back to run history</Button>}
      <Button variant="secondary" iconStart="refresh" onClick={() => setRefresh(value => value + 1)}>Refresh</Button>
    </div>
    <h2 ref={navigationHeading} tabIndex={-1}>{runId ? "Selected run" : "Saved runs"}</h2>
    <div className="visually-hidden" role="status">{detail ? `Selected run: ${statusLabel(detail.run.status)}. ${detail.run.steps.length} steps.` : runId ? "Loading run detail" : history ? `${history.runs.length} saved runs loaded` : "Loading run history"}</div>
    {error && <InlineAlert title="Could not read run history" variant="error" body={error} />}
    {!history && !error && <Spinner label="Loading run history" />}
    {history && !history.runs.length && <EmptyState icon="play" title="No saved runs"
      body="Run a reviewed workflow in this graph to record its steps here." />}
    {history && !runId && !!history.runs.length && <>
      <p>Durable runs in the open graph. Prompt and result contents are hidden by default.</p>
      <ol className="workflow-timeline">{history.runs.map(run => <li key={run.run_id}>
        <Card title={time(run.started_at)} actions={<Button size="sm" disabled={graphId == null}
          aria-label={"View run " + run.run_id}
          onClick={() => { if (graphId != null) { moveFocus.current = true; setSelection({ graphId, runId: run.run_id }); setCompare(""); } }}>View run</Button>}>
          <Badge>{statusLabel(run.status)}</Badge>
          <div><code>{run.run_id}</code></div>
          <div>{formatCount(run.step_count)} steps · {formatCount(run.failed_count)} failed · {duration(run.duration_ms)}</div>
          <div>Recorded tokens: {count(run.total_tokens)} · Recorded cost: {cost(run.cost_usd)}
            {run.priced_steps < run.step_count && " (incomplete cost data)"}</div>
        </Card>
      </li>)}</ol>
    </>}
    {history?.hasMore && <Button onClick={() => setPages(value => value + 1)}>Load older runs</Button>}
    {history && runId && <>
      <Select label="Compare with another run" value={compare} onChange={setCompare}
        options={[{ value: "", label: "No comparison" }, ...history.runs.filter(run => run.run_id !== runId)
          .map(run => ({ value: run.run_id, label: time(run.started_at) + " · " + statusLabel(run.status) + " · " + run.run_id }))]} />
      {detail ? <RunDetail key={runId + ":" + compare} run={detail.run} other={detail.other} names={names} store={store} retry={retry} />
        : !error && <Spinner label="Loading run detail" />}
    </>}
  </>;
}

export function WorkflowHistoryDialog({ store }: { store: SceneStore }) {
  return <Dialog name="run-history" title="Run history" size="lg" className="graph-runner-dialog">
    <DetachedWorkflowInspector />
    <History store={store} />
  </Dialog>;
}
