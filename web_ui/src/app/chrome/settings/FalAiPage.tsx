import { useState } from "react";
import { useConfirm } from "../../ui/ConfirmDialog";
import { Button } from "../../ui/Button";
import { TextField } from "../../ui/TextField";
import { TextArea } from "../../ui/TextArea";
import { NumberField } from "../../ui/NumberField";
import { Select } from "../../ui/Select";
import { FieldGroup } from "../../ui/Field";
import { Disclosure } from "../../ui/Disclosure";
import { Spinner } from "../../ui/Spinner";
import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";

/** fal.ai is a universal generative media/inference workspace, not a chat LLM mode.
 * All credential-bearing requests stay inside the Python backend. Model inputs
 * and results remain model-specific JSON so new fal endpoints work immediately.
 */
type FalJobSummary = {
  requestId: string;
  modelId: string;
  status: string;
  createdAt?: string;
  updatedAt?: string;
  resultJson?: string;
  mediaUrls?: string[];
};

export function FalAiPage({
  state,
  transport,
}: {
  state: AppSettingsState;
  transport: WsTransport;
}) {
  const askConfirm = useConfirm();
  const [keyDraft, setKeyDraft] = useState("");
  const [modelId, setModelId] = useState("fal-ai/flux/schnell");
  const [argumentsJson, setArgumentsJson] = useState('{"prompt": "A cinematic mountain landscape at sunrise"}');
  const [optionsJson, setOptionsJson] = useState('{"priority": "normal", "store_io": false}');
  const [localPath, setLocalPath] = useState("");
  const [fileField, setFileField] = useState("image_url");
  const [modelSearch, setModelSearch] = useState("");
  const [modelCategory, setModelCategory] = useState("");
  const [estimateQuantity, setEstimateQuantity] = useState(1);
  const [estimateBasis, setEstimateBasis] = useState("historical_api_price");
  let discovery: { kind: string; data?: unknown; truncated?: boolean } | null = null;
  try {
    const parsed: unknown = JSON.parse(state.falDiscoveryJson || "null");
    if (parsed && typeof parsed === "object" && "kind" in parsed) {
      discovery = parsed as { kind: string; data?: unknown; truncated?: boolean };
    }
  } catch {
    // Ignore malformed transient discovery JSON without crashing Settings.
  }
  type FalModelEntry = { endpoint_id: string; metadata?: {
    display_name?: string; category?: string; description?: string; status?: string;
  }};
  const discovered = discovery?.kind === "models" && discovery.data &&
    typeof discovery.data === "object" ? discovery.data as {
      models?: FalModelEntry[]; has_more?: boolean; next_cursor?: string;
    } : null;
  const [localNotice, setLocalNotice] = useState("");
  let jobs: FalJobSummary[] = [];
  try {
    const decoded: unknown = JSON.parse(state.falJobsJson || "[]");
    if (Array.isArray(decoded)) jobs = decoded as FalJobSummary[];
  } catch {
    // A malformed persisted job ledger is never a reason to crash Settings.
  }

  const costEstimate = discovery?.kind === "estimate" && discovery.data &&
    typeof discovery.data === "object" && "total_cost" in discovery.data &&
    typeof discovery.data.total_cost === "number"
    ? "$" + discovery.data.total_cost.toFixed(4) : "Unknown — check provider pricing";
  const prepareRequest = (): boolean => {
    if (!modelId.trim()) {
      setLocalNotice("Enter a fal.ai model ID, such as fal-ai/flux/schnell.");
      return false;
    }
    try {
      const args = JSON.parse(argumentsJson);
      const opts = JSON.parse(optionsJson);
      if (!args || Array.isArray(args) || typeof args !== "object" ||
          !opts || Array.isArray(opts) || typeof opts !== "object") {
        throw new Error("JSON values must be objects");
      }
    } catch {
      setLocalNotice("Model arguments and request options must both be valid JSON objects.");
      return false;
    }
    setLocalNotice("");
    return true;
  };

  const attachFile = (file: File | undefined) => {
    if (!file) return;
    if (!fileField.trim() || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(fileField.trim())) {
      setLocalNotice("Enter a valid JSON parameter name (for example image_url or audio_url).");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setLocalNotice("Data URI attachments are limited to 8 MiB. Upload larger files to Fal CDN instead.");
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => setLocalNotice("Could not read the chosen file.");
    reader.onload = () => {
      if (typeof reader.result !== "string") return;
      try {
        const previous = JSON.parse(argumentsJson);
        if (!previous || typeof previous !== "object" || Array.isArray(previous)) throw new Error();
        setArgumentsJson(JSON.stringify({ ...previous, [fileField.trim()]: reader.result }, null, 2));
        setLocalNotice(`Attached ${file.name} as a data URI in ${fileField}. Some models require a hosted URL instead.`);
      } catch {
        setLocalNotice("Fix the model arguments JSON before attaching a file.");
      }
    };
    reader.readAsDataURL(file);
  };

  return (
    <div className="settings-general-page">
      <h2 className="settings-page-title">Fal.ai</h2>
      <p className="settings-page-description">
        Run any Fal.ai hosted model or custom endpoint directly from Graphlink: images, video, audio,
        speech, 3D, training endpoints, and future models. Submit long-running jobs to the queue,
        resume them by ID, or use synchronous inference for fast models. These calls can incur charges.
        Visit <a href="https://fal.ai/models" target="_blank" rel="noreferrer">Fal model catalog</a> for
        each model's exact JSON schema and pricing.
      </p>

      <FieldGroup {...{ title: "Connection" }}>
        <TextField label="Fal.ai API key" type="password" suffix={{ type: "password" }}
          autoComplete="new-password" mono placeholder="Paste key (never returned to the browser)" value={keyDraft}
          onValueChange={setKeyDraft} />
        <p className="settings-update-status">
          {state.falApiKeySource === "stored" ? "An API key is saved in Graphlink." :
            state.falApiKeySource === "environment" ? "Using FAL_KEY / GRAPHLINK_FAL_API_KEY from the environment." :
            "No API key configured. Generate a key at fal.ai/dashboard/keys."}
        </p>
        <div className="settings-button-row">
          <Button variant="primary"
            disabled={keyDraft.trim().length === 0 || state.falBusy}
            onClick={() => {
              transport.fireIntent("app-settings", "setFalApiKey", [keyDraft]);
              setKeyDraft("");
            }}>Save API Key</Button>
          <Button variant="secondary" disabled={state.falBusy}
            onClick={() => { transport.fireIntent("app-settings", "clearFalApiKey", []); setKeyDraft(""); }}>
            Clear Stored Key
          </Button>
        </div>
      </FieldGroup>

      <FieldGroup {...{ title: "Live model discovery & pricing" }}>
        <p className="settings-field-hint">
          Search the current Fal catalog and inspect a model's OpenAPI schema before sending
          model-specific inputs. Price checks and cost estimates do not run an inference or
          charge for a generation. Estimates are not guaranteed final bills.
        </p>
        <TextField label="Search models" value={modelSearch}
          placeholder="e.g. text to video, music, image editing"
          onValueChange={setModelSearch} />
        <TextField label="Category (optional)" value={modelCategory}
          placeholder="text-to-image, image-to-video, text-to-speech…"
          onValueChange={setModelCategory} />
        <div className="settings-button-row">
          <Button variant="primary" disabled={state.falBusy}
            onClick={() => transport.fireIntent("app-settings", "searchFalModels",
              [modelSearch.trim(), modelCategory.trim(), 20, ""])}>Search Catalog</Button>
          <Button variant="secondary" disabled={state.falBusy || !modelId.trim()}
            onClick={() => transport.fireIntent("app-settings", "inspectFalModel", [modelId.trim()])}>
            Inspect Selected Model / Schema
          </Button>
          <Button variant="secondary" disabled={state.falBusy || !modelId.trim()}
            onClick={() => transport.fireIntent("app-settings", "getFalModelPricing", [modelId.trim()])}>
            Check Current Pricing
          </Button>
        </div>
        {!!discovered?.models && (
          <div className="settings-field">
            <p className="settings-update-status">{discovered.models.length} matching endpoints on this page</p>
            {discovered.models.filter((m) => typeof m.endpoint_id === "string").map((m) => (
              <div key={m.endpoint_id} className="settings-field">
                <span className="settings-field-label">{m.metadata?.display_name || m.endpoint_id}</span>
                <span className="settings-field-hint">{m.endpoint_id} — {m.metadata?.category || "unknown category"}</span>
                {!!m.metadata?.description && <p className="settings-field-hint">{m.metadata.description}</p>}
                <Button variant="secondary"
                  onClick={() => setModelId(m.endpoint_id)}>Use This Model</Button>
              </div>
            ))}
            {discovered.has_more && typeof discovered.next_cursor === "string" && (
              <Button variant="secondary" disabled={state.falBusy}
                onClick={() => transport.fireIntent("app-settings", "searchFalModels",
                  [modelSearch.trim(), modelCategory.trim(), 20, discovered.next_cursor])}>
                Next Catalog Page
              </Button>
            )}
          </div>
        )}
        <div className="settings-button-row">
          <NumberField label="Cost estimate quantity" min={1} max={1000000} step={1}
            value={estimateQuantity}
            onValueChange={(value) => { if (value !== "") setEstimateQuantity(value); }} />
          <div className="settings-field">
            <span className="settings-field-label">Estimate basis</span>
            <Select ariaLabel="Estimate basis" value={estimateBasis}
              onChange={setEstimateBasis}
              options={[
                { value: "historical_api_price", label: "Historical cost per API call" },
                { value: "unit_price", label: "Cost per billing unit" },
              ]} />
          </div>
          <Button variant="secondary" disabled={state.falBusy || !modelId.trim() ||
            !Number.isInteger(estimateQuantity) || estimateQuantity < 1 || estimateQuantity > 1000000}
            onClick={() => transport.fireIntent("app-settings", "estimateFalModelCost",
              [modelId.trim(), estimateQuantity, estimateBasis])}>Estimate Cost</Button>
        </div>
        {!!discovery && discovery.kind !== "models" && (
          <Disclosure {...{ title: discovery.kind === "details" ? "Model metadata / OpenAPI schema" :
              discovery.kind === "pricing" ? "Current model pricing" : "Cost estimate" }}>
            <pre className="settings-code-output">
              {JSON.stringify(discovery.data, null, 2)}
            </pre>
            {!!discovery.truncated && (
              <p className="settings-field-hint">Schema preview is truncated. Consult Fal's official model page.</p>
            )}
          </Disclosure>
        )}
      </FieldGroup>

      <FieldGroup {...{ title: "Model inference" }}>
        <TextField label="Model endpoint ID (including optional nested path)" value={modelId}
          mono spellCheck={false} onValueChange={setModelId} placeholder="fal-ai/flux/schnell" />
        <label className="settings-field">
          <span className="settings-field-label">Model-specific input (JSON)</span>
          <textarea className="settings-select" spellCheck={false} rows={8} value={argumentsJson}
            onChange={(event) => setArgumentsJson(event.target.value)}
            aria-label="Fal model arguments JSON" />
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Request options (JSON)</span>
          <textarea className="settings-select" spellCheck={false} rows={3} value={optionsJson}
            onChange={(event) => setOptionsJson(event.target.value)}
            aria-label="Fal request options JSON" />
        </label>
        <p className="settings-field-hint">
          Available options include priority, start_timeout, runner_hint, no_retry,
          store_io, object_lifecycle, retry_config, webhook_url (queue only),
          and max_queue_length. Disabling store_io is not equivalent to zero data retention.
        </p>
        <div className="settings-button-row">
          <Button variant="primary" disabled={state.falBusy}
            onClick={async () => {
              if (prepareRequest() && await askConfirm({
                title: "Submit billable queue request?", body: `Submit a Fal.ai queue request to ${modelId}?`,
                billing: { estimate: costEstimate }, confirmLabel: "Submit request",
              })) {
                transport.fireIntent("app-settings", "submitFalJob", [modelId.trim(), argumentsJson, optionsJson]);
              }
            }}>Submit to Queue</Button>
          <Button variant="secondary" disabled={state.falBusy}
            onClick={async () => {
              if (prepareRequest() && await askConfirm({
                title: "Run billable inference?", body: `Run Fal.ai inference on ${modelId} now?`,
                billing: { estimate: costEstimate }, confirmLabel: "Run inference",
              })) {
                transport.fireIntent("app-settings", "runFalSync", [modelId.trim(), argumentsJson, optionsJson]);
              }
            }}>Run Synchronously</Button>
        </div>
      </FieldGroup>

      <FieldGroup {...{ title: "Input media" }}>
        <p className="settings-field-hint">
          Small files can be passed as base64 data URIs when the model supports them.
          For large audio, video or images, use the CDN uploader (requires the optional
          fal-client Python package). The uploaded URL can be pasted as a JSON value.
        </p>
        <TextField label="Input JSON field for file" value={fileField}
          onValueChange={setFileField} placeholder="image_url" />
        <label className="settings-field">
          <span className="settings-field-label">Attach local file as data URI (up to 8 MiB)</span>
          <input type="file" onChange={(event) => attachFile(event.target.files?.[0])} />
        </label>
        <TextField label="Local file path for CDN upload" value={localPath} mono
          onValueChange={setLocalPath} placeholder="Absolute path to a local file" />
        <div className="settings-button-row">
          <Button variant="secondary" disabled={state.falBusy || !localPath.trim()}
            onClick={async () => {
              if (await askConfirm({
                title: "Upload to Fal.ai CDN?",
                body: `Upload ${localPath.split(/[\\/]/).pop() || localPath} to Fal.ai CDN? Its contents will leave your device.`,
                confirmLabel: "Upload file",
              })) {
                transport.fireIntent("app-settings", "uploadFalFile", [localPath.trim()]);
              }
            }}>Upload File to Fal CDN</Button>
        </div>
        {!!state.falUploadUrl && (
          <p className="settings-update-status">
            Uploaded: <a href={state.falUploadUrl} target="_blank" rel="noreferrer">Open file URL</a>
            <Button variant="secondary"
              onClick={() => void navigator.clipboard?.writeText(state.falUploadUrl)}>Copy URL</Button>
          </p>
        )}
      </FieldGroup>

      {!!localNotice && <p className="settings-update-status">{localNotice}</p>}
      <p className="settings-update-status" role="status" aria-live="polite"
        data-level={state.falNotice.includes("failed") || state.falNotice.includes("error") ? "warning" : undefined}>
        {state.falBusy ? <><Spinner size={14} decorative /> Processing Fal.ai request…</> : state.falNotice}
      </p>

      {!!state.falSyncResultJson && (
        <Disclosure {...{ title: "Last synchronous result" }}>
          <pre className="settings-code-output">
            {state.falSyncResultJson}
          </pre>
        </Disclosure>
      )}

      <FieldGroup {...{ title: "Queue jobs (persisted across sessions)" }}>
        <p className="settings-field-hint">
          Queue requests can outlive Graphlink. Refresh a completed job to fetch its output.
          Cancellation is best-effort; Fal may already be running the model. A completed
          status can also represent an error.
        </p>
        {jobs.length === 0 && <p className="settings-update-status">No submitted queue jobs yet.</p>}
        {jobs.map((job) => (
          <Disclosure key={job.requestId} {...{ title: `${job.modelId} — ${job.status} — ${job.requestId.slice(0, 8)}` }}>
            <p className="settings-update-status">Request ID: <code>{job.requestId}</code></p>
            <div className="settings-button-row">
              <Button variant="secondary" disabled={state.falBusy}
                onClick={() => transport.fireIntent("app-settings", "refreshFalJob",
                  [job.modelId, job.requestId, true])}>Refresh / Fetch Result</Button>
              <Button variant="secondary" disabled={state.falBusy}
                onClick={async () => {
                  if (await askConfirm({
                    title: "Cancel queued request?", body: `Request cancellation of ${job.requestId}?`,
                    confirmLabel: "Cancel job", destructive: true,
                  })) {
                    transport.fireIntent("app-settings", "cancelFalJob", [job.modelId, job.requestId]);
                  }
                }}>Cancel Job</Button>
            </div>
            {(job.mediaUrls || []).filter((url) => url.startsWith("https://")).map((url, i) => (
              <p key={url} className="settings-update-status">
                <a href={url} target="_blank" rel="noreferrer">Open generated asset {i + 1}</a>
              </p>
            ))}
            {!!job.resultJson && (
              <Disclosure {...{ title: "JSON output" }}>
                <pre className="settings-code-output">
                  {job.resultJson}
                </pre>
              </Disclosure>
            )}
          </Disclosure>
        ))}
      </FieldGroup>
    </div>
  );
}
