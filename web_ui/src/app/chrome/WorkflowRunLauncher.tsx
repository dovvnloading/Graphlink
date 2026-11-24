import { useState, type ReactNode } from "react";
import type { PortSpec, PortValue, WorkflowSpec } from "../../lib/workflow-contract/generated/workflow";
import { Button } from "../ui/Button";
import { InlineAlert } from "../ui/InlineAlert";
import { Switch } from "../ui/Switch";
import { TextArea } from "../ui/TextArea";
import { stepName, WorkflowPreviewSummary } from "./WorkflowPreviewSummary";
import {
  draftFor, errorText, typedInputs, workflowRequest, workspacePath,
  type DetachedRequest, type InputDrafts, type WorkflowPreview,
} from "./workflowApi";

export function initialDrafts(ports: readonly PortSpec[], examples?: Record<string, PortValue>): InputDrafts {
  return Object.fromEntries(ports.map(port => [port.name, draftFor(port, examples?.[port.name])]));
}

const kindHelp: Record<string, string | undefined> = {
  text: undefined,
  json: "A JSON value, for example {\"limit\": 5} or [1, 2, 3]",
  table: "Comma-separated values. The first row names the columns.",
};

/** One field per workflow input, typed by its port. */
export function WorkflowInputsForm({ ports, drafts, onChange, disabled }: {
  ports: readonly PortSpec[]; drafts: InputDrafts; onChange: (drafts: InputDrafts) => void; disabled?: boolean;
}) {
  if (!ports.length) return <p className="workflow-muted">This workflow takes no inputs.</p>;
  return <div className="workflow-inputs">
    {ports.map(port => {
      const label = stepName(port.name) + (port.required ? "" : " (optional)");
      if (port.kind === "file" || port.kind === "image") {
        return <InlineAlert key={port.name} title={label}
          body={`This form can't attach a ${port.kind} yet. Turn on Edit as JSON to supply one, or use a preset.`} />;
      }
      return <TextArea key={port.name} label={label} description={kindHelp[port.kind]} disabled={disabled}
        minRows={port.kind === "text" ? 3 : 4} maxRows={12} value={drafts[port.name] ?? ""}
        onValueChange={value => onChange({ ...drafts, [port.name]: value })} />;
    })}
  </div>;
}

/** Preview and start a detached run of one workflow version. */
export function WorkflowRunLauncher({ spec, examples, workspace, onStarted, jsonOnly = false, previewLabel, title, note }: {
  spec?: WorkflowSpec; examples?: Record<string, PortValue>; workspace: number | null;
  onStarted: (runId: string) => void; jsonOnly?: boolean; previewLabel?: string; title?: string; note?: ReactNode;
}) {
  const [drafts, setDrafts] = useState<InputDrafts>(() => initialDrafts(spec?.inputs ?? [], examples));
  const [asJson, setAsJson] = useState(jsonOnly);
  const [json, setJson] = useState(() => spec ? JSON.stringify({ spec, parameters: examples ?? {} }, null, 2) : "");
  const [review, setReview] = useState<{ request: DetachedRequest; preview: WorkflowPreview } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function request(): DetachedRequest {
    if (asJson || !spec) {
      let parsed: unknown;
      try { parsed = JSON.parse(json); } catch { throw new Error("The request isn't valid JSON. Check for a missing comma or quote."); }
      if (!parsed || typeof parsed !== "object" || !("spec" in parsed) || !("parameters" in parsed)) {
        throw new Error("The request needs a \"spec\" and a \"parameters\" object.");
      }
      return parsed as DetachedRequest;
    }
    return { spec, parameters: typedInputs(spec.inputs, drafts) };
  }
  async function preview() {
    if (workspace === null) return;
    setBusy(true); setError(""); setReview(null);
    try {
      const body = request();
      setReview({ request: body, preview: await workflowRequest<WorkflowPreview>(workspacePath(workspace) + "/preview", { method: "POST", body }) });
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  async function start() {
    if (!review || workspace === null) return;
    setBusy(true); setError("");
    try {
      const { approval_token } = await workflowRequest<{ approval_token: string }>(workspacePath(workspace) + "/approve",
        { method: "POST", body: { approved: true, fingerprint: review.preview.fingerprint } });
      const { run_id } = await workflowRequest<{ run_id: string }>(workspacePath(workspace) + "/runs",
        { method: "POST", body: { ...review.request, approval_token } });
      setReview(null);
      onStarted(run_id);
    } catch (err) { setError(errorText(err)); setReview(null); }
    finally { setBusy(false); }
  }
  const edit = (fn: () => void) => { fn(); setReview(null); };

  return <div className="workflow-launcher">
    <div className="workflow-card-header">
    {title && <h4>{title}</h4>}
    {!jsonOnly && spec && <Switch label="Edit as JSON" checked={asJson} disabled={busy}
      onCheckedChange={next => {
        edit(() => {
          if (next) {
            try { setJson(JSON.stringify({ spec, parameters: typedInputs(spec.inputs, drafts) }, null, 2)); }
            catch { setJson(JSON.stringify({ spec, parameters: examples ?? {} }, null, 2)); }
          }
          setAsJson(next);
        });
      }} />}
    </div>
    {note}
    {asJson || !spec
      ? <TextArea label="Workflow request JSON (spec and parameters)" disabled={busy} minRows={8} maxRows={20}
          description="A frozen spec and one typed value per input. Only run a spec you trust."
          value={json} onValueChange={value => edit(() => setJson(value))} />
      : <WorkflowInputsForm ports={spec.inputs} drafts={drafts} disabled={busy} onChange={next => edit(() => setDrafts(next))} />}
    {error && <InlineAlert variant="error" title="Couldn't preview or start this run" body={error} />}
    {workspace === null && <InlineAlert title="No workspace" body="Create a workspace in the chat library first. Runs are stored per workspace." />}
    {review
      ? <>
          <WorkflowPreviewSummary preview={review.preview} spec={review.request.spec} label={previewLabel} />
          <div className="workflow-actions">
            <Button variant="primary" loading={busy} onClick={() => void start()}>
              {review.preview.requires_approval ? "Approve preview and start" : "Start reviewed workflow"}
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => setReview(null)}>Change inputs</Button>
          </div>
        </>
      : <div className="workflow-actions">
          <Button variant="secondary" loading={busy} disabled={workspace === null} onClick={() => void preview()}>Preview workflow</Button>
          <span className="workflow-muted">Nothing runs until you review the preview.</span>
        </div>}
  </div>;
}
