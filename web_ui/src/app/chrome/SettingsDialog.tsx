import { useEffect, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppPluginGrant, AppPluginsState } from "../../lib/bridge-core/generated/app-plugins-state";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import { useExecutionLimits } from "../canvas/ExecutionLimitsContext";
import { Dialog, useOverlays } from "../overlays/overlays";
import { CustomSelect } from "./CustomSelect";
import { CloudProviderSettings } from "./CloudProviderSettings";

/**
 * The settings dialog (Qt-removal plan R2.5d, extended R7.4a-c) - settings
 * island's SPA successor. Every page R2.5d originally deferred is real now:
 * General, Integrations, API-provider, Ollama, and (R7.4c) Llama.cpp. The
 * Ollama page's own "Scan Folder..." button, deferred alongside Llama.cpp
 * pending a native folder-picker capability, is real too - see
 * backend/native_dialogs.py's docstring for the mechanism.
 */

const SECTIONS = [
  "General",
  "Ollama (Local)",
  "Llama.cpp (Local)",
  "API Endpoint",
  "Fal.ai",
  "Integrations",
  "MCP Servers",
  "Plugins",
  "Resource Limits",
] as const;
type Section = (typeof SECTIONS)[number];

// Mirrors graphlink_task_config.py's TASK_* constants - the same 5-slot set
// backend/settings.py's _OLLAMA_TASK_KEYS uses (task_image_gen is absent:
// Ollama has no image-generation path).
const OLLAMA_TASKS = ["task_chat", "task_title", "task_chart", "task_web_validate", "task_web_summarize"] as const;
const OLLAMA_TASK_LABELS: Record<(typeof OLLAMA_TASKS)[number], string> = {
  task_chat: "Chat Model",
  task_title: "Chat Naming Model",
  task_chart: "Chart Generation Model",
  task_web_validate: "Web Content Validation Model",
  task_web_summarize: "Web Content Summarization Model",
};
const OLLAMA_MODELS_DATALIST_ID = "settings-ollama-scanned-models";
const LLAMA_CPP_MODELS_DATALIST_ID = "settings-llama-cpp-scanned-models";

// R8a: reasoning went from a 2-value Mode (Thinking/Quick) to a graded
// 4-value Level shared by both local providers here and the composer's own
// Reasoning popover (backend/composer.py's REASONING_OPTIONS) - same ids,
// same labels, so a user never has to learn two different vocabularies.
const REASONING_LEVEL_OPTIONS = [
  { id: "off", label: "Off" },
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
] as const;

// ADR-018 stage 18.4: mirrors graphlink_model_catalog.AUTO_POLICIES - the
// policy the auto rung of the resolution chain applies when neither an
// explicit task assignment nor a node/branch pin resolves a model.
const AUTO_MODEL_POLICY_OPTIONS = [
  { id: "cheapest-capable", label: "Cheapest Capable" },
  { id: "fastest", label: "Fastest" },
  { id: "best-quality", label: "Best Quality" },
] as const;

// ADR-012 stage 12.2: mirrors SettingsManager.THEMES (graphlink_settings_store.py).
const THEME_OPTIONS = [
  { id: "system", label: "Match System" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
] as const;

// Llama.cpp has no per-task assignment concept like Ollama's OLLAMA_TASKS -
// just one global chat model path plus an optional title/naming override
// (api_provider.py's _get_llama_cpp_model_path: chart/web-validate/web-
// summarize all silently fall back to the chat model path).
function basename(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

const NOTIFICATION_TYPE_LABELS: Record<string, string> = {
  info: "Info",
  success: "Success",
  warning: "Warning",
  error: "Error",
};

// Local mode IDs are also used by the backend setProviderMode intent.
const MODE_OLLAMA_LOCAL = "Ollama (Local)";
const MODE_LLAMACPP_LOCAL = "Llama.cpp (Local)";

const initialState: AppSettingsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  activeSection: "General",
  showTokenCounter: true,
  enableSystemPrompt: true,
  notificationPreferences: {},
  githubTokenConfigured: false,
  falApiKeySource: "none",
  falJobsJson: "[]",
  falNotice: "",
  falBusy: false,
  falSyncResultJson: "",
  falDiscoveryJson: "",
  falUploadUrl: "",
  // ADR-004 stage 4.4: optimistic default (true = encrypted) so the
  // persistent badge below never flashes a false warning before the
  // first real snapshot arrives - the real backend value replaces this
  // on the very first "app-settings" publish, same as every other field
  // here.
  secretsEncryptedAtRest: true,
  // ADR-016 stage 16.1: mirrors SettingsManager.get_log_level()'s own default.
  logLevel: "INFO",
  // ADR-018 stage 18.4: mirrors SettingsManager.get_auto_model_policy()'s
  // own default.
  autoModelPolicy: "cheapest-capable",
  // ADR-012 stage 12.2: mirrors SettingsManager.get_theme()'s own default.
  theme: "system",
  // ADR-012 stage 12.6: mirrors SettingsManager.get_has_completed_onboarding()'s
  // own default - OnboardingDialog.tsx is the surface that actually acts on
  // this; SettingsDialog only needs it to satisfy the shared contract.
  hasCompletedOnboarding: false,
  // ADR-012 stage 12.6: mirrors SettingsManager.get_current_mode()'s own default.
  providerMode: MODE_OLLAMA_LOCAL,
  activeApiProvider: "",
  viewingApiProvider: "",
  apiProviders: [],
  apiBaseUrl: "",
  apiKeyConfigured: {},
  apiKeySource: {},
  apiModels: {},
  apiModelCatalog: [],
  apiCatalogBaseUrl: "",
  apiCatalogStatus: "idle",
  apiCatalogMessage: "Model catalog has not been refreshed yet.",
  geminiStaticModels: [],
  geminiStaticImageModels: [],
  ollamaReasoningLevel: "high",
  ollamaCurrentModel: "",
  ollamaModelAssignments: {},
  ollamaScannedModels: [],
  ollamaScanSummary: "",
  ollamaScanStatus: "idle",
  ollamaPullStatus: "idle",
  ollamaNotice: "",
  llamaCppReasoningLevel: "high",
  llamaCppChatModelPath: "",
  llamaCppTitleModelPath: "",
  llamaCppChatFormat: "",
  llamaCppNCtx: 4096,
  llamaCppNGpuLayers: 0,
  llamaCppNThreads: 0,
  llamaCppScannedModels: [],
  llamaCppScanSummary: "",
  llamaCppScanStatus: "idle",
  llamaCppNotice: "",
  // ADR-012 stage 12.6: mirrors SettingsManager.get_mcp_servers()'s own default.
  mcpServers: [],
};

const initialPluginsState: AppPluginsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  categories: [],
  // ADR-014 stage 14.4: one entry per discovered NON-built-in plugin (see
  // backend/plugins.py's plugin_registry.picker_entries/node_kinds split vs.
  // builtin_actions, and AppPluginGrantPayload's own docstring in
  // contracts/graphlink_app_plugins_payload.py for the "one row per
  // distinct plugin_id" construction rule) - built-ins never appear here.
  grants: [],
};

