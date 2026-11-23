import { useReactFlow, useStoreApi } from "@xyflow/react";
import { lazy, Suspense, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { GraphModelBinding, GraphRun, GraphRunPreview, GraphRunStep, LatestGraphRun, SceneStore } from "../canvas/sceneStore";
import { formatScienceSummary } from "../canvas/scientificWorkbench";
import { Dialog, useOverlays } from "../overlays/overlays";
import { getRunTrace } from "./graphRunTrace";
import { Disclosure } from "./RunnerDisclosure";
import { Button } from "../ui/Button";
import { Select } from "../ui/Select";
import { TextField } from "../ui/TextField";
import { StatusPill } from "../ui/Badge";
import { formatCount, formatRelativeTime } from "../ui/format";
import "./GraphRunnerDialog.css";

const LazyGraphRunnerInspectorContent = lazy(() =>
  import("./GraphRunnerInspector").then((m) => ({ default: m.GraphRunnerInspectorContent })),
);

const statuses: Record<string, string> = {
  ready: "Ready", waiting: "Waiting on upstream", current: "Up to date", blocked: "Blocked", failed: "Failed",
  cancelled: "Cancelled",
  pending: "Pending", completed: "Completed", partial: "Completed with issues",
  interrupted: "Interrupted", running: "Running",
  "reconciliation-required": "Reconciliation required",
};
const operations: Record<string, string> = {
  copy: "Copy text", word_count: "Word count", group_sum: "Group and sum",
  model: "Local model reply",
};

function RunSteps({ steps, names }: { steps: GraphRunStep[]; names: Map<string, string> }) {
  return <ol className="runner-steps">
    {steps.map((step, index) => <li key={step.nodeId || index}>
      <div className="runner-step-heading">
        <strong>{step.label || names.get(step.nodeId) || step.nodeId}</strong>
        <span data-status={step.status}>{statuses[step.status] ?? step.status}</span>
      </div>
      <div>{step.kind === "scientific" ? `Scientific · ${step.operation}` : (operations[step.operation] ?? step.operation)}
        {(step.sourceIds?.length ? step.sourceIds : step.sourceId ? [step.sourceId] : []).length > 0 &&
          <> from {(step.sourceIds?.length ? step.sourceIds : [step.sourceId])
            .map((id) => names.get(id) || id).join(", ")}</>}
      </div>
      {step.kind === "scientific" && <div>
        {step.elapsedMs !== undefined && <>Elapsed: {formatCount(step.elapsedMs)} ms. </>}
        {step.errorType && <>Error type: <strong>{step.errorType}</strong></>}
        {step.scientificResult && <p>Result: {formatScienceSummary(step.scientificResult.summary)}</p>}
      </div>}
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
  return Number.isNaN(date.valueOf()) ? run.runId : formatRelativeTime(date);
}

function RunInspector({ run, names, onSelect }: {
  run: GraphRun; names: Map<string, string>; onSelect: (nodeId: string) => void;
}) {
  const trace = getRunTrace(run);
  if (!trace) return null;
  return <Disclosure label={`Inspect run trace (${trace.steps.length} steps)`} className="runner-inspector">
    <Suspense fallback={<p role="status">Loading run trace…</p>}>
      <LazyGraphRunnerInspectorContent
        trace={trace} names={names} onSelect={onSelect}
        statuses={statuses} operations={operations}
      />
    </Suspense>
  </Disclosure>;
}

function Runner({ store }: { store: SceneStore }) {
  const rf = useReactFlow();
  const rfStore = useStoreApi();
  function selectInspectorNode(nodeId: string) {
    // The canvas owns an uncontrolled React Flow node store; use its same
    // setter (SceneCanvas's scene-sync effect also calls setNodes here).
    const nodes = rfStore.getState().nodes;
    if (!nodeId || !nodes.some((node) => node.id === nodeId)) return;
    rfStore.getState().setNodes(nodes.map((node) => ({ ...node, selected: node.id === nodeId })));
  }
  const requestedIds = useSyncExternalStore(store.subscribe, store.getGraphRunnerRequestedIds);
  const [appliedRequestedIds, setAppliedRequestedIds] = useState(requestedIds);
  const [selectedIds, setSelectedIds] = useState<string[]>(() =>
    requestedIds ? [...requestedIds] : rf.getNodes().filter((node) => node.selected).map((node) => node.id));
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
  const modelNoteHandoff = !!requestedIds?.some((id) => plainNotes.some((node) =>
    node.id === id && !node.textInputSourceId && !node.tableInputSourceId,
  ));
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
  const noteModelBindings = (modelBindings ?? []).filter((binding) => binding.nodeKind !== "model");
  const modelNodes = (modelBindings ?? []).filter((binding) => binding.nodeKind === "model");
  const savedModelBinding = noteModelBindings.find((binding) => binding.targetId === modelTargetId);
  const [preview, setPreview] = useState<(GraphRunPreview & { nodeIds: string[]; operationId: string }) | null>(null);
  const [latest, setLatest] = useState<LatestGraphRun | null>(null);
  const [busy, setBusy] = useState("Loading run history…");
  const [cancellingRun, setCancellingRun] = useState(false);
  const [error, setError] = useState("");
  // A new handoff is a stable store snapshot. Reconcile it during render,
  // like OrganizationDialog, so scope/selection cannot briefly show a stale
  // preview. Selecting bound nodes never starts execution or grants approval.
  if (requestedIds !== appliedRequestedIds) {
    setAppliedRequestedIds(requestedIds);
    if (requestedIds !== null) {
      setSelectedIds([...requestedIds]);
      setScope("selection");
      setPreview(null);
      setError("");
    }
  }
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

  // Saved Model→Note bindings have their OWN existing Graph Runner model
  // preview/approval panel. Selecting the note in a graph-run preview would
  // otherwise show no steps, since it is not a first-class Model node.
  useEffect(() => {
    if (!modelNoteHandoff || !requestedIds) return;
    let active = true;
    void store.getGraphModelBindings().then((rows) => {
      if (!active) return;
      setModelBindings(rows ?? []);
      const bound = (rows ?? []).find((row) =>
        row.nodeKind !== "model" && requestedIds.includes(row.targetId));
      if (bound) {
        setModelSourceSelection(bound.sourceId);
        setModelTargetSelection(bound.targetId);
        setModelId(bound.modelId);
      }
    }).catch((err: unknown) => {
      if (active) setError(err instanceof Error ? err.message : String(err));
    });
    return () => { active = false; };
  }, [store, modelNoteHandoff, requestedIds]);
  // Poll in-flight requests to expose the backend's cancellable running state.
  // Do not invent a local run: a lost reply must remain retryable by operation ID.
  useEffect(() => {
    if (!(running || busy === "Running graph…" || busy === "Resuming run…") || error) return;
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
      <Button variant="secondary" disabled={!!busy || running} onClick={() =>
        void perform("Resuming run…", () => updateRun(
          store.resumeGraphRun(run.runId, run.steps.some((step) => step.kind === "scientific")),
        ))
      }>{uncertain ? "Continue safe steps" : "Resume run"}</Button>
    </div>;
  }

  function cancelButton(run: GraphRun) {
    if (run.status !== "running") return null;
    return <div className="runner-commit">
      <Button variant="secondary" disabled={cancellingRun}
        onClick={() => void cancelActiveRun(run.runId)}>
        {cancellingRun ? "Stopping run…" : "Cancel run"}
      </Button>
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
      setModelBindings(rows ?? []);
      const noteRows = (rows ?? []).filter((row) => row.nodeKind !== "model");
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

  async function cancelActiveRun(runId: string) {
    if (cancellingRun) return;
    setCancellingRun(true);
    setError("");
    try {
      const answer = await store.cancelGraphRun(runId);
      if (!answer.ok) throw new Error("The run has already finished or is not active.");
      setLatest(await store.getGraphRun());
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setCancellingRun(false); }
  }

  const ready = preview?.steps.filter((step) => step.status === "ready").length ?? 0;
  return <>
    <p className="runner-intro">Preview a dependency-ordered graph. Local Model nodes and explicitly configured Scientific Workbench operations run only after approval; every connected input must be ready. Scientific calculations use a bounded, cancellable local worker. Preview never executes either.</p>
    <div className="runner-controls">
      <Select label="Run scope" value={scope} disabled={!!busy}
        onChange={(value) => { setScope(value); setPreview(null); setError(""); }}
        options={[
          { value: "all", label: "Whole graph" },
          { value: "selection", label: `Selected nodes (${selectedIds.length})`, disabled: !selectedIds.length },
        ]} />
      <Button variant="secondary" disabled={!!busy} onClick={previewRun}>Preview run</Button>
    </div>
    {scope === "selection" && <p className="runner-note">Includes downstream outputs and the inputs they depend on.</p>}
    {modelNoteHandoff && <p className="runner-note">The selected note has a saved local-model
      input. Review it below with Preview model step; a separate approval is required to run it.</p>}
    <Disclosure label="Run a local model into a note" onOpen={loadModelBindings}
      initiallyOpen={modelNoteHandoff}>
      {noteModelBindings.length > 0 && <div className="runner-controls">
        <Select label="Saved model connections" ariaLabel="Saved model connections"
          value={savedModelBinding?.targetId ?? ""} disabled={!!busy} onChange={selectSavedModel}
          options={[{ value: "", label: "Choose a saved connection" },
            ...noteModelBindings.map((binding) => ({
              value: binding.targetId,
              label: `${names.get(binding.targetId) || binding.targetId} · ${binding.modelId} · ${binding.status}`,
            }))]} />
      </div>}
      <div className="runner-controls">
        <Select label="Model text source" ariaLabel="Model text source"
          value={modelSourceId} disabled={!!busy}
          onChange={(value) => { setModelSourceSelection(value); setModelPreview(null); }}
          options={sources.map((node) => ({ value: node.id, label: node.title || node.id }))} />
        <Select label="Model output note" ariaLabel="Model output note"
          value={modelTargetId} disabled={!!busy}
          onChange={(value) => {
            const binding = noteModelBindings.find((row) => row.targetId === value);
            if (binding) selectSavedModel(value);
            else { setModelTargetSelection(value); setModelPreview(null); }
          }}
          options={eligibleTargets.map((node) => ({ value: node.id, label: node.title || node.id }))} />
        <TextField label="Installed Ollama model ID" aria-label="Installed Ollama model ID"
          value={modelId} disabled={!!busy} placeholder="e.g. llama3.2:3b"
          onValueChange={(value) => { setModelId(value); setModelPreview(null); }} />
        <Button variant="secondary" disabled={!!busy || !modelSourceId || !modelId.trim()}
          onClick={() => void perform("Creating Model node…", async () => {
            await store.addModelNode(modelSourceId, "Ollama", modelId.trim());
            setModelBindings((await store.getGraphModelBindings()) ?? []);
            setModelPreview(null);
          })}>Create Model node</Button>
        <Button variant="secondary" disabled={!!busy || !modelSourceId || !modelTargetId || !modelId.trim()}
          onClick={() => void perform("Saving model connection…", async () => {
            setModelBindings((await store.bindGraphModel(modelSourceId, modelTargetId, "Ollama", modelId.trim())) ?? []);
            setModelPreview(null);
          })}>Save model connection</Button>
        {savedModelBinding && <Button variant="secondary" disabled={!!busy} onClick={() =>
          void perform("Disconnecting model…", async () => {
            setModelBindings((await store.unbindGraphModel(modelTargetId)) ?? []);
            setModelPreview(null);
          })
        }>Disconnect model connection</Button>}
        <Button variant="secondary" disabled={!!busy || !modelSourceId || !modelTargetId || !modelId.trim()}
          onClick={previewLocalModel}>Preview model step</Button>
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
          <Button variant="secondary" disabled={!!busy || running} onClick={() =>
            void perform("Running local model…", async () => {
              await updateRun(store.executeGraphModel(
                modelPreview.sourceId, modelPreview.targetId, modelPreview.provider, modelPreview.modelId,
                modelPreview.previewToken, modelPreview.operationId,
              ));
              setModelPreview(null);
              setModelBindings((await store.getGraphModelBindings()) ?? []);
            })
          }>Run local model</Button>
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
      <Button variant="secondary" disabled={!!busy} onClick={() => void perform("Checking run status…", async () => {
        const run = await store.getGraphRun();
        setLatest(run);
        if (run && preview?.operationId === run.runId) setPreview(null);
      })}>Check run status</Button>
    </div>}
    {preview && <section aria-label="Run preview">
      <header className="runner-section-header">
        <h2>Run preview</h2>
        <span>{ready} ready</span>
      </header>
      {preview.steps.length ? <RunSteps steps={preview.steps} names={names} /> :
        <p>No connected outputs in this scope. Bind a note's text input, create a table output, or save a Scientific Workbench operation for Graph Runner.</p>}
      {ready > 0 && <div className="runner-commit">
        <p>Running saves this graph and records each finished step.</p>
        <Button variant="secondary" disabled={!!busy || running} onClick={() =>
          void perform("Running graph…", () => updateRun(store.executeGraphRun(
            preview.nodeIds, preview.previewToken, preview.operationId,
            preview.steps.some((step) => step.kind === "scientific"),
          )))
        }>Run graph</Button>
      </div>}
    </section>}
    {latest && <section aria-label="Latest run">
      <header className="runner-section-header">
        <h2>Latest run</h2>
        <span role="status" data-status={latest.status}>
          <StatusPill status={latest.status === "completed" ? "Done"
            : latest.status === "running" ? "Running" : latest.status === "cancelled" ? "Cancelled"
            : latest.status === "failed" || latest.status === "reconciliation-required" ? "Failed"
            : "Out of date"} />
          {statuses[latest.status] ?? latest.status}
        </span>
      </header>
      <div className="runner-note">{runDate(latest)}</div>
      {cancelButton(latest)}
      <RunSteps steps={latest.steps} names={names} />
      <RunInspector run={latest} names={names} onSelect={selectInspectorNode} />
      {latest.status === "reconciliation-required" && <p className="runner-note" role="alert">One or more external requests have an uncertain outcome. Graph Runner will not retry them automatically.</p>}
      {resumeButton(latest)}
      {!!latest.history?.length && <Disclosure label={`Earlier runs (${latest.history.length})`} className="runner-history">
        {latest.history.map((run) => <Disclosure key={run.runId} label={`${runDate(run)} · ${statuses[run.status] ?? run.status}`}>
          <RunSteps steps={run.steps} names={names} />
          <RunInspector run={run} names={names} onSelect={selectInspectorNode} />
          {cancelButton(run)}
          {resumeButton(run)}
        </Disclosure>)}
      </Disclosure>}
    </section>}
  </>;
}

export function GraphRunnerDialog({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  const wasOpen = useRef(false);
  useEffect(() => {
    const isOpen = overlays.isOpen("graph-runner");
    // Runner itself unmounts while Dialog is closed. Clear the handoff at
    // the parent level on an actual open -> closed transition, not on the
    // initial closed mount (which might precede a queued badge handoff).
    if (wasOpen.current && !isOpen) store.clearGraphRunnerRequestedIds();
    wasOpen.current = isOpen;
  }, [overlays, store]);
  return <Dialog name="graph-runner" title="Graph runner" size="lg" className="graph-runner-dialog">
    <Runner store={store} />
  </Dialog>;
}
