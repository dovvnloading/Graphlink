import { useState } from "react";
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
      <p className="settings-integrations-intro">
        Run any Fal.ai hosted model or custom endpoint directly from Graphlink: images, video, audio,
        speech, 3D, training endpoints, and future models. Submit long-running jobs to the queue,
        resume them by ID, or use synchronous inference for fast models. These calls can incur charges.
        Visit <a href="https://fal.ai/models" target="_blank" rel="noreferrer">Fal model catalog</a> for
        each model's exact JSON schema and pricing.
      </p>

      <fieldset className="settings-fieldset">
        <legend>Connection</legend>
        <label className="settings-field">
          <span className="settings-field-label">Fal.ai API key</span>
          <input type="password" autoComplete="off" className="settings-select"
            placeholder="Paste key (never returned to the browser)" value={keyDraft}
            onChange={(event) => setKeyDraft(event.target.value)} />
        </label>
        <p className="settings-update-status">
          {state.falApiKeySource === "stored" ? "An API key is saved in Graphlink." :
            state.falApiKeySource === "environment" ? "Using FAL_KEY / GRAPHLINK_FAL_API_KEY from the environment." :
            "No API key configured. Generate a key at fal.ai/dashboard/keys."}
        </p>
        <div className="settings-button-row">
          <button type="button" className="settings-button settings-button-primary"
            disabled={keyDraft.trim().length === 0 || state.falBusy}
            onClick={() => {
              transport.fireIntent("app-settings", "setFalApiKey", [keyDraft]);
              setKeyDraft("");
            }}>Save API Key</button>
          <button type="button" className="settings-button" disabled={state.falBusy}
            onClick={() => { transport.fireIntent("app-settings", "clearFalApiKey", []); setKeyDraft(""); }}>
            Clear Stored Key
          </button>
        </div>
      </fieldset>

      <fieldset className="settings-fieldset">
        <legend>Live model discovery &amp; pricing</legend>
        <p className="settings-field-hint">
          Search the current Fal catalog and inspect a model's OpenAPI schema before sending
          model-specific inputs. Price checks and cost estimates do not run an inference or
          charge for a generation. Estimates are not guaranteed final bills.
        </p>
        <label className="settings-field">
          <span className="settings-field-label">Search models</span>
          <input className="settings-select" value={modelSearch}
            placeholder="e.g. text to video, music, image editing"
            onChange={(event) => setModelSearch(event.target.value)} />
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Category (optional)</span>
          <input className="settings-select" value={modelCategory}
            placeholder="text-to-image, image-to-video, text-to-speech..."
            onChange={(event) => setModelCategory(event.target.value)} />
        </label>
        <div className="settings-button-row">
          <button type="button" className="settings-button settings-button-primary" disabled={state.falBusy}
            onClick={() => transport.fireIntent("app-settings", "searchFalModels",
              [modelSearch.trim(), modelCategory.trim(), 20, ""])}>Search Catalog</button>
          <button type="button" className="settings-button" disabled={state.falBusy || !modelId.trim()}
            onClick={() => transport.fireIntent("app-settings", "inspectFalModel", [modelId.trim()])}>
            Inspect Selected Model / Schema
          </button>
          <button type="button" className="settings-button" disabled={state.falBusy || !modelId.trim()}
            onClick={() => transport.fireIntent("app-settings", "getFalModelPricing", [modelId.trim()])}>
            Check Current Pricing
          </button>
        </div>
        {!!discovered?.models && (
          <div className="settings-field">
            <p className="settings-update-status">{discovered.models.length} matching endpoints on this page</p>
            {discovered.models.filter((m) => typeof m.endpoint_id === "string").map((m) => (
              <div key={m.endpoint_id} className="settings-field">
                <span className="settings-field-label">{m.metadata?.display_name || m.endpoint_id}</span>
                <span className="settings-field-hint">{m.endpoint_id} — {m.metadata?.category || "unknown category"}</span>
                {!!m.metadata?.description && <p className="settings-field-hint">{m.metadata.description}</p>}
                <button type="button" className="settings-button"
                  onClick={() => setModelId(m.endpoint_id)}>Use This Model</button>
              </div>
            ))}
            {discovered.has_more && typeof discovered.next_cursor === "string" && (
              <button type="button" className="settings-button" disabled={state.falBusy}
                onClick={() => transport.fireIntent("app-settings", "searchFalModels",
                  [modelSearch.trim(), modelCategory.trim(), 20, discovered.next_cursor])}>
                Next Catalog Page
              </button>
            )}
          </div>
        )}
        <div className="settings-button-row">
          <label className="settings-field">
            <span className="settings-field-label">Cost estimate quantity</span>
            <input type="number" className="settings-select" min={1} max={1000000}
              value={estimateQuantity} onChange={(event) => setEstimateQuantity(Number(event.target.value))} />
          </label>
          <label className="settings-field">
            <span className="settings-field-label">Estimate basis</span>
            <select className="settings-select" value={estimateBasis}
              onChange={(event) => setEstimateBasis(event.target.value)}>
              <option value="historical_api_price">Historical cost per API call</option>
              <option value="unit_price">Cost per billing unit</option>
            </select>
          </label>
          <button type="button" className="settings-button" disabled={state.falBusy || !modelId.trim() ||
            !Number.isInteger(estimateQuantity) || estimateQuantity < 1 || estimateQuantity > 1000000}
            onClick={() => transport.fireIntent("app-settings", "estimateFalModelCost",
              [modelId.trim(), estimateQuantity, estimateBasis])}>Estimate Cost</button>
        </div>
        {!!discovery && discovery.kind !== "models" && (
          <details open className="settings-field">
            <summary>{discovery.kind === "details" ? "Model metadata / OpenAPI schema" :
              discovery.kind === "pricing" ? "Current model pricing" : "Cost estimate"}</summary>
            <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 400, overflow: "auto" }}>
              {JSON.stringify(discovery.data, null, 2)}
            </pre>
            {!!discovery.truncated && (
              <p className="settings-field-hint">Schema preview is truncated. Consult Fal's official model page.</p>
            )}
          </details>
        )}
      </fieldset>

      <fieldset className="settings-fieldset">
        <legend>Model inference</legend>
        <label className="settings-field">
          <span className="settings-field-label">Model endpoint ID (including optional nested path)</span>
          <input className="settings-select" spellCheck={false} value={modelId}
            onChange={(event) => setModelId(event.target.value)}
            placeholder="fal-ai/flux/schnell" />
        </label>
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
          <button type="button" className="settings-button settings-button-primary" disabled={state.falBusy}
            onClick={() => {
              if (prepareRequest() && window.confirm(`Submit a billable Fal.ai queue request to ${modelId}?`)) {
                transport.fireIntent("app-settings", "submitFalJob", [modelId.trim(), argumentsJson, optionsJson]);
              }
            }}>Submit to Queue</button>
          <button type="button" className="settings-button" disabled={state.falBusy}
            onClick={() => {
              if (prepareRequest() && window.confirm(`Run a billable Fal.ai synchronous request to ${modelId}?`)) {
                transport.fireIntent("app-settings", "runFalSync", [modelId.trim(), argumentsJson, optionsJson]);
              }
            }}>Run Synchronously</button>
        </div>
      </fieldset>

      <fieldset className="settings-fieldset">
        <legend>Input media</legend>
        <p className="settings-field-hint">
          Small files can be passed as base64 data URIs when the model supports them.
          For large audio, video or images, use the CDN uploader (requires the optional
          fal-client Python package). The uploaded URL can be pasted as a JSON value.
        </p>
        <label className="settings-field">
          <span className="settings-field-label">Input JSON field for file</span>
          <input className="settings-select" value={fileField}
            onChange={(event) => setFileField(event.target.value)} placeholder="image_url" />
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Attach local file as data URI (up to 8 MiB)</span>
          <input type="file" onChange={(event) => attachFile(event.target.files?.[0])} />
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Local file path for CDN upload</span>
          <input className="settings-select" value={localPath}
            onChange={(event) => setLocalPath(event.target.value)}
            placeholder="Absolute path to a local file" />
        </label>
        <div className="settings-button-row">
          <button type="button" className="settings-button" disabled={state.falBusy || !localPath.trim()}
            onClick={() => {
              if (window.confirm("Upload this local file to Fal.ai CDN? Its contents will leave your device.")) {
                transport.fireIntent("app-settings", "uploadFalFile", [localPath.trim()]);
              }
            }}>Upload File to Fal CDN</button>
        </div>
        {!!state.falUploadUrl && (
          <p className="settings-update-status">
            Uploaded: <a href={state.falUploadUrl} target="_blank" rel="noreferrer">Open file URL</a>
            <button type="button" className="settings-button"
              onClick={() => void navigator.clipboard?.writeText(state.falUploadUrl)}>Copy URL</button>
          </p>
        )}
      </fieldset>

      {!!localNotice && <p className="settings-update-status">{localNotice}</p>}
      <p className="settings-update-status" role="status" aria-live="polite"
        data-level={state.falNotice.includes("failed") || state.falNotice.includes("error") ? "warning" : undefined}>
        {state.falBusy ? "Processing Fal.ai request..." : state.falNotice}
      </p>

      {!!state.falSyncResultJson && (
        <details open className="settings-fieldset">
          <summary>Last synchronous result</summary>
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 320, overflow: "auto" }}>
            {state.falSyncResultJson}
          </pre>
        </details>
      )}

      <fieldset className="settings-fieldset">
        <legend>Queue jobs (persisted across sessions)</legend>
        <p className="settings-field-hint">
          Queue requests can outlive Graphlink. Refresh a completed job to fetch its output.
          Cancellation is best-effort; Fal may already be running the model. A completed
          status can also represent an error.
        </p>
        {jobs.length === 0 && <p className="settings-update-status">No submitted queue jobs yet.</p>}
        {jobs.map((job) => (
          <details key={job.requestId} className="settings-field">
            <summary>
              <strong>{job.modelId}</strong> — {job.status} — {job.requestId.slice(0, 8)}
            </summary>
            <p className="settings-update-status">Request ID: <code>{job.requestId}</code></p>
            <div className="settings-button-row">
              <button type="button" className="settings-button" disabled={state.falBusy}
                onClick={() => transport.fireIntent("app-settings", "refreshFalJob",
                  [job.modelId, job.requestId, true])}>Refresh / Fetch Result</button>
              <button type="button" className="settings-button" disabled={state.falBusy}
                onClick={() => {
                  if (window.confirm(`Request cancellation of ${job.requestId}?`)) {
                    transport.fireIntent("app-settings", "cancelFalJob", [job.modelId, job.requestId]);
                  }
                }}>Cancel Job</button>
            </div>
            {(job.mediaUrls || []).filter((url) => url.startsWith("https://")).map((url, i) => (
              <p key={url} className="settings-update-status">
                <a href={url} target="_blank" rel="noreferrer">Open generated asset {i + 1}</a>
              </p>
            ))}
            {!!job.resultJson && (
              <details>
                <summary>JSON output</summary>
                <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 320, overflow: "auto" }}>
                  {job.resultJson}
                </pre>
              </details>
            )}
          </details>
        ))}
      </fieldset>
    </div>
  );
}