function sectionKey(section: Section): string {
  // Backend storage is UI-navigation-only for this field (mirrors the
  // legacy bridge - see backend/settings.py's active_section cell), so any
  // stable string works; reusing the display name keeps the wire traffic
  // human-readable in the WS inspector.
  return section === "General" ? "general" : section === "Integrations" ? "integrations" : section.toLowerCase();
}

function GeneralPage({
  state,
  transport,
}: {
  state: AppSettingsState;
  transport: WsTransport;
}) {
  // R8a (UI/UX issue list finding #9) removed the prior Theme select
  // (Dark/Muted/Monochromatic): it persisted a real setTheme intent, but
  // nothing in the SPA ever applied the value - no data-theme attribute, no
  // alternate token set, the app rendered identically no matter what was
  // selected. A persisted preference that provably does nothing is worse
  // than no preference at all, so it was cut rather than left inert.
  // ADR-012 stages 12.1 (a real light+dark token palette) and 12.2 (this
  // control, plus App.tsx's own applyTheme effect) resolve exactly that gap
  // - system/light/dark all genuinely repaint the app now.
  const overlays = useOverlays();
  return (
    <div className="settings-general-page">
      <div className="settings-field">
        <span className="settings-field-label">Theme</span>
        <CustomSelect
          ariaLabel="Theme"
          value={state.theme}
          options={THEME_OPTIONS.map((option) => ({ id: option.id, label: option.label }))}
          onChange={(id) => transport.fireIntent("app-settings", "setTheme", [id], undefined, true)}
        />
      </div>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.showTokenCounter}
          onChange={(event) => transport.fireIntent("app-settings", "setShowTokenCounter", [event.target.checked], undefined, true)}
        />
        Show Token Counter Overlay
      </label>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.enableSystemPrompt}
          onChange={(event) => transport.fireIntent("app-settings", "setEnableSystemPrompt", [event.target.checked], undefined, true)}
        />
        Enable Assistant System Prompt
      </label>

      <div className="settings-field">
        <span className="settings-field-label">Automatic Model Selection Policy</span>
        <CustomSelect
          ariaLabel="Automatic Model Selection Policy"
          value={state.autoModelPolicy}
          options={AUTO_MODEL_POLICY_OPTIONS.map((option) => ({ id: option.id, label: option.label }))}
          onChange={(id) => transport.fireIntent("app-settings", "setAutoModelPolicy", [id], undefined, true)}
        />
        <p className="settings-field-hint">
          Used when a task has no explicitly assigned model and no node/branch pin - picks the cheapest, fastest,
          or highest-quality capable model from your configured providers.
        </p>
      </div>

      <fieldset className="settings-fieldset">
        <legend>Notification types</legend>
        {Object.keys(NOTIFICATION_TYPE_LABELS).map((type) => (
          <label className="settings-checkbox-row" key={type}>
            <input
              type="checkbox"
              checked={state.notificationPreferences[type] ?? true}
              onChange={(event) =>
                transport.fireIntent("app-settings", "setNotificationPreference", [type, event.target.checked], undefined, true)
              }
            />
            {NOTIFICATION_TYPE_LABELS[type]}
          </label>
        ))}
      </fieldset>

      {/* ADR-012 stage 12.6: manual re-entry to the first-run onboarding
          wizard (OnboardingDialog.tsx) - it auto-opens exactly once (a
          fresh machine's hasCompletedOnboarding defaults false) and never
          shows itself again on its own, so this button is the only way
          back to it afterward - the "still reachable manually" entry point,
          matching how Help's own AppBar chip is a standing way back into
          that dialog. */}
      <div className="settings-field">
        <span className="settings-field-label">Onboarding</span>
        <div className="settings-button-row">
          <button type="button" className="settings-button" onClick={() => overlays.open("onboarding", "dialog")}>
            Show Onboarding
          </button>
        </div>
      </div>
    </div>
  );
}


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

