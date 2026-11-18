import { useReactFlow } from "@xyflow/react";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import type { GraphModelBinding, GraphRun, GraphRunPreview, GraphRunStep, LatestGraphRun, SceneStore } from "../canvas/sceneStore";
import { Dialog } from "../overlays/overlays";
import { downloadBlob } from "../canvas/downloadTextFile";
import { getRunTrace, serializeGraphRunTrace } from "./graphRunTrace";
import "./GraphRunnerDialog.css";

const statuses: Record<string, string> = {
  ready: "Ready", waiting: "Waiting on Model", current: "Up to date", blocked: "Blocked", failed: "Failed",
  pending: "Pending", completed: "Completed", partial: "Completed with issues",
  interrupted: "Interrupted", running: "Running",
  "reconciliation-required": "Reconciliation required",
};
const operations: Record<string, string> = {
  copy: "Copy text", word_count: "Word count", group_sum: "Group and sum",
  model: "Local model reply",
};

function Disclosure({ label, children, className, onOpen, onToggle }: {
  label: string; children: ReactNode; className?: string; onOpen?: () => void; onToggle?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return <div className={className}>
    <button type="button" className="runner-disclosure" aria-expanded={open} onClick={() => {
      setOpen(!open);
      onToggle?.();
      if (!open) onOpen?.();
    }}>
      <span aria-hidden="true">{open ? "▾" : "▸"}</span> {label}
    </button>
    {open && children}
  </div>;
}

function RunSteps({ steps, names }: { steps: GraphRunStep[]; names: Map<string, string> }) {
  return <ol className="runner-steps">
    {steps.map((step, index) => <li key={step.nodeId || index}>
      <div className="runner-step-heading">
        <strong>{step.label || names.get(step.nodeId) || step.nodeId}</strong>
        <span data-status={step.status}>{statuses[step.status] ?? step.status}</span>
      </div>
      <div>{operations[step.operation] ?? step.operation}{step.sourceId && <> from {names.get(step.sourceId) || step.sourceId}</>}</div>
      {step.detail && <p>{step.detail}</p>}
      {step.externalAttempt && <Disclosure label="External request" className="runner-fingerprints">
        <div>Provider <code>{step.externalAttempt.provider}</code></div>
        <div>{step.externalAttempt.kind === "model" ? "Model" : "Tool"} <code>{step.externalAttempt.model || step.externalAttempt.tool}</code></div>
        <div>Input reference <code>{step.externalAttempt.inputRef}</code></div>
        <div>Request ID <code>{step.externalAttempt.requestId}</code></div>
        {step.status === "reconciliation-required" && <p>Check the provider for this request before starting another attempt. It may already have run or incurred a charge.</p>}
      </Disclosure>}
      {(step.outputHash || step.inputHash) && <Disclosure label="Fingerprints" className="runner-fingerprints">
        {step.inputHash && <div>Input <code>{step.inputHash}</code></div>}
        {step.outputHash && <div>Output <code>{step.outputHash}</code></div>}
      </Disclosure>}
    </li>)}
  </ol>;
}

function runDate(run: GraphRun) {
  const date = new Date(run.startedAt);
  return Number.isNaN(date.valueOf()) ? run.runId : date.toLocaleString();
}

function traceTime(value: string | null): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Not recorded" : date.toLocaleString();
}

function traceDuration(value: number | null): string {
  return value === null ? "Not recorded" : `${value.toLocaleString()} ms`;
}

