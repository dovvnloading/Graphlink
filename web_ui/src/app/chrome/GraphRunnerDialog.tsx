import { useReactFlow } from "@xyflow/react";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import type { GraphRun, GraphRunPreview, GraphRunStep, LatestGraphRun, SceneStore } from "../canvas/sceneStore";
import { Dialog } from "../overlays/overlays";
import "./GraphRunnerDialog.css";

const statuses: Record<string, string> = {
  ready: "Ready", current: "Up to date", blocked: "Blocked", failed: "Failed",
  pending: "Pending", completed: "Completed", partial: "Completed with issues",
  interrupted: "Interrupted", running: "Running",
  "reconciliation-required": "Reconciliation required",
};
const operations: Record<string, string> = {
  copy: "Copy text", word_count: "Word count", group_sum: "Group and sum",
  model: "Local model reply",
};

function Disclosure({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  return <div className={className}>
    <button type="button" className="runner-disclosure" aria-expanded={open} onClick={() => setOpen(!open)}>
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

function Runner({ store }: { store: SceneStore }) {
  const rf = useReactFlow();
  const [selectedIds] = useState(() => rf.getNodes().filter((node) => node.selected).map((node) => node.id));
  const [scope, setScope] = useState(selectedIds.length ? "selection" : "all");
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const names = new Map(scene.nodes.map((node) => [node.id, node.title]));
  const plainNotes = scene.nodes.filter((node) =>
    node.kind === "note" && !node.isTableSource && !node.isSystemPrompt &&
    !node.isSummaryNote && !node.isBranchComparison && !node.tableOutputVersion
  );
  const sources = scene.nodes.filter((node) =>
    node.kind === "chat" || node.kind === "artifact" ||
    (node.kind === "note" && plainNotes.some((note) => note.id === node.id))
  );
  const [modelSourceSelection, setModelSourceSelection] = useState("");
  const [modelTargetSelection, setModelTargetSelection] = useState("");
  const [modelId, setModelId] = useState("");
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
    <p className="runner-intro">Refresh connected notes, grouped tables, charts and reports in dependency order.</p>
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
    <Disclosure label="Run a local model into a note">
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
            setModelTargetSelection(event.target.value); setModelPreview(null);
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
        <button type="button" className="appbar-btn" disabled={!!busy || !modelSourceId || !modelTargetId || !modelId.trim()}
          onClick={previewLocalModel}>Preview model step</button>
      </div>
      <p className="runner-note">This sends the selected text to an Ollama model you run locally. Cloud models and tools are not enabled in Graph Runner.</p>
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
            })
          }>Run local model</button>
        </div>
      </section>}
    </Disclosure>
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
      {latest.status === "reconciliation-required" && <p className="runner-note" role="alert">One or more external requests have an uncertain outcome. Graph Runner will not retry them automatically.</p>}
      {resumeButton(latest)}
      {!!latest.history?.length && <Disclosure label={`Earlier runs (${latest.history.length})`} className="runner-history">
        {latest.history.map((run) => <Disclosure key={run.runId} label={`${runDate(run)} · ${statuses[run.status] ?? run.status}`}>
          <RunSteps steps={run.steps} names={names} />
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