function FalAiPage({
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

function IntegrationsPage({
  state,
  transport,
}: {
  state: AppSettingsState;
  transport: WsTransport;
}) {
  const [draftToken, setDraftToken] = useState("");

  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        Store optional external-service tokens used by specialized plugins. The Gitlink plugin uses a GitHub
        personal access token to load your private repositories - if you leave this empty, it still works with
        public repositories.
      </p>

      <label className="settings-field">
        <span className="settings-field-label">GitHub Personal Access Token</span>
        <input
          type="password"
          className="settings-select"
          placeholder="ghp_... or fine-grained token"
          value={draftToken}
          onChange={(event) => setDraftToken(event.target.value)}
        />
      </label>

      <p className="settings-update-status">
        {state.githubTokenConfigured ? "A GitHub token is currently configured." : "No GitHub token configured."}
      </p>

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button"
          onClick={() => {
            transport.fireIntent("app-settings", "clearGithubToken", []);
            setDraftToken("");
          }}
        >
          Clear Token
        </button>
        <button
          type="button"
          className="settings-button settings-button-primary"
          disabled={draftToken.trim().length === 0}
          onClick={() => {
            transport.fireIntent("app-settings", "setGithubToken", [draftToken]);
            setDraftToken("");
          }}
        >
          Save Integrations
        </button>
      </div>
    </div>
  );
}

// A local mode switch starts a provider, so it cannot be replayed offline.
function ProviderModeSwitch({ mode, state, transport }: { mode: string; state: AppSettingsState; transport: WsTransport }) {
  const isActive = state.providerMode === mode;
  return (
    <div className="settings-field">
      <p className="settings-update-status" data-level={isActive ? undefined : "warning"}>
        {isActive
          ? `${mode} is the active provider mode.`
          : `${mode} is configured here, but ${state.providerMode} is the active provider mode.`}
      </p>
      {!isActive && (
        <div className="settings-button-row">
          <button
            type="button"
            className="settings-button settings-button-primary"
            onClick={() => transport.fireIntent("app-settings", "setProviderMode", [mode])}
          >
            Use This Provider
          </button>
        </div>
      )}
    </div>
  );
}

// The flat wire value ("inherit"|"auto"|"<explicit model id>") maps onto two
// UI controls: a mode select (mirrors the legacy island's 3 special combo
// entries) and a conditionally-shown text input for the explicit case. This
// is a deliberate 2-control simplification of the legacy single editable
// combo, ported verbatim from web_ui/src/islands/settings/App.tsx's own
// OllamaTaskField.
function OllamaTaskField({
  task,
  value,
  transport,
}: {
  task: (typeof OLLAMA_TASKS)[number];
  value: string;
  transport: WsTransport;
}) {
  const isSpecial = value === "inherit" || value === "auto";
  const [uiMode, setUiMode] = useState(isSpecial ? value : "explicit");
  const [prevValue, setPrevValue] = useState(value);

  if (value !== prevValue) {
    setPrevValue(value);
    setUiMode(isSpecial ? value : "explicit");
  }

  const modeOptions = [
    ...(task !== "task_chat" ? [{ id: "inherit", label: "Use chat model" }] : []),
    { id: "auto", label: "Auto - choose a compatible installed model" },
    { id: "explicit", label: "Custom model ID..." },
  ];

  return (
    <>
      <div className="settings-field">
        <span className="settings-field-label">{OLLAMA_TASK_LABELS[task]}</span>
        <CustomSelect
          ariaLabel={OLLAMA_TASK_LABELS[task]}
          value={uiMode}
          options={modeOptions}
          onChange={(nextMode) => {
            setUiMode(nextMode);
            if (nextMode !== "explicit") {
              transport.fireIntent("app-settings", "setOllamaModelAssignment", [task, nextMode], undefined, true);
            }
          }}
        />
      </div>
      {uiMode === "explicit" && (
        <label className="settings-field">
          <span className="settings-field-label">{OLLAMA_TASK_LABELS[task]} (custom model ID)</span>
          <input
            type="text"
            className="settings-select"
            list={OLLAMA_MODELS_DATALIST_ID}
            value={isSpecial ? "" : value}
            onChange={(event) => transport.fireIntent("app-settings", "setOllamaModelAssignment", [task, event.target.value], undefined, true)}
          />
        </label>
      )}
    </>
  );
}

function OllamaPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  const [draftPullModel, setDraftPullModel] = useState("");

  return (
    <div className="settings-general-page">
      <ProviderModeSwitch mode={MODE_OLLAMA_LOCAL} state={state} transport={transport} />

      <fieldset className="settings-fieldset">
        <legend>Reasoning Level</legend>
        {REASONING_LEVEL_OPTIONS.map((option) => (
          <label className="settings-checkbox-row" key={option.id}>
            <input
              type="radio"
              name="ollama-reasoning-level"
              checked={state.ollamaReasoningLevel === option.id}
              onChange={() => transport.fireIntent("app-settings", "setOllamaReasoningLevel", [option.id], undefined, true)}
            />
            {option.label}
          </label>
        ))}
      </fieldset>

      <p className="settings-update-status">
        Current Active Model: <strong>{state.ollamaCurrentModel || "Auto - no compatible installed model found"}</strong>
      </p>

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button"
          disabled={state.ollamaScanStatus === "running"}
          onClick={() => transport.fireIntent("app-settings", "scanOllamaSystem", [])}
        >
          {state.ollamaScanStatus === "running" ? "Scanning..." : "System Scan"}
        </button>
        <button
          type="button"
          className="settings-button"
          disabled={state.ollamaScanStatus === "running"}
          onClick={() => transport.fireIntent("app-settings", "pickOllamaScanFolder", [])}
        >
          Scan Folder...
        </button>
      </div>
      <p className="settings-update-status">{state.ollamaScanSummary}</p>

      <datalist id={OLLAMA_MODELS_DATALIST_ID}>
        {state.ollamaScannedModels.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>

      <fieldset className="settings-fieldset">
        <legend>Model Selection (per task)</legend>
        {OLLAMA_TASKS.map((task) => (
          <OllamaTaskField
            key={task}
            task={task}
            value={state.ollamaModelAssignments[task] ?? "auto"}
            transport={transport}
          />
        ))}
      </fieldset>

      <label className="settings-field">
        <span className="settings-field-label">Validate and Pull Model</span>
        <input
          type="text"
          className="settings-select"
          list={OLLAMA_MODELS_DATALIST_ID}
          placeholder="Advanced model ID entry"
          value={draftPullModel}
          onChange={(event) => setDraftPullModel(event.target.value)}
        />
      </label>
      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button settings-button-primary"
          disabled={draftPullModel.trim().length === 0 || state.ollamaPullStatus === "running"}
          onClick={() => transport.fireIntent("app-settings", "pullOllamaModel", [draftPullModel])}
        >
          {state.ollamaPullStatus === "running" ? "Validating..." : "Validate and Pull Model"}
        </button>
      </div>

      {state.ollamaNotice && (
        <p className="settings-update-status" data-level="error">
          {state.ollamaNotice}
        </p>
      )}
    </div>
  );
}

// Llama.cpp's runtime tunables (chat format, n_ctx, n_gpu_layers, n_threads)
// persist on every change - no separate Save step, unlike the model-path
// fields below. A plain value={state.llamaCpp*} binding (no local buffer)
// breaks multi-character typing: the fake test transport (and, over a real
// WS round trip, ordinary network latency) never echoes a change back
// before the NEXT keystroke fires, so the input keeps resetting to the
// stale last-confirmed value between keystrokes instead of accumulating
// what the user is typing. A small local draft, reset only when the
// confirmed value changes to something the draft doesn't already reflect
// (the same pattern OllamaTaskField/ApiProviderPage already use), fixes it.
//
// Adversarial-review finding, deliberately NOT further chased: this reset
// compares the incoming value only against the last value WE'VE SEEN, not
// against every edit we've SENT - so a same-session echo of an EARLIER
// keystroke landing after a NEWER one would still stomp the newer draft.
// A fully correct fix needs echo-cancellation (a pending-edit counter) or a
// backend-tracked version stamp; both add real state-machine complexity
// for a narrow window that requires sustained fast typing during a slow
// round trip on a loopback WS connection. This is the same class of
// already-accepted risk OllamaTaskField's own explicit-model-ID input has
// (no draft buffer at all there) - not a new regression, just not fully
// closed either.
function LlamaCppNumberField({
  label,
  value,
  min,
  max,
  step,
  placeholder,
  onCommit,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  onCommit: (n: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  const [lastValue, setLastValue] = useState(value);

  if (value !== lastValue) {
    setLastValue(value);
    setDraft(String(value));
  }

  return (
    <label className="settings-field">
      <span className="settings-field-label">{label}</span>
      <input
        type="number"
        className="settings-select"
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          const n = Number(event.target.value);
          if (!Number.isNaN(n) && event.target.value.trim() !== "") onCommit(n);
        }}
      />
    </label>
  );
}

function LlamaCppPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  const [draftChatFormat, setDraftChatFormat] = useState(state.llamaCppChatFormat);
  const [lastChatFormat, setLastChatFormat] = useState(state.llamaCppChatFormat);

  if (state.llamaCppChatFormat !== lastChatFormat) {
    setLastChatFormat(state.llamaCppChatFormat);
    setDraftChatFormat(state.llamaCppChatFormat);
  }

  // Chat/title model path fields are STAGED server-side (backend/settings.py's
  // llama_staged_chat_path/llama_staged_title_path) - Browse/select updates
  // the draft immediately (visible via state), but nothing persists until
  // Save Settings, matching the API-provider page's own draft-then-commit
  // shape rather than Ollama's per-field immediate persist.
  const scannedChatValue = state.llamaCppScannedModels.includes(state.llamaCppChatModelPath)
    ? state.llamaCppChatModelPath
    : "";
  const scannedTitleValue = state.llamaCppScannedModels.includes(state.llamaCppTitleModelPath)
    ? state.llamaCppTitleModelPath
    : "";

  return (
    <div className="settings-general-page">
      {/* Compares against the PERSISTED Llama.cpp config, same as
          apply_provider_mode's own Llama.cpp branch reads
          (settings_manager.get_llama_cpp_settings()) - if the model path
          fields below have unsaved edits, switching here still activates
          the last-saved config, not the draft on screen. Save Settings
          first to switch to what you just typed. */}
      <ProviderModeSwitch mode={MODE_LLAMACPP_LOCAL} state={state} transport={transport} />

      <fieldset className="settings-fieldset">
        <legend>Reasoning Level</legend>
        {REASONING_LEVEL_OPTIONS.map((option) => (
          <label className="settings-checkbox-row" key={option.id}>
            <input
              type="radio"
              name="llama-cpp-reasoning-level"
              checked={state.llamaCppReasoningLevel === option.id}
              onChange={() => transport.fireIntent("app-settings", "setLlamaCppReasoningLevel", [option.id], undefined, true)}
            />
            {option.label}
          </label>
        ))}
      </fieldset>

      <p className="settings-update-status">
        Current Active GGUF: <strong>{state.llamaCppChatModelPath ? basename(state.llamaCppChatModelPath) : "No model selected"}</strong>
      </p>

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button"
          disabled={state.llamaCppScanStatus === "running"}
          onClick={() => transport.fireIntent("app-settings", "scanLlamaCppSystem", [])}
        >
          {state.llamaCppScanStatus === "running" ? "Scanning..." : "System Scan"}
        </button>
        <button
          type="button"
          className="settings-button"
          disabled={state.llamaCppScanStatus === "running"}
          onClick={() => transport.fireIntent("app-settings", "pickLlamaCppScanFolder", [])}
        >
          Scan Folder...
        </button>
      </div>
      <p className="settings-update-status">{state.llamaCppScanSummary}</p>

      <datalist id={LLAMA_CPP_MODELS_DATALIST_ID}>
        {state.llamaCppScannedModels.map((path) => (
          <option key={path} value={path} />
        ))}
      </datalist>

      {state.llamaCppScannedModels.length > 0 && (
        <div className="settings-field">
          <span className="settings-field-label">Scanned Chat Model</span>
          <CustomSelect
            ariaLabel="Scanned Chat Model"
            value={scannedChatValue}
            options={[
              { id: "", label: "Select a scanned model..." },
              ...state.llamaCppScannedModels.map((path) => ({ id: path, label: basename(path) })),
            ]}
            onChange={(path) => transport.fireIntent("app-settings", "setLlamaCppChatModelPath", [path], undefined, true)}
          />
        </div>
      )}

      <div className="settings-field">
        <span className="settings-field-label">Chat Model File</span>
        <span className="settings-update-status">{state.llamaCppChatModelPath || "No file selected"}</span>
        <button
          type="button"
          className="settings-button"
          onClick={() => transport.fireIntent("app-settings", "pickLlamaCppChatModelFile", [])}
        >
          Browse...
        </button>
      </div>

      {state.llamaCppScannedModels.length > 0 && (
        <div className="settings-field">
          <span className="settings-field-label">Scanned Naming Model</span>
          <CustomSelect
            ariaLabel="Scanned Naming Model"
            value={scannedTitleValue}
            options={[
              { id: "", label: "Select a scanned model..." },
              ...state.llamaCppScannedModels.map((path) => ({ id: path, label: basename(path) })),
            ]}
            onChange={(path) => transport.fireIntent("app-settings", "setLlamaCppTitleModelPath", [path], undefined, true)}
          />
        </div>
      )}

      <div className="settings-field">
        <span className="settings-field-label">Chat Naming File (optional)</span>
        <span className="settings-update-status">{state.llamaCppTitleModelPath || "Reusing the main chat model"}</span>
        <button
          type="button"
          className="settings-button"
          onClick={() => transport.fireIntent("app-settings", "pickLlamaCppTitleModelFile", [])}
        >
          Browse...
        </button>
      </div>

      <label className="settings-field">
        <span className="settings-field-label">Chat Format Override</span>
        <input
          type="text"
          className="settings-select"
          placeholder="Leave blank to let the GGUF metadata decide"
          value={draftChatFormat}
          onChange={(event) => {
            setDraftChatFormat(event.target.value);
            transport.fireIntent("app-settings", "setLlamaCppChatFormat", [event.target.value], undefined, true);
          }}
        />
      </label>

      <LlamaCppNumberField
        label="Context Window"
        value={state.llamaCppNCtx}
        min={256}
        max={131072}
        step={256}
        onCommit={(n) => transport.fireIntent("app-settings", "setLlamaCppNCtx", [n], undefined, true)}
      />
      <LlamaCppNumberField
        label="GPU Layers"
        value={state.llamaCppNGpuLayers}
        min={-1}
        max={9999}
        onCommit={(n) => transport.fireIntent("app-settings", "setLlamaCppNGpuLayers", [n], undefined, true)}
      />
      <LlamaCppNumberField
        label="CPU Threads"
        value={state.llamaCppNThreads}
        min={0}
        max={256}
        placeholder="0 = Auto"
        onCommit={(n) => transport.fireIntent("app-settings", "setLlamaCppNThreads", [n], undefined, true)}
      />

      {state.llamaCppNotice && (
        <p className="settings-update-status" data-level="error">
          {state.llamaCppNotice}
        </p>
      )}

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button settings-button-primary"
          onClick={() => transport.fireIntent("app-settings", "saveLlamaCppSettings", [])}
        >
          Save Settings
        </button>
      </div>
    </div>
  );
}