function RunInspector({ run, names, onSelect }: {
  run: GraphRun; names: Map<string, string>; onSelect: (nodeId: string) => void;
}) {
  const trace = getRunTrace(run);
  if (!trace) return null;
  const nodeName = (id: string) => names.get(id) || id || "Unavailable";
  const hash = (value: string | null) => value ?? "Not recorded";
  return <Disclosure label={`Inspect run trace (${trace.steps.length} steps)`} className="runner-inspector">
    <div className="runner-commit">
      <p>Started: {traceTime(trace.startedAt)} · Finished: {traceTime(trace.finishedAt)} · Duration: {traceDuration(trace.durationMs)}</p>
      <button type="button" className="appbar-btn" onClick={() => downloadBlob(
        new Blob([serializeGraphRunTrace(trace)], { type: "application/json" }),
        `graph-run-${trace.runId}.trace.json`,
      )}>Export trace JSON</button>
    </div>
    {trace.steps.length ? <ol className="runner-steps runner-timeline">
      {trace.steps.map((step) => <li key={step.index}>
        <Disclosure className="runner-trace-entry"
          label={`Step ${step.index} · ${nodeName(step.destinationNodeId)} · ${statuses[step.status] ?? step.status}`}
          onToggle={() => onSelect(step.destinationNodeId)}>
          <div>Source: <strong>{nodeName(step.sourceNodeId)}</strong> <code>{step.sourceNodeId || "Unavailable"}</code></div>
          <div>Destination: <strong>{nodeName(step.destinationNodeId)}</strong> <code>{step.destinationNodeId || "Unavailable"}</code></div>
          <div>Operation: {operations[step.operation] ?? step.operation}</div>
          <div>Model: {step.model ? `${step.provider ?? "Provider not recorded"} · ${step.model}` : "Not recorded"}</div>
          <div>Status: <span data-status={step.status}>{statuses[step.status] ?? step.status}</span></div>
          {step.blockedCause && <p>Blocked cause: {step.blockedCause}</p>}
          <div className="runner-fingerprints">
            <div>Input SHA-256: <code>{hash(step.inputFingerprints.input)}</code></div>
            <div>Configuration SHA-256: <code>{hash(step.inputFingerprints.configuration)}</code></div>
            <div>Destination before SHA-256: <code>{hash(step.inputFingerprints.destinationBefore)}</code></div>
            <div>Endpoint SHA-256: <code>{hash(step.inputFingerprints.endpoint)}</code></div>
            <div>Output version SHA-256: <code>{hash(step.outputVersion.sha256)}</code></div>
            <div>Output revision: {step.outputVersion.number ?? "Not recorded"}</div>
          </div>
          <div>Started: {traceTime(step.startedAt)}</div>
          <div>Finished: {traceTime(step.finishedAt)}</div>
          <div>Duration: {traceDuration(step.durationMs)}</div>
          <div className="runner-attempts">
            <strong>Attempt history ({step.attempts.length})</strong>
            {step.attempts.length ? <ol>
              {step.attempts.map((attempt) => <li key={attempt.requestId}>
                <div>Request ID: <code>{attempt.requestId}</code></div>
                <div>Outcome: {statuses[attempt.phase] ?? attempt.phase} (saved phase: {attempt.persistedPhase})</div>
                <div>{attempt.kind === "model" ? "Model" : "Tool"}: {attempt.provider ?? "Unknown provider"} · {attempt.model || attempt.tool || "Unknown"}</div>
                <div>Input SHA-256: <code>{hash(attempt.inputFingerprint)}</code></div>
                <div>Dispatched: {traceTime(attempt.dispatchedAt)}</div>
                <div>Resolved: {traceTime(attempt.resolvedAt)}</div>
              </li>)}
            </ol> : <p>No external request was recorded for this step.</p>}
          </div>
        </Disclosure>
      </li>)}
    </ol> : <p>No saved steps in this run.</p>}
  </Disclosure>;
}

