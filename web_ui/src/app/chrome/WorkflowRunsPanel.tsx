/* eslint-disable jsx-a11y/no-noninteractive-tabindex -- output and input values are scrollable, read-only regions that must be keyboard focusable (WCAG 2.1.1), not button or link stand-ins. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Badge, StatusPill } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Disclosure } from "../ui/Disclosure";
import { EmptyState } from "../ui/EmptyState";
import { InlineAlert } from "../ui/InlineAlert";
import { stepName, WorkflowPreviewSummary } from "./WorkflowPreviewSummary";
import { WorkflowRunLauncher } from "./WorkflowRunLauncher";
import {
  adapterLabel, effectLabel, effectSentence, elapsed, errorText, money, plural, runWaiting, statusText, statusWord,
  valueSummary, valueText, when, workflowRequest, workspacePath,
  type DetachedRun, type Preset, type RunEvent, type RunStep,
} from "./workflowApi";
import { downloadStoredValue, externalValueSummary, isExternalizedValue, type ExternalizedValue, type StoredPortValue } from "./workflowRunValues";

function RunStatus({ run }: { run: DetachedRun }) {
  const word = statusWord(run.status, runWaiting(run));
  return word ? <StatusPill status={word} announce={false} /> : <Badge>{statusText(run.status)}</Badge>;
}

function ExternalValueBlock({ name, value, runPath }: { name: string; value: ExternalizedValue; runPath: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => { request.current?.abort(); }, []);
  async function download() {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError("");
    try { await downloadStoredValue(runPath, value, controller.signal); }
    catch (err) { if (!controller.signal.aborted) setError(errorText(err)); }
    finally {
      request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return <div className="workflow-value">
    <div className="workflow-value-head">
      <span className="workflow-value-name">{stepName(name)}</span>
      <span className="workflow-muted">{value.kind}</span>
    </div>
    <p className="workflow-value-summary">{externalValueSummary(value)}</p>
    <Button size="sm" variant="secondary" loading={busy} disabled={busy}
      onClick={() => void download()}>Download {stepName(name).toLowerCase()} (JSON)</Button>
    {error && <InlineAlert variant="error" title="Couldn't download stored value" body={error} />}
  </div>;
}

function ValueBlock({ name, value, runPath, open = false }: { name: string; value: StoredPortValue; runPath: string; open?: boolean }) {
  if (isExternalizedValue(value)) {
    return <ExternalValueBlock key={runPath + ":" + value.kind + ":" + value.asset_ref + ":" + value.size_bytes}
      name={name} value={value} runPath={runPath} />;
  }
  const text = valueText(value);
  const long = text.length > 280 || text.split("\n").length > 6;
  const preClass = "workflow-pre" + (value.kind === "text" ? " is-text" : "");
  return <div className="workflow-value">
    <div className="workflow-value-head">
      <span className="workflow-value-name">{stepName(name)}</span>
      <span className="workflow-muted">{value.kind}</span>
    </div>
    {long && !open
      ? <><p className="workflow-value-summary">{valueSummary(value)}</p>
          <Disclosure title={"Show full " + stepName(name).toLowerCase()}>
            <pre className={preClass} tabIndex={0} role="region" aria-label={stepName(name)}>{text}</pre>
          </Disclosure></>
      : <pre className={preClass} tabIndex={0} role="region" aria-label={stepName(name)}>{text || "Empty"}</pre>}
  </div>;
}

const eventCopy: Record<string, string> = {
  "run-created": "Run created", "run-started": "Run started", "run-completed": "Run completed",
  "run-failed": "Run failed", "run-cancelled": "Run cancelled", "run-interrupted": "Run interrupted",
  "step-started": "Started", "step-completed": "Completed", "step-failed": "Failed", "step-blocked": "Blocked",
  "step-cancelled": "Cancelled", "step-interrupted": "Interrupted", "step-progress": "Update",
};
function eventLine(event: RunEvent) {
  const what = eventCopy[event.kind] ?? event.kind;
  return (event.step_id ? stepName(event.step_id) + ": " + what.toLowerCase() : what) + (event.message ? " · " + event.message : "");
}

function StepRow({ id, step, adapter, runPath, gate, paused = false }: {
  id: string; step: RunStep | undefined; adapter: string; runPath: string; gate?: ReactNode; paused?: boolean;
}) {
  const status = step?.status ?? "pending";
  const waiting = paused || (step?.approval?.status === "pending" && (status === "pending" || status === "running"));
  const word = statusWord(status, waiting);
  const took = elapsed(step?.started_at, step?.finished_at);
  const outputs = Object.entries(step?.outputs ?? {});
  const items = Object.values(step?.map_items ?? {});
  return <li className="workflow-step">
    <span className="workflow-step-status">{word ? <StatusPill status={word} announce={false} /> : <Badge>{statusText(status)}</Badge>}</span>
    <div className="workflow-step-body">
      <div className="workflow-step-heading">
        <strong>{stepName(id)}</strong>
        <span className="workflow-muted">{adapterLabel(adapter)}{step?.side_effect_class ? " · " + effectLabel(step.side_effect_class) : ""}</span>
        <span className="workflow-step-numbers">{[took, step?.cost_usd ? money(step.cost_usd) : null].filter(Boolean).join(" · ")}</span>
      </div>
      {step?.error && status !== "cancelled" && <p className="workflow-step-error">{step.error}</p>}
      {step?.approval && <p className="workflow-muted">Approval: {step.approval.message || statusText(step.approval.status)}</p>}
      {gate}
      {!!step?.skipped_outputs?.length && <p className="workflow-muted">Skipped: {step.skipped_outputs.map(stepName).join(", ")}</p>}
      {items.length > 0 && <p className="workflow-muted">
        {plural(items.length, "item")} · {items.filter(item => item.status === "completed").length} done
        {items.some(item => item.status === "failed") && ` · ${items.filter(item => item.status === "failed").length} failed`}
      </p>}
      {outputs.length > 0 && <Disclosure title={"Outputs (" + outputs.length + ")"}>
        <div className="workflow-values">{outputs.map(([name, value]) => <ValueBlock key={name} name={name} value={value} runPath={runPath} />)}</div>
      </Disclosure>}
    </div>
  </li>;
}

function RunDetail({ run, workspace, presetName, onChanged }: {
  run: DetachedRun; workspace: number; presetName: string | null; onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const base = workspacePath(workspace) + "/runs/" + encodeURIComponent(run.run_id);
  async function act(fn: () => Promise<unknown>) {
    statusRef.current?.focus();
    setBusy(true); setError("");
    try { await fn(); onChanged(); } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  const pause = run.status === "running" ? run.awaiting_approval : null;
  const gates = run.status === "running" ? Object.values(run.steps).filter(step => step.approval?.status === "pending") : [];
  const waitingNames = [...(pause ? [pause.step_id] : []), ...gates.map(step => step.step_id)].map(stepName);
  const pausedPolicy = pause ? run.preview.steps.find(([id]) => id === pause.step_id)?.[2] : undefined;
  const outputs = Object.entries(run.outputs ?? {});
  const inputs = Object.entries(run.inputs ?? {});
  const took = elapsed(run.started_at ?? run.created_at, run.finished_at);
  return <section className="workflow-run" aria-label="Run detail">
    <header className="workflow-detail-header">
      <div className="workflow-detail-title">
        <h3>{run.spec.name}</h3>
        <RunStatus run={run} />
      </div>
      <div className="workflow-actions">
        {(run.status === "running" || run.status === "pending") &&
          <Button variant="secondary" disabled={busy} onClick={() => void act(() => workflowRequest(base + "/cancel", { method: "POST" }))}>Cancel run</Button>}
        {run.status === "completed" && !saved && <Button variant="secondary" iconStart="document-text" disabled={busy}
          onClick={() => void act(async () => { await workflowRequest(base + "/save-as-graph", { method: "POST", body: {} }); setSaved(true); })}>
          Save result as graph</Button>}
      </div>
    </header>
    <p className="workflow-detail-meta">
      Started {when(run.started_at ?? run.created_at)}{took && " · took " + took} · cost {money(run.cost_usd)}
      {" · "}{presetName ? "From preset “" + presetName + "”" : run.preset_id ? "From a deleted preset" : "Started by you"}
    </p>
    <p ref={statusRef} tabIndex={-1} role="status" className="visually-hidden">
      {busy ? "Sending your decision…" : run.spec.name + ": " + (waitingNames.length ? "Waiting for approval: " + waitingNames.join(", ") : statusText(run.status))}
    </p>
    {error && <InlineAlert variant="error" title="That didn't go through" body={error} />}
    {saved && <InlineAlert variant="success" title="Saved as a new graph" body="Open it from the chat library." />}
    {run.error && !pause && run.status !== "completed" && <InlineAlert variant={run.status === "cancelled" ? "info" : "warning"}
      title={run.status === "cancelled" ? "Cancelled" : "Why the run stopped"} body={run.error} />}
    {pause && <div className="workflow-approval" role="group" aria-label={"Review " + stepName(pause.step_id)}>
      <InlineAlert variant="warning" title={"Review before “" + stepName(pause.step_id) + "” runs"}
        body={<>This step {effectSentence(pause.side_effect_class)}
          {pausedPolicy?.model_identity && <>, using {pausedPolicy.model_identity.join(" · ")}</>}. Nothing in it has run yet.
          {run.unattended_effects?.length
            ? <> This preset only lets {run.unattended_effects.map(effect => effectLabel(effect).toLowerCase()).join(", ")} steps run without you.</>
            : null}</>} />
      <div className="workflow-actions">
        <Button variant="primary" disabled={busy} onClick={() => void act(() => workflowRequest(base + "/step-decision",
          { method: "POST", body: { step_id: pause.step_id, nonce: pause.nonce, approved: true } }))}>Approve this step once</Button>
        <Button variant="secondary" destructive disabled={busy} onClick={() => void act(() => workflowRequest(base + "/step-decision",
          { method: "POST", body: { step_id: pause.step_id, nonce: pause.nonce, approved: false } }))}>Deny and stop run</Button>
      </div>
    </div>}
    {outputs.length > 0 && <section className="workflow-card" aria-label="Result">
      <h4>Result</h4>
      <div className="workflow-values">{outputs.map(([name, value]) => <ValueBlock key={name} name={name} value={value} runPath={base} open />)}</div>
    </section>}
    <section aria-label="Steps">
      <h4>Steps</h4>
      <ol className="workflow-steps">
        {run.spec.steps.map(spec => {
          const step = run.steps[spec.step_id];
          const gate = gates.find(item => item.step_id === spec.step_id);
          return <StepRow key={spec.step_id} id={spec.step_id} step={step} adapter={spec.adapter.adapter_id} runPath={base}
            paused={pause?.step_id === spec.step_id}
            gate={gate && <div className="workflow-actions">
              <Button size="sm" variant="primary" disabled={busy} onClick={() => void act(() => workflowRequest(base + "/step-approval",
                { method: "POST", body: { step_id: gate.step_id, approved: true } }))}>Approve step</Button>
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => void act(() => workflowRequest(base + "/step-approval",
                { method: "POST", body: { step_id: gate.step_id, approved: false } }))}>Reject step</Button>
            </div>} />;
        })}
      </ol>
    </section>
    {inputs.length > 0 && <Disclosure title={"Inputs (" + inputs.length + ")"}>
      <div className="workflow-values">{inputs.map(([name, value]) => <ValueBlock key={name} name={name} value={value} runPath={base} />)}</div>
    </Disclosure>}
    <Disclosure title="Reviewed preview"><WorkflowPreviewSummary preview={run.preview} spec={run.spec} label="Reviewed preview" /></Disclosure>
    {!!run.events?.length && <Disclosure title={"Event log (" + run.events.length + ")"}>
      <ol className="workflow-events">{run.events.map(event => <li key={event.sequence}>
        <span className="workflow-muted">{when(event.timestamp)}</span> {eventLine(event)}
      </li>)}</ol>
    </Disclosure>}
  </section>;
}

export function WorkflowRunsPanel({ workspace, runs, loaded, presets, selected, onSelect, onChanged, toolbar }: {
  workspace: number | null; runs: DetachedRun[]; loaded: boolean; presets: Preset[];
  selected: string; onSelect: (runId: string) => void; onChanged: () => void; toolbar?: ReactNode;
}) {
  const [custom, setCustom] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const focusHeading = useRef(false);
  const run = runs.find(item => item.run_id === selected);
  useEffect(() => {
    if (focusHeading.current) { focusHeading.current = false; heading.current?.focus(); }
  }, [selected, custom]);
  const presetName = (id: string | null | undefined) => presets.find(preset => preset.preset_id === id)?.name ?? null;
  const back = () => { focusHeading.current = true; onSelect(""); setCustom(false); };

  if (custom && workspace !== null) {
    return <div className="workflow-panel">
      <Button variant="ghost" iconStart="chevron-left" className="workflow-back" onClick={back}>All runs</Button>
      <h3 ref={heading} tabIndex={-1}>Run from a JSON request</h3>
      <p className="workflow-muted">For a spec you wrote or exported. Library workflows are easier to run from their page in the Workflows tab.</p>
      <WorkflowRunLauncher jsonOnly workspace={workspace} previewLabel="Workflow preview"
        onStarted={runId => { setCustom(false); onSelect(runId); onChanged(); }} />
    </div>;
  }
  if (selected) {
    return <div className="workflow-panel">
      <Button variant="ghost" iconStart="chevron-left" className="workflow-back" onClick={back}>All runs</Button>
      <h3 ref={heading} tabIndex={-1} className="visually-hidden">Selected run</h3>
      {run && workspace !== null
        ? <RunDetail key={workspace + ":" + run.run_id} run={run} workspace={workspace} presetName={presetName(run.preset_id)} onChanged={onChanged} />
        : <p className="workflow-muted" role="status">Loading the run…</p>}
    </div>;
  }
  const waiting = runs.filter(runWaiting).length;
  return <div className="workflow-panel">
    <div className="workflow-toolbar">
      <h3 ref={heading} tabIndex={-1} className="workflow-toolbar-title">Runs</h3>
      <span className="workflow-muted" role="status">
        {loaded ? plural(runs.length, "run") + (waiting ? " · " + waiting + " waiting for you" : "") : "Loading runs…"}
      </span>
      <span className="workflow-toolbar-end">
        {toolbar}
        <Button variant="secondary" iconStart="file-code" disabled={workspace === null} onClick={() => { focusHeading.current = true; setCustom(true); }}>Run from JSON</Button>
      </span>
    </div>
    {loaded && !runs.length && <EmptyState icon="play" title="No runs yet"
      body="Open a workflow in the Workflows tab and run it, or run a preset. Each run's steps and results are kept here." />}
    {!!runs.length && <ul className="workflow-list" aria-label="Runs">
      {runs.map(item => {
        const took = elapsed(item.started_at ?? item.created_at, item.finished_at);
        const from = presetName(item.preset_id);
        return <li key={item.run_id} className="workflow-row">
          <button type="button" className="workflow-row-main" onClick={() => { focusHeading.current = true; onSelect(item.run_id); }}
            aria-label={`${item.spec.name}, ${runWaiting(item) ? "waiting for approval" : statusText(item.status).toLowerCase()}, ${when(item.created_at)}`}>
            <span className="workflow-row-status"><RunStatus run={item} /></span>
            <span className="workflow-row-copy">
              <span className="workflow-row-title">{item.spec.name}</span>
              <span className="workflow-row-meta">
                {when(item.created_at)}{took && " · " + took} · {money(item.cost_usd)}
                {from ? " · preset “" + from + "”" : item.preset_id ? " · deleted preset" : ""}
              </span>
            </span>
          </button>
        </li>;
      })}
    </ul>}
  </div>;
}