// ADR-012 stage 12.6: the deferred UI half of ADR-007 stage 7.5's MCP client
// - an McpServerConfig dataclass and SettingsManager.get_mcp_servers/
// set_mcp_servers persistence already existed (backend/mcp_client.py, its
// own module docstring explicitly deferred this page to ADR-012) with zero
// way to view or edit the configured list until now. Every mutation below
// sends the FULL updated array via setMcpServers - bulk-replace, matching
// SettingsManager.set_mcp_servers' own "replace the whole collection"
// posture, the simplest shape to implement and test on both the toggle/
// remove path (derived from the current state.mcpServers) and the add path
// (state.mcpServers plus one new entry). queueable (same as GeneralPage's
// checkboxes/OllamaTaskField's selects above) - a pure persisted setting
// with no live side effect at save time (backend/agents.py's
// _register_configured_mcp_tools only connects enabled servers lazily, when
// the Builder registry is next built), so replaying it after a reconnect is
// always safe.
/** "KEY=value" lines -> env record. Blank lines and lines without "=" are
 * skipped; the first "=" splits, so a value may itself contain "=" (base64
 * tokens routinely do). Exported for direct unit testing. */
export function parseEnvLines(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    if (!key) continue;
    env[key] = line.slice(eq + 1).trim();
  }
  return env;
}