function Runner({ store }: { store: SceneStore }) {
  const rf = useReactFlow();
  function selectInspectorNode(nodeId: string) {
    if (!nodeId || !rf.getNodes().some((node) => node.id === nodeId)) return;
    rf.setNodes((nodes) => nodes.map((node) => ({ ...node, selected: node.id === nodeId })));
  }
  const [selectedIds] = useState(() => rf.getNodes().filter((node) => node.selected).map((node) => node.id));
  const [scope, setScope] = useState(selectedIds.length ? "selection" : "all");
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const names = new Map(scene.nodes.map((node) => [node.id, node.title]));
  const plainNotes = scene.nodes.filter((node) =>
    node.kind === "note" && !node.isTableSource && !node.isSystemPrompt &&
    !node.isSummaryNote && !node.isBranchComparison && !node.tableOutputVersion
  );
  const sources = scene.nodes.filter((node) =>
    node.kind === "chat" || node.kind === "artifact" || node.kind === "model" ||
    (node.kind === "note" && plainNotes.some((note) => note.id === node.id))
  );
  const [modelSourceSelection, setModelSourceSelection] = useState("");
  const [modelTargetSelection, setModelTargetSelection] = useState("");
  const [modelId, setModelId] = useState("");
  const [modelBindings, setModelBindings] = useState<GraphModelBinding[]>([]);
  const [modelPreview, setModelPreview] = useState<(GraphRunPreview & {
    sourceId: string; targetId: string; provider: string; modelId: string; operationId: string;
  }) | null>(null);
  const modelSourceId = sources.some((node) => node.id === modelSourceSelection)
    ? modelSourceSelection : (sources[0]?.id ?? "");
  const eligibleTargets = plainNotes.filter((node) =>
    node.id !== modelSourceId && !node.textInputSourceId
  );
  const modelTargetId = eligibleTargets.some((node) => node.id === modelTargetSelection)
    ? modelTargetSelection : (eligibleTargets[0]?.id ?? "");
  const noteModelBindings = modelBindings.filter((binding) => binding.nodeKind !== "model");
  const modelNodes = modelBindings.filter((binding) => binding.nodeKind === "model");
  const savedModelBinding = noteModelBindings.find((binding) => binding.targetId === modelTargetId);
  const [preview, setPreview] = useState<(GraphRunPreview & { nodeIds: string[]; operationId: string }) | null>(null);
  const [latest, setLatest] = useState<LatestGraphRun | null>(null);
  const [busy, setBusy] = useState("Loading run history…");
  const [error, setError] = useState("");
  const running = latest?.status === "running" || latest?.history?.some((run) => run.status === "running");

  // Opening the panel always re-reads the active graph, including after
  // reconnecting to a run whose execute response never reached this tab.
  useEffect(() => {
    let active = true;
    store.getGraphRun().then((run) => {
      if (active) setLatest(run);
    }).catch((err: unknown) => {
      if (active) setError(err instanceof Error ? err.message : String(err));
    }).finally(() => {
      if (active) setBusy("");
    });
    return () => { active = false; };
  }, [store]);

  useEffect(() => {
    if (busy || !running || error) return;
    let active = true;
    const timer = setTimeout(() => {
      store.getGraphRun().then((run) => {
        if (active) setLatest(run);
      }).catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : String(err));
      });
    }, 1000);
    return () => { active = false; clearTimeout(timer); };
  }, [busy, error, latest, running, store]);

  async function perform(label: string, action: () => Promise<void>) {
    if (busy) return;
    setBusy(label);
    setError("");
    try { await action(); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(""); }
  }

  async function updateRun(result: Promise<GraphRun>) {
    await result;
    setPreview(null);
    // History remains in creation order when an older run resumes. Fetch
    // the authoritative order, including any records the retention cap evicted.
    setLatest(await store.getGraphRun());
  }

  function resumeButton(run: GraphRun) {
    const uncertain = run.status === "reconciliation-required";
    if (run.status !== "interrupted" && !uncertain) return null;
    if (uncertain && !run.steps.some((step) => step.status === "pending")) return null;
    return <div className="runner-commit">
      <p>{uncertain
        ? "Continue only unaffected pending steps. The uncertain external request will not be repeated."
        : "Continue from the last saved step."}</p>
      <button type="button" className="appbar-btn" disabled={!!busy || running} onClick={() =>
        void perform("Resuming run…", () => updateRun(store.resumeGraphRun(run.runId)))
      }>{uncertain ? "Continue safe steps" : "Resume run"}</button>
    </div>;
  }

  function previewRun() {
    void perform("Preparing preview…", async () => {
      setPreview(null);
      const nodeIds = scope === "all" ? [] : selectedIds;
      const plan = await store.previewGraphRun(nodeIds);
      // Keep the same identifier if a request times out after backend commit.
      setPreview({ ...plan, nodeIds, operationId: crypto.randomUUID() });
    });
  }

  function loadModelBindings() {
    void store.getGraphModelBindings().then((rows) => {
      setModelBindings(rows);
      const noteRows = rows.filter((row) => row.nodeKind !== "model");
      if (!modelTargetSelection && noteRows.length) {
        // Reopening the dialog should make a saved connection reusable, not
        // require the user to re-enter the original source/model identity.
        const first = noteRows[0];
        setModelSourceSelection(first.sourceId);
        setModelTargetSelection(first.targetId);
        setModelId(first.modelId);
      }
    }).catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }

  function selectSavedModel(targetId: string) {
    const binding = noteModelBindings.find((row) => row.targetId === targetId);
    if (!binding) return;
    setModelSourceSelection(binding.sourceId);
    setModelTargetSelection(binding.targetId);
    setModelId(binding.modelId);
    setModelPreview(null);
  }

  function previewLocalModel() {
    void perform("Preparing model preview…", async () => {
      setModelPreview(null);
      const provider = "Ollama";
      const prepared = await store.previewGraphModel(modelSourceId, modelTargetId, provider, modelId.trim());
      setModelPreview({
        ...prepared, sourceId: modelSourceId, targetId: modelTargetId,
        provider, modelId: modelId.trim(), operationId: crypto.randomUUID(),
      });
    });
  }

  const ready = preview?.steps.filter((step) => step.status === "ready").length ?? 0;
  return <>
    <p className="runner-intro">Preview a dependency-ordered graph. Model nodes execute explicitly through local Ollama; downstream transforms wait for committed replies. A preview never sends a model request.</p>
    <div className="runner-controls">
      <label>Run scope
        <select value={scope} disabled={!!busy} onChange={(event) => {
          setScope(event.target.value);
          setPreview(null);
          setError("");
        }}>
          <option value="all">Whole graph</option>
          <option value="selection" disabled={!selectedIds.length}>Selected nodes ({selectedIds.length})</option>
        </select>
      </label>
      <button type="button" className="appbar-btn" disabled={!!busy} onClick={previewRun}>Preview run</button>
    </div>
    {scope === "selection" && <p className="runner-note">Includes downstream outputs and the inputs they depend on.</p>}
    <Disclosure label="Run a local model into a note" onOpen={loadModelBindings}>
      {noteModelBindings.length > 0 && <div className="runner-controls">
        <label>Saved model connections
          <select aria-label="Saved model connections" value={savedModelBinding?.targetId ?? ""} disabled={!!busy}
            onChange={(event) => selectSavedModel(event.target.value)}>
            <option value="">Choose a saved connection</option>
            {noteModelBindings.map((binding) => <option key={binding.targetId} value={binding.targetId}>
              {names.get(binding.targetId) || binding.targetId} · {binding.modelId} · {binding.status}
            </option>)}
          </select>
        </label>
      </div>}
      <div className="runner-controls">
        <label>Model text source
          <select aria-label="Model text source" value={modelSourceId} disabled={!!busy} onChange={(event) => {
            setModelSourceSelection(event.target.value); setModelPreview(null);
          }}>
            {sources.map((node) => <option key={node.id} value={node.id}>{node.title || node.id}</option>)}
          </select>
        </label>
        <label>Model output note
          <select aria-label="Model output note" value={modelTargetId} disabled={!!busy} onChange={(event) => {
            const binding = noteModelBindings.find((row) => row.targetId === event.target.value);
            if (binding) selectSavedModel(event.target.value);
            else { setModelTargetSelection(event.target.value); setModelPreview(null); }
          }}>
            {eligibleTargets.map((node) => <option key={node.id} value={node.id}>{node.title || node.id}</option>)}
          </select>
        </label>
        <label>Installed Ollama model ID
          <input aria-label="Installed Ollama model ID" value={modelId} disabled={!!busy}
            placeholder="e.g. llama3.2:3b" onChange={(event) => {
              setModelId(event.target.value); setModelPreview(null);
            }} />
        </label>
        <button type="button" className="appbar-btn" disabled={!!busy || !modelSourceId || !modelId.trim()}
          onClick={() => void perform("Creating Model node…", async () => {
            await store.addModelNode(modelSourceId, "Ollama", modelId.trim());
            setModelBindings(await store.getGraphModelBindings());
            setModelPreview(null);
          })}>Create Model node</button>
        <button type="button" className="appbar-btn" disabled={!!busy || !modelSourceId || !modelTargetId || !modelId.trim()}
          onClick={() => void perform("Saving model connection…", async () => {
            setModelBindings(await store.bindGraphModel(modelSourceId, modelTargetId, "Ollama", modelId.trim()));
            setModelPreview(null);
          })}>Save model connection</button>
        {savedModelBinding && <button type="button" className="appbar-btn" disabled={!!busy} onClick={() =>
          void perform("Disconnecting model…", async () => {
            setModelBindings(await store.unbindGraphModel(modelTargetId));
            setModelPreview(null);
          })
        }>Disconnect model connection</button>}
        <button type="button" className="appbar-btn" disabled={!!busy || !modelSourceId || !modelTargetId || !modelId.trim()}
          onClick={previewLocalModel}>Preview model step</button>
      </div>
      {savedModelBinding && <p className="runner-note" role="status">
        Saved text → text connection (version {savedModelBinding.version}). Status: {savedModelBinding.status}.
        Changes upstream do not trigger execution.
      </p>}
      <p className="runner-note">This sends text to your Ollama daemon over loopback. Verify custom model aliases are local: Ollama itself can route an alias to cloud. Graph Runner does not directly call cloud providers or external tools.</p>
      {modelPreview && <section aria-label="Model preview">
        <RunSteps steps={modelPreview.steps} names={names} />
        <div className="runner-commit">
          <p>The model request is made only after you select Run local model. Its request ID and outcome are saved.</p>
          <button type="button" className="appbar-btn" disabled={!!busy || running} onClick={() =>
            void perform("Running local model…", async () => {
              await updateRun(store.executeGraphModel(
                modelPreview.sourceId, modelPreview.targetId, modelPreview.provider, modelPreview.modelId,
                modelPreview.previewToken, modelPreview.operationId,
              ));
              setModelPreview(null);
              setModelBindings(await store.getGraphModelBindings());
            })
          }>Run local model</button>
        </div>
      </section>}
    </Disclosure>
    {modelNodes.length > 0 && <section aria-label="Model nodes in graph">
      <header className="runner-section-header"><h2>Model nodes</h2><span>{modelNodes.length} connected</span></header>
      <ol className="runner-steps">
        {modelNodes.map((binding) => <li key={binding.targetId}>
          <strong>{names.get(binding.targetId) || binding.targetId}</strong>
          <div>Text input from {names.get(binding.sourceId) || binding.sourceId}</div>
          <div>Ollama · {binding.modelId} · {binding.status}</div>
        </li>)}
      </ol>
    </section>}
    {busy && <p role="status">{busy}</p>}
    {error && <div className="runner-error">
      <p role="alert">{error}</p>
      <button type="button" className="appbar-btn" disabled={!!busy} onClick={() => void perform("Checking run status…", async () => {
        const run = await store.getGraphRun();
        setLatest(run);
        if (run && preview?.operationId === run.runId) setPreview(null);
      })}>Check run status</button>
    </div>}
    {preview && <section aria-label="Run preview">
      <header className="runner-section-header">
        <h2>Run preview</h2>
        <span>{ready} ready</span>
      </header>
      {preview.steps.length ? <RunSteps steps={preview.steps} names={names} /> :
        <p>No connected outputs in this scope. Bind a note's text input or create an output from a data table.</p>}
      {ready > 0 && <div className="runner-commit">
        <p>Running saves this graph and records each finished step.</p>
        <button type="button" className="appbar-btn" disabled={!!busy || running} onClick={() =>
          void perform("Running graph…", () => updateRun(store.executeGraphRun(preview.nodeIds, preview.previewToken, preview.operationId)))
        }>Run graph</button>
      </div>}
    </section>}
    {latest && <section aria-label="Latest run">
      <header className="runner-section-header">
        <h2>Latest run</h2>
        <span role="status" data-status={latest.status}>{statuses[latest.status] ?? latest.status}</span>
      </header>
      <div className="runner-note">{runDate(latest)}</div>
      <RunSteps steps={latest.steps} names={names} />
      <RunInspector run={latest} names={names} onSelect={selectInspectorNode} />
      {latest.status === "reconciliation-required" && <p className="runner-note" role="alert">One or more external requests have an uncertain outcome. Graph Runner will not retry them automatically.</p>}
      {resumeButton(latest)}
      {!!latest.history?.length && <Disclosure label={`Earlier runs (${latest.history.length})`} className="runner-history">
        {latest.history.map((run) => <Disclosure key={run.runId} label={`${runDate(run)} · ${statuses[run.status] ?? run.status}`}>
          <RunSteps steps={run.steps} names={names} />
          <RunInspector run={run} names={names} onSelect={selectInspectorNode} />
          {resumeButton(run)}
        </Disclosure>)}
      </Disclosure>}
    </section>}
  </>;
}

export function GraphRunnerDialog({ store }: { store: SceneStore }) {
  return <Dialog name="graph-runner" title="Graph Runner" className="graph-runner-dialog">
    <Runner store={store} />
  </Dialog>;
}
