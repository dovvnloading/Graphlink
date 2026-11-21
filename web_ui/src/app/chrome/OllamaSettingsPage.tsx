import { useEffect, useState } from "react";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import { CustomSelect } from "./CustomSelect";
import { ProviderModeSwitch, REASONING_LEVEL_OPTIONS } from "./LocalProviderSettings";

const MODE_OLLAMA_LOCAL = "Ollama (Local)";

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
const OLLAMA_CONTEXT_CHOICES = [
  { tokens: 4096, label: "4k" },
  { tokens: 8192, label: "8k" },
  { tokens: 16384, label: "16k" },
  { tokens: 32768, label: "32k" },
  { tokens: 65536, label: "64k" },
  { tokens: 131072, label: "128k" },
] as const;

function gib(bytes: number): string {
  return (bytes / (1024 ** 3)).toFixed(1) + " GiB";
}

// Download sizes are approximate Ollama library sizes for these exact tags.
// This is a static, opt-in starter list, never an automatic download queue.
const OLLAMA_RECOMMENDED_MODELS = [
  { id: "qwen3:4b", purpose: "Tool-capable chat", size: "2.5 GB" },
  { id: "nomic-embed-text:latest", purpose: "Embeddings", size: "274 MB" },
  { id: "gemma3:4b", purpose: "Vision and image understanding", size: "3.4 GB" },
] as const;

const OLLAMA_SERVER_STATUS_LABELS: Record<string, string> = {
  checking: "Checking...",
  running: "Running",
  installed: "Installed but not running",
  not_found: "Not found",
};

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
            aria-label={`${OLLAMA_TASK_LABELS[task]} (custom model ID)`}
            value={isSpecial ? "" : value}
            onChange={(event) => transport.fireIntent("app-settings", "setOllamaModelAssignment", [task, event.target.value], undefined, true)}
          />
        </label>
      )}
    </>
  );
}