// SECURITY-FIX (mcp-approval-auto-invisible-in-settings-ui): human-readable
// labels for ToolRegistry's own three-value approval vocabulary
// (backend/tools.py's ApprovalPolicy = Literal["auto", "once", "always"] /
// _KNOWN_APPROVAL_POLICIES) - see the render site below for why this page
// shows the value at all. Falls back to the raw wire string for anything
// outside that set (defensive only; the backend rejects unknown policies
// at registration time, so this page should never actually see one).
const MCP_APPROVAL_LABELS: Record<string, string> = {
  auto: "Auto (no confirmation)",
  once: "Confirm once",
  always: "Always confirm",
};

function McpServersPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  const [draftName, setDraftName] = useState("");
  const [draftCommand, setDraftCommand] = useState("");
  const [draftArgs, setDraftArgs] = useState("");
  const [draftEnv, setDraftEnv] = useState("");

  // What setMcpServers SENDS, which is genuinely not the same shape the
  // app-settings state carries back. env values are write-only on this wire
  // (see the contract's own McpServerConfigPayload.envKeys): the state we
  // render has names only, while an outgoing entry MAY carry real values
  // when the user just typed them. A missing `env` means "keep whatever is
  // stored for this server", which is what lets toggling or removing one
  // server leave every other server's variables untouched.
  //
  // Spelled out in full rather than written as `McpServerConfig & { env?: }`
  // deliberately: intersecting a generated wire type is the ADR-003 C9
  // anti-pattern (see lib/bridge-core/wireTypeCastGuard.test.ts) - it tells
  // TypeScript a wire field exists on the author's word. This type describes
  // the OUTBOUND intent argument, which the generated inbound row type has
  // no business defining.
  type OutgoingMcpServer = {
    // REVIEW-FIX: absent on a brand-new "Add Server" entry - the backend
    // assigns a fresh one (see SettingsManager.set_mcp_servers). Present
    // (via the `...s` spread below) on every existing server, echoed back
    // unchanged - that id, not `name`, is now the join key the backend
    // uses to preserve a server's env values across an edit that omits
    // them, since two servers can share a name.
    id?: string;
    name: string;
    command: string;
    args: string[];
    scopes: string[];
    approval: string;
    enabledTools: string[];
    enabled: boolean;
    timeout: number;
    envKeys: string[];
    env?: Record<string, string>;
  };

  const updateServers = (next: OutgoingMcpServer[]) => {
    transport.fireIntent("app-settings", "setMcpServers", [next], undefined, true);
  };

  const handleAdd = () => {
    const name = draftName.trim();
    const command = draftCommand.trim();
    if (!name || !command) return;
    const args = draftArgs.trim() ? draftArgs.trim().split(/\s+/) : [];
    const env = parseEnvLines(draftEnv);
    updateServers([
      ...state.mcpServers,
      {
        name, command, args, scopes: [], approval: "always", enabledTools: [], enabled: true, timeout: 30,
        // The one place values are sent: the user just typed them. envKeys
        // keeps the entry the same shape the rest of the list has.
        envKeys: Object.keys(env).sort(),
        env,
      },
    ]);
    setDraftName("");
    setDraftCommand("");
    setDraftArgs("");
    setDraftEnv("");
  };

  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        Configure local MCP (Model Context Protocol) servers. Each enabled server's tools become available to the
        Builder, namespaced and approval-gated - MCP servers run arbitrary user-configured commands, so treat them
        the same as any other third-party code you choose to run.
      </p>

      <fieldset className="settings-fieldset">
        <legend>Configured Servers</legend>
        {state.mcpServers.length === 0 && (
          <p className="settings-update-status">No MCP servers configured yet.</p>
        )}
        {state.mcpServers.map((server, index) => (
          <div className="settings-mcp-server-row" key={server.id}>
            <label
              className="settings-checkbox-row settings-mcp-server-toggle"
              aria-label={server.name || "MCP server"}
            >
              <input
                type="checkbox"
                checked={server.enabled}
                onChange={(event) =>
                  updateServers(
                    state.mcpServers.map((s, i) => (i === index ? { ...s, enabled: event.target.checked } : s)),
                  )
                }
              />
              <span className="settings-mcp-server-info">
                <span className="settings-mcp-server-name">{server.name}</span>
                <span className="settings-mcp-server-command">
                  {server.command}
                  {server.args.length > 0 ? ` ${server.args.join(" ")}` : ""}
                </span>
                {/* Names only, never values - these are the user's own
                    tokens for a server they chose to run, and a settings
                    page is no place to echo a secret back. */}
                {(server.envKeys ?? []).length > 0 && (
                  <span className="settings-mcp-server-command">
                    env: {[...server.envKeys].sort().join(", ")}
                  </span>
                )}
                {/* SECURITY-FIX (mcp-approval-auto-invisible-in-settings-ui):
                    before this line, this row showed only name/command/
                    args/env - a server whose config carries approval="auto"
                    (settable via a hand-edited or imported session.dat; see
                    this codebase's own "hostile data on disk" threat
                    boundary) has its tools run with NO human confirmation
                    in either Builder mode (backend/tools.py's ToolRegistry.
                    invoke gates on `registration.approval != "auto"` -
                    "auto" is the one policy that skips request_approval
                    entirely), and there was no control surface here a user
                    would think to check for it. Read-only display, not an
                    editable control: the gap this closes is the value being
                    INVISIBLE, not being un-settable, and this page's own
                    edit-in-place mutations (the enabled checkbox above,
                    Remove) are the only two established here - reusing that
                    same bulk-replace `updateServers` path for a security-
                    relevant field risked a confused-click flipping
                    "always"->"auto" as a bigger, less-certain change than
                    this pass calls for. Always rendered (unlike the
                    conditional env line above) because the whole point is
                    that this must never be easy to miss. "auto" additionally
                    gets a distinct color - the same --gl-semantic-status-
                    warning token .plan-node-approval already uses for its
                    own approval-needed banner - so the one policy that
                    skips confirmation reads as visually different from the
                    two that don't, not just differently worded. */}
                <span
                  className={
                    server.approval === "auto"
                      ? "settings-mcp-server-command settings-mcp-server-approval-auto"
                      : "settings-mcp-server-command"
                  }
                >
                  Approval: {MCP_APPROVAL_LABELS[server.approval] ?? server.approval}
                </span>
                {/* SECURITY-FIX (mcp-approval-auto-invisible-in-settings-ui):
                    same visibility gap as `approval` above, for the granted
                    `scopes` list. Purely informational, matching
                    PluginsPage's own established stance a few dozen lines
                    down ("no per-scope editing UI - MCP Servers itself has
                    never had one for its own `scopes` field") - scopes are
                    what a server's config declares it was granted, not a
                    per-tool pick a user makes here. */}
                <span className="settings-mcp-server-command">
                  Scopes: {server.scopes.length > 0 ? server.scopes.join(", ") : "No declared scopes"}
                </span>
              </span>
            </label>
            <button
              type="button"
              className="settings-button settings-mcp-server-remove"
              aria-label={`Remove ${server.name}`}
              onClick={() => updateServers(state.mcpServers.filter((_, i) => i !== index))}
            >
              Remove
            </button>
          </div>
        ))}
      </fieldset>

      <fieldset className="settings-fieldset">
        <legend>Add Server</legend>
        <label className="settings-field">
          <span className="settings-field-label">Server Name</span>
          <input
            type="text"
            className="settings-select"
            placeholder="e.g. filesystem"
            value={draftName}
            onChange={(event) => setDraftName(event.target.value)}
          />
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Command</span>
          <input
            type="text"
            className="settings-select"
            placeholder="e.g. npx"
            value={draftCommand}
            onChange={(event) => setDraftCommand(event.target.value)}
          />
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Arguments (optional, space-separated)</span>
          <input
            type="text"
            className="settings-select"
            placeholder="-y @modelcontextprotocol/server-filesystem /path/to/allow"
            value={draftArgs}
            onChange={(event) => setDraftArgs(event.target.value)}
          />
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Environment variables (optional, one KEY=value per line)</span>
          <textarea
            className="settings-select settings-mcp-env-input"
            rows={3}
            placeholder={"GITHUB_TOKEN=ghp_...\nBRAVE_API_KEY=..."}
            value={draftEnv}
            onChange={(event) => setDraftEnv(event.target.value)}
            spellCheck={false}
          />
          <span className="settings-field-hint">
            A server receives only these variables plus basic system plumbing (PATH, TEMP, HOME) - never this
            app&apos;s own provider keys.
          </span>
        </label>
        <div className="settings-button-row">
          <button
            type="button"
            className="settings-button settings-button-primary"
            disabled={!draftName.trim() || !draftCommand.trim()}
            onClick={handleAdd}
          >
            Add Server
          </button>
        </div>
      </fieldset>
    </div>
  );
}

