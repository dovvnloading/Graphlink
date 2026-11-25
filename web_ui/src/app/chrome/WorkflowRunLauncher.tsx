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
import { draftsForRequest, parseDetachedRequest, requestFromDrafts } from "./workflowRunDrafts";

export function initialDrafts(ports: readonly PortSpec[], examples?: Record<string, PortValue>): InputDrafts {
  return Object.fromEntries(ports.map(port => [port.name, draftFor(port, examples?.[port.name])]));
}

const kindHelp: Record<string, string | undefined> = {
  text: undefined,
  json: "A JSON value, for example {\"limit\": 5} or [1, 2, 3]",
  table: "CSV: first row names columns, quote text (including numeric identifiers), unquoted empty cells are null. Or enter a JSON object with columns and rows for exact types.",
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
  const [asJson, setAsJson] = useState(() => {
    if (jsonOnly) return true;
    if (!spec || !examples) return false;
    // Do not start in a form that would silently drop a typed example (e.g. an asset).
    try { draftsForRequest(spec, { spec, parameters: examples }); return false; }
    catch { return true; }
  });
  const [json, setJson] = useState(() => spec ? JSON.stringify({ spec, parameters: examples ?? {} }, null, 2) : "");
  const [review, setReview] = useState<{ request: DetachedRequest; preview: WorkflowPreview } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [modeError, setModeError] = useState("");

  function request(): DetachedRequest {
    if (asJson || !spec) return parseDetachedRequest(json);
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
  const edit = (fn: () => void) => { fn(); setReview(null); setModeError(""); setError(""); };

  function switchEditor(next: boolean) {
    if (!spec || next === asJson) return;
    try {
      if (next) {
        const body = requestFromDrafts(spec, drafts);
        edit(() => { setJson(JSON.stringify(body, null, 2)); setAsJson(true); });
      } else {
        const nextDrafts = draftsForRequest(spec, parseDetachedRequest(json));
        edit(() => { setDrafts(nextDrafts); setAsJson(false); });
      }
    } catch (err) {
      // A failed conversion leaves the current editor, its draft and review intact.
      setModeError(errorText(err));
    }
  }

  return <div className="workflow-launcher">
    <div className="workflow-card-header">
    {title && <h4>{title}</h4>}
    {!jsonOnly && spec && <Switch label="Edit as JSON" checked={asJson} disabled={busy}
      onCheckedChange={switchEditor} />}
    </div>
    {note}
    {asJson || !spec
      ? <TextArea label="Workflow request JSON (spec and parameters)" disabled={busy} minRows={8} maxRows={20}
          description="A frozen spec and one typed value per input. Only run a spec you trust."
          value={json} onValueChange={value => edit(() => setJson(value))} />
      : <WorkflowInputsForm ports={spec.inputs} drafts={drafts} disabled={busy} onChange={next => edit(() => setDrafts(next))} />}
    {modeError && <InlineAlert variant="error" title="Couldn't switch input editors" body={modeError} />}
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