function formatOllamaBytes(value: number | null | undefined): string {
  if (value == null) return "Unknown";
  if (value === 0) return "0 B";
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 4);
  return `${(value / (1024 ** unit)).toFixed(1)} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}

export function OllamaSettingsPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  const [draftPullModel, setDraftPullModel] = useState("");
  const checkInProgress = state.ollamaModelChecks.some((check) => check.status === "running");
  const [selectedContextModel, setSelectedContextModel] = useState("");
  const contextModels = Array.from(new Set([
    ...state.ollamaScannedModels,
    ...state.ollamaInventory.map((model) => model.name),
    state.ollamaCurrentModel,
    ...Object.values(state.ollamaModelAssignments),
    ...Object.keys(state.ollamaContextWindows),
  ].map((model) => model.trim()).filter((model) => model && model !== "auto" && model !== "inherit")));
  const contextModel = contextModels.includes(selectedContextModel)
    ? selectedContextModel : (state.ollamaCurrentModel || contextModels[0] || "");
  const contextReady = state.ollamaContextModel === contextModel && state.ollamaContextStatus === "ready";
  // Use PR 917's shared /api/tags + /api/show snapshot as the source
  // of trained context limits. Disk-only metadata is not verified.
  const contextKey = (name: string) => {
    const normalized = name.trim().toLowerCase();
    return normalized.includes(":") ? normalized : `${normalized}:latest`;
  };
  const inventoryModel = state.ollamaInventory.find((model) => contextKey(model.name) === contextKey(contextModel));
  const trainedLimit = inventoryModel?.source === "server"
    && typeof inventoryModel.trainedContextLength === "number"
    && inventoryModel.trainedContextLength > 0 ? inventoryModel.trainedContextLength : 0;
  const optionLimit = trainedLimit || 8192;
  const savedWindow = state.ollamaContextWindows[contextModel.toLowerCase()] ?? 8192;
  const activeWindow = Math.min(savedWindow, optionLimit);
  const memoryEstimate = contextReady
    ? state.ollamaContextModelSizeBytes + activeWindow * state.ollamaContextKvBytesPerToken
    : 0;
  const availableMemory = state.ollamaContextAvailableMemoryBytes;
  const exceedsMemory = contextReady && availableMemory > 0 && memoryEstimate > availableMemory;
  const [pendingRemoval, setPendingRemoval] = useState<string | null>(null);
  const pulling = state.ollamaPullStatus === "running" || state.ollamaPullStatus === "cancelling";
  const busy = pulling || state.ollamaRemoveStatus === "running" || state.ollamaScanStatus === "running";
  const installedModels = state.ollamaInventorySource === "server" ? state.ollamaInventory : [];
  const percent = state.ollamaPullTotal > 0
    ? Math.min(100, Math.round((100 * state.ollamaPullCompleted) / state.ollamaPullTotal))
    : null;

  useEffect(() => {
    if (contextModel) {
      transport.fireIntent("app-settings", "inspectOllamaContextModel", [contextModel]);
    }
  }, [contextModel, transport, state.ollamaScanStatus]);

  // Refresh the shared Ollama/Knowledge inventory on page open. Only explicit
  // Pull and Remove clicks mutate the installed models on the daemon.
  useEffect(() => {
    transport.fireIntent("app-settings", "scanOllamaSystem", []);
  }, [transport]);

  return (
    <div className="settings-general-page">
      <ProviderModeSwitch mode={MODE_OLLAMA_LOCAL} state={state} transport={transport} />

      <p className="settings-update-status" role="status" aria-label="Ollama server status">
        Ollama server: {OLLAMA_SERVER_STATUS_LABELS[state.ollamaServerStatus] ?? "Checking..."}
      </p>
      {state.ollamaServerStatus === "installed" && (
        <div className="settings-button-row">
          <button
            type="button"
            className="settings-button settings-button-primary"
            disabled={state.ollamaStartStatus === "starting"}
            onClick={() => transport.fireIntent("app-settings", "startOllama", [])}
          >
            Start Ollama
          </button>
        </div>
      )}
      {state.ollamaStartStatus === "starting" && (
        <p className="settings-update-status">Starting Ollama...</p>
      )}
      {state.ollamaStartError && (
        <p className="settings-update-status" role="alert" data-level="error">
          {state.ollamaStartError}
        </p>
      )}

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
          disabled={busy}
          onClick={() => transport.fireIntent("app-settings", "scanOllamaSystem", [])}
        >
          {state.ollamaScanStatus === "running" ? "Scanning..." : "System Scan"}
        </button>
        <button
          type="button"
          className="settings-button"
          disabled={busy}
          onClick={() => transport.fireIntent("app-settings", "pickOllamaScanFolder", [])}
        >
          Scan Folder...
        </button>
      </div>
      <p className="settings-update-status">{state.ollamaScanSummary}</p>
      {state.ollamaInventorySource === "folder" && (
        <p className="settings-field-hint">
          Models were found on disk. Run System Scan with Ollama running to inspect capabilities and memory usage.
        </p>
      )}

      <fieldset className="settings-fieldset">
        <legend>Local model inventory</legend>
        <p className="settings-field-hint">
          Check loads a model and runs brief probes. Results are reports only: no settings change and tool calls are never executed.
        </p>
        <div className="settings-button-row">
          <button
            type="button"
            className="settings-button"
            disabled={busy}
            onClick={() => transport.fireIntent("app-settings", "scanOllamaSystem", [])}
          >
            {state.ollamaScanStatus === "running" ? "Refreshing..." : "Refresh installed models"}
          </button>
        </div>
        {state.ollamaServerReachable === false && (
          <p role="alert" className="settings-update-status" data-level="error">
            Ollama is unavailable. Start the daemon and refresh to manage installed models; disk scan results cannot be removed here.
          </p>
        )}
        {state.ollamaInventory.length === 0 && (
          <p className="settings-field-hint">
            {state.ollamaInventorySource === "server"
              ? "No models installed in this Ollama instance."
              : "Run a system scan to inspect installed models."}
          </p>
        )}
        {state.ollamaInventory.map((model) => {
          const report = state.ollamaModelChecks.find((item) => item.name === model.name);
          return (
            <div className="settings-field" key={model.name}>
              <strong>{model.name}</strong>
              <span className="settings-field-hint">
                {model.loaded === true
                  ? `Loaded · ${formatOllamaBytes(model.memoryBytes)} memory in use · ${formatOllamaBytes(model.vramBytes)} VRAM`
                  : model.loaded === false ? "Not loaded" : "Load state unavailable"}
              </span>
              <span className="settings-field-hint">
                {formatOllamaBytes(model.sizeBytes)} on disk · {model.family || "Unknown family"} ·
                {" "}{model.parameterSize || "Unknown parameter size"} · {model.quantization || "Unknown quantization"}
                {" "}· Trained context: {model.trainedContextLength?.toLocaleString() ?? "Unknown"} tokens
              </span>
              <span className="settings-field-hint">
                Capabilities: {model.capabilities.join(", ") || "Unavailable"}
              </span>
              <div className="settings-button-row">
                <button type="button" className="settings-button" aria-label={`Check ${model.name}`}
                  disabled={checkInProgress || busy}
                  onClick={() => transport.fireIntent("app-settings", "checkOllamaModel", [model.name])}>
                  {report?.status === "running" ? "Checking..." : "Check"}
                </button>
                {report?.status === "running" && (
                  <button type="button" className="settings-button" aria-label={`Cancel check ${model.name}`}
                    onClick={() => transport.fireIntent("app-settings", "cancelOllamaModelCheck", [model.name])}>
                    Cancel
                  </button>
                )}
              </div>
              {report && (
                <p className="settings-update-status" role="status"
                  data-level={report.status === "error" ? "error" : undefined}>
                  {report.status === "running" ? "Checking model..." :
                    report.status === "cancelled" ? "Check cancelled." :
                    report.status === "error" ? report.message :
                    `Load: ${report.loadSeconds == null ? "Unavailable" : `${report.loadSeconds.toFixed(2)} s`} ·
                    Speed: ${report.tokensPerSecond == null ? "Unavailable" : `${report.tokensPerSecond.toFixed(1)} tokens/s`} ·
                    Tool calling: ${report.toolCallingWorks === true ? "Works" :
                      report.toolCallingWorks === false ? "Not working" : "Unavailable"}`}
                </p>
              )}
              {report?.status === "complete" && report.message && (
                <span className="settings-field-hint">{report.message}</span>
              )}
              {model.source === "server" && (
                <button
                  type="button"
                  className="settings-button"
                  aria-label={"Remove " + model.name}
                  disabled={busy || checkInProgress}
                  onClick={() => setPendingRemoval(model.name)}
                >
                  Remove
                </button>
              )}
            </div>
          );
        })}
        {pendingRemoval && (
          <div role="group" aria-label={"Confirm removal of " + pendingRemoval}>
            <p className="settings-update-status">
              Remove {pendingRemoval} from Ollama? This deletes the local model files.
            </p>
            <div className="settings-button-row">
              <button
                type="button"
                className="settings-button"
                disabled={busy}
                onClick={() => {
                  transport.fireIntent("app-settings", "removeOllamaModel", [pendingRemoval]);
                  setPendingRemoval(null);
                }}
              >
                Confirm remove
              </button>
              <button type="button" className="settings-button" onClick={() => setPendingRemoval(null)}>
                Keep model
              </button>
            </div>
          </div>
        )}
        {state.ollamaRemoveStatus === "running" && (
          <p role="status" className="settings-update-status">Removing {state.ollamaRemoveModel}...</p>
        )}
      </fieldset>

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

      <fieldset className="settings-fieldset">
        <legend>Context Window (per model)</legend>
        <p className="settings-update-status">
          Choose how many tokens Ollama serves for each model. The default is 8k; larger windows consume more memory.
        </p>
        <div className="settings-field">
          <span className="settings-field-label">Context model</span>
          <CustomSelect
            ariaLabel="Ollama context model"
            value={contextModel}
            placeholder="Scan or configure a model"
            disabled={contextModels.length === 0}
            options={contextModels.map((model) => ({ id: model, label: model }))}
            onChange={setSelectedContextModel}
          />
        </div>
        <div className="settings-field">
          <span className="settings-field-label">Context window (num_ctx)</span>
          <CustomSelect
            ariaLabel="Ollama context window"
            value={String(activeWindow)}
            disabled={!contextModel || optionLimit < 4096}
            options={OLLAMA_CONTEXT_CHOICES
              .filter((choice) => choice.tokens <= optionLimit)
              .map((choice) => ({ id: String(choice.tokens), label: choice.label }))}
            onChange={(value) => transport.fireIntent(
              "app-settings", "setOllamaContextWindow", [contextModel, Number(value)], undefined, true,
            )}
          />
        </div>
        {contextModel && !contextReady && (
          <p className="settings-update-status">
            {state.ollamaContextModel === contextModel && state.ollamaContextStatus === "loading"
              ? "Inspecting model context and memory..."
              : "Context options above 8k require a verified trained length from the shared Ollama inventory. Run System Scan."}
          </p>
        )}
        {contextReady && (
          <>
            <p className="settings-update-status">
              Trained model limit (shared inventory): {trainedLimit.toLocaleString()} tokens.
              Estimated {state.ollamaContextModelSizeBytes > 0 ? "model weights + KV cache" : "KV cache (weights unavailable)"}:
              {" "}{gib(memoryEstimate)} at {activeWindow.toLocaleString()} tokens.
            </p>
            <p className="settings-update-status">
              Available system RAM: {availableMemory > 0 ? gib(availableMemory) : "unavailable"}.
              GPU VRAM and other Ollama allocations are not included in this comparison.
            </p>
            {exceedsMemory && (
              <p className="settings-update-status" data-level="error" role="alert">
                Estimated Ollama memory exceeds available system RAM. This window may run out of memory.
              </p>
            )}
          </>
        )}
        {state.ollamaContextModel === contextModel && state.ollamaContextNotice && (
          <p className="settings-update-status" data-level="error">
            {state.ollamaContextNotice}
          </p>
        )}
      </fieldset>

      <fieldset className="settings-fieldset">
        <legend>Download recommended models</legend>
        <p className="settings-update-status">
          Sizes are approximate; nothing downloads until you click Pull.
        </p>
        {OLLAMA_RECOMMENDED_MODELS.map((model) => {
          const installed = installedModels.some((row) => row.name === model.id);
          return (
            <div className="settings-button-row" key={model.id}>
              <span>
                <strong>{model.id}</strong> — {model.purpose} (~{model.size})
              </span>
              <button
                type="button"
                className="settings-button"
                aria-label={"Pull " + model.id}
                disabled={busy || installed}
                onClick={() => transport.fireIntent("app-settings", "pullOllamaModel", [model.id])}
              >
                {installed ? "Installed" : "Pull"}
              </button>
            </div>
          );
        })}
      </fieldset>

      <label className="settings-field">
        <span className="settings-field-label">Validate and Pull Model</span>
        <input
          type="text"
          className="settings-select"
          list={OLLAMA_MODELS_DATALIST_ID}
          placeholder="Advanced model ID entry"
          aria-label="Validate and Pull Model"
          value={draftPullModel}
          onChange={(event) => setDraftPullModel(event.target.value)}
        />
      </label>
      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button settings-button-primary"
          disabled={draftPullModel.trim().length === 0 || busy}
          onClick={() => transport.fireIntent("app-settings", "pullOllamaModel", [draftPullModel.trim()])}
        >
          {pulling ? "Downloading..." : "Validate and Pull Model"}
        </button>
      </div>

      {pulling && (
        <div className="settings-field" aria-live="polite">
          <span className="settings-field-label">
            {state.ollamaPullModel}: {state.ollamaPullMessage}
            {percent !== null && (" — " + percent + "% of current layer (" +
              formatOllamaBytes(state.ollamaPullCompleted) + " / " +
              formatOllamaBytes(state.ollamaPullTotal) + ")")}
          </span>
          <progress
            aria-label={"Download progress for " + state.ollamaPullModel}
            max={state.ollamaPullTotal > 0 ? state.ollamaPullTotal : 1}
            value={state.ollamaPullTotal > 0 ? state.ollamaPullCompleted : undefined}
          />
          <div className="settings-button-row">
            <button
              type="button"
              className="settings-button"
              disabled={state.ollamaPullStatus === "cancelling"}
              onClick={() => transport.fireIntent("app-settings", "cancelOllamaPull", [])}
            >
              {state.ollamaPullStatus === "cancelling" ? "Cancelling..." : "Cancel download"}
            </button>
          </div>
        </div>
      )}
      {state.ollamaPullStatus === "done" && (
        <p role="status" className="settings-update-status">Downloaded {state.ollamaPullModel}.</p>
      )}
      {state.ollamaPullStatus === "cancelled" && (
        <p role="status" className="settings-update-status">Download cancelled.</p>
      )}

      {state.ollamaNotice && (
        <p className="settings-update-status" data-level="error">
          {state.ollamaNotice}
        </p>
      )}
    </div>
  );
}