// ADR-014 stage 14.4: install-time consent for discovered THIRD-PARTY
// plugins, mirroring McpServersPage's own minimalism immediately above -
// one row per entry, a single checkbox, no per-scope editing UI (MCP
// Servers itself has never had one for its own `scopes` field, so this
// stays exactly that coarse). Built-in plugins (the 8 migrated in stage
// 14.3) never appear in `grants` - backend/plugins.py only emits an entry
// per distinct plugin_id reached through the generic, non-builtin_actions
// discovery path - so this page can never gate anything that ships with
// the app itself.
//
// Honesty note (carried over from the ADR-014 stage 14.4 design): checking
// "Granted" here answers "did the user consent to this plugin acting at
// all," not "is this specific mutation confined to some enforced boundary."
// A plugin's declared scopes are a self-reported checklist the backend
// records for its own self-documentation, not a sandboxed capability set -
// real capability sandboxing is ADR-014 stage 14.5's job (out-of-process
// execution). This page does not claim otherwise.
function PluginsPage({ grants, transport }: { grants: AppPluginGrant[]; transport: WsTransport }) {
  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        Discovered third-party plugins are not granted by default. A plugin listed here cannot create nodes or run
        its intents until you check "Granted" for it - built-in plugins ship with the app and are never listed or
        gated here.
      </p>

      <fieldset className="settings-fieldset">
        <legend>Discovered Plugins</legend>
        {grants.length === 0 && (
          <p className="settings-update-status">No third-party plugins need a grant right now.</p>
        )}
        {grants.map((grant) => (
          <div className="settings-mcp-server-row" key={grant.pluginId}>
            <label className="settings-checkbox-row settings-mcp-server-toggle">
              <input
                type="checkbox"
                aria-label={`Granted: ${grant.name}`}
                checked={grant.granted}
                onChange={(event) =>
                  transport.fireIntent(
                    "app-plugins",
                    "setPluginGrant",
                    [grant.pluginId, event.target.checked],
                    undefined,
                    true,
                  )
                }
              />
              <span className="settings-mcp-server-info">
                <span className="settings-mcp-server-name">{grant.name}</span>
                <span className="settings-mcp-server-command">
                  {grant.scopes.length > 0 ? grant.scopes.join(", ") : "No declared scopes"}
                </span>
              </span>
            </label>
          </div>
        ))}
      </fieldset>
    </div>
  );
}

// ADR-012 stage 12.6: a read-only disclosure surface, not a settings page in
// the usual sense - there is nothing here to edit (per ADR-005 §4, these
// caps are a fixed, backend-computed fact, not a user-tunable preference),
// so unlike every other page in this file there is no transport prop and no
// fireIntent call anywhere below. Sourced from useExecutionLimits()
// (ExecutionLimitsContext.tsx) - the SAME context CodeExecutionApprovalPanel.tsx
// already reads before a human approves a pending Execution Sandbox run -
// rather than a second copy of the strings, so this page and that panel can
// never drift out of sync with each other or with the backend's own
// platform-conditional computation (backend/execution_limits.py). Exists so
// the disclosure is reachable on its own, ahead of time, rather than only
// surfacing the first time a user is mid-approval and already under time
// pressure to decide.
function ResourceLimitsPage() {
  const executionLimits = useExecutionLimits();
  const hasAnyText = !!executionLimits.codeSandboxResourceLimitsText;

  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        The resource caps applied to AI-driven code execution, computed by the backend for this system. The same
        text is shown in the Virtual Environment Runner approval prompt before you approve a run -
        this page just makes it reachable without waiting for one.
      </p>

      {!!executionLimits.codeSandboxResourceLimitsText && (
        <div className="settings-field">
          <span className="settings-field-label">Virtual Environment Runner</span>
          <p className="settings-update-status">{executionLimits.codeSandboxResourceLimitsText}</p>
        </div>
      )}

      {!hasAnyText && (
        <p className="settings-update-status">No resource limits have been reported by the backend yet.</p>
      )}
    </div>
  );
}

export function SettingsDialog({ transport }: { transport: WsTransport }) {
  const [state, setState] = useState<AppSettingsState>(initialState);
  // ADR-014 stage 14.4: a second, independent subscription to "app-plugins"
  // (the same topic PluginPicker.tsx already reads) - the grants list isn't
  // part of "app-settings" (which this dialog otherwise reads exclusively),
  // so this is a sibling subscription, not a repurposing of `state` above.
  const [pluginsState, setPluginsState] = useState<AppPluginsState>(initialPluginsState);

  useEffect(() => {
    return transport.subscribe("app-settings", (payload) => {
      const validated = TOPIC_VALIDATORS["app-settings"](payload);
      if (validated.ok) setState(validated.value as AppSettingsState);
      else console.error("[app-settings] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  useEffect(() => {
    return transport.subscribe("app-plugins", (payload) => {
      const validated = TOPIC_VALIDATORS["app-plugins"](payload);
      if (validated.ok) setPluginsState(validated.value as AppPluginsState);
      else console.error("[app-plugins] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  const activeSection = (SECTIONS.find((s) => sectionKey(s) === state.activeSection) ?? "General") as Section;

  return (
    <Dialog name="settings" title="Settings" className="settings-dialog">
      {/* ADR-004 stage 4.4: persistent (visible regardless of which
          section tab is active, not a one-time toast) - a DPAPI failure
          means every API key/GitHub token this dialog saves from now on
          is written to disk in PLAINTEXT, a fact worth surfacing no
          matter which page a user happens to be on when it's true. Hidden
          entirely in the common case (secretsEncryptedAtRest is true).
          role="alert" (adversarial-review fix) matches every other warning/
          error banner in this codebase (BridgeErrorState.tsx, the 3
          node-view banner-error divs) - without it, the dialog's own focus
          trap moves focus straight to the rail buttons on open (this <p>
          isn't focusable), so screen-reader users could tab straight past
          this security-relevant text and never encounter it, and a later
          flip from encrypted to unencrypted while the dialog is already
          open would render with no assistive-tech announcement at all. */}
      {!state.secretsEncryptedAtRest && (
        <p className="settings-update-status settings-secrets-warning" data-level="warning" role="alert">
          API keys are stored unencrypted on this system (Windows DPAPI is unavailable or failing here).
        </p>
      )}
      <div className="settings-shell">
        <nav className="settings-rail" aria-label="Settings sections">
          {SECTIONS.map((section) => (
            <button
              key={section}
              type="button"
              className={"settings-rail-button" + (section === activeSection ? " active" : "")}
              aria-current={section === activeSection ? "page" : undefined}
              onClick={() => transport.fireIntent("app-settings", "setActiveSection", [sectionKey(section)], undefined, true)}
            >
              {section}
            </button>
          ))}
        </nav>
        <div className="settings-page" role="region" aria-label={activeSection}>
          {activeSection === "General" ? (
            <GeneralPage state={state} transport={transport} />
          ) : activeSection === "Integrations" ? (
            <IntegrationsPage state={state} transport={transport} />
          ) : activeSection === "API Endpoint" ? (
            <CloudProviderSettings state={state} transport={transport} />
          ) : activeSection === "Fal.ai" ? (
            <FalAiPage state={state} transport={transport} />
          ) : activeSection === "Ollama (Local)" ? (
            <OllamaPage state={state} transport={transport} />
          ) : activeSection === "Llama.cpp (Local)" ? (
            <LlamaCppPage state={state} transport={transport} />
          ) : activeSection === "MCP Servers" ? (
            <McpServersPage state={state} transport={transport} />
          ) : activeSection === "Plugins" ? (
            <PluginsPage grants={pluginsState.grants} transport={transport} />
          ) : activeSection === "Resource Limits" ? (
            <ResourceLimitsPage />
          ) : (
            activeSection
          )}
        </div>
      </div>
    </Dialog>
  );
}
