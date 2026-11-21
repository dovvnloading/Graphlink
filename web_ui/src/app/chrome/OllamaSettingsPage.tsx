import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import { CustomSelect } from "./CustomSelect";
import { formatLocalModelBytes, ProviderModeSwitch, REASONING_LEVEL_OPTIONS } from "./LocalProviderSettings";

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
// Mirror the backend strict model-ID grammar; server validation is authoritative.
const VALID_OLLAMA_MODEL = /^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\/[A-Za-z0-9][A-Za-z0-9_.-]*){0,2}(?::[A-Za-z0-9][A-Za-z0-9_.-]*)?$/;
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
  checking: "Checking for the Ollama server…",
  running: "Connected",
  installed: "Installed, not responding",
  not_found: "Not detected",
};


type OllamaTask = (typeof OLLAMA_TASKS)[number];

// Keep the persisted value as-is: "auto" and "inherit" are special;
// a model ID from the server inventory is a direct selection, and any
// other ID stays editable through the custom fallback (including offline IDs).
function assignmentMode(value: string, installedModels: string[]): string {
  if (value === "auto" || value === "inherit") return value;
  return installedModels.includes(value) ? value : "custom";
}

function OllamaTaskField({
  task, value, installedModels, transport,
}: {
  task: OllamaTask;
  value: string;
  installedModels: InventoryModel[];
  transport: WsTransport;
}) {
  // Known embedding-only models cannot complete chat, naming, or other text
  // tasks. Unknown capabilities remain selectable rather than being guessed
  // incompatible when /api/show is unavailable.
  const chatModels = installedModels.filter((model) => !isEmbeddingOnly(model)).map((model) => model.name);
  const incompatible = installedModels.find((model) => model.name === value && isEmbeddingOnly(model));
  const resolvedMode = assignmentMode(value, chatModels);
  const [localMode, setLocalMode] = useState<string | null>(null);
  const [lastValue, setLastValue] = useState(value);
  const [draftId, setDraftId] = useState(resolvedMode === "custom" ? value : "");

  // An incoming authoritative assignment (for instance from another window)
  // supersedes local selection, without losing multi-character custom typing
  // while waiting for an echo from the backend.
  if (value !== lastValue) {
    setLastValue(value);
    setLocalMode(null);
    if (resolvedMode === "custom") setDraftId(value);
  }
  const uiMode = localMode ?? resolvedMode;
  const options = [
    ...(task !== "task_chat" ? [{ id: "inherit", label: "Use chat model" }] : []),
    { id: "auto", label: "Auto - choose a compatible installed model" },
    ...chatModels.map((name) => ({ id: name, label: name })),
    { id: "custom", label: "Custom model ID..." },
  ];

  const saveCustomId = () => {
    const id = draftId.trim();
    if (id && id !== value) {
      transport.fireIntent("app-settings", "setOllamaModelAssignment", [task, id], undefined, true);
    }
  };

  return (
    <>
      <div className="settings-field">
        <span className="settings-field-label">{OLLAMA_TASK_LABELS[task]}</span>
        <CustomSelect
          ariaLabel={OLLAMA_TASK_LABELS[task]}
          value={uiMode}
          options={options}
          onChange={(nextMode) => {
            setLocalMode(nextMode);
            if (nextMode === "custom") {
              setDraftId(resolvedMode === "custom" ? value : "");
            } else {
              transport.fireIntent("app-settings", "setOllamaModelAssignment", [task, nextMode], undefined, true);
            }
          }}
        />
      </div>
      {uiMode === "custom" && (
        <div className="settings-field">
          <label className="settings-field-label" htmlFor={`ollama-task-custom-${task}`}>
            {OLLAMA_TASK_LABELS[task]} (custom model ID)
          </label>
          <input
            id={`ollama-task-custom-${task}`}
            type="text"
            className="settings-select"
            list={OLLAMA_MODELS_DATALIST_ID}
            aria-label={OLLAMA_TASK_LABELS[task] + " (custom model ID)"}
            spellCheck={false}
            value={draftId}
            onChange={(event) => setDraftId(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                event.preventDefault();
                saveCustomId();
              }
            }}
          />
          <span className="settings-field-hint">
            Enter the exact model ID, then save. Choosing Auto clears a custom assignment.
          </span>
          <button type="button" className="settings-button"
            disabled={!draftId.trim() || draftId.trim() === value}
            onClick={saveCustomId}>Save {OLLAMA_TASK_LABELS[task]} ID</button>
        </div>
      )}
      {incompatible && (
        <p className="settings-update-status" role="alert" data-level="error">
          {value} is an embedding-only model and cannot generate text for {OLLAMA_TASK_LABELS[task].toLowerCase()}.
          Choose a chat-capable model instead.
        </p>
      )}
    </>
  );
}




type InventoryModel = AppSettingsState["ollamaInventory"][number];
type ModelCheck = AppSettingsState["ollamaModelChecks"][number];

function isEmbeddingOnly(model: InventoryModel): boolean {
  return model.capabilities.includes("embeddings") && !model.capabilities.includes("chat");
}

function OllamaModelCard({
  model, report, state, transport, busy, checkInProgress, onInspect, onRemoveRequest,
}: {
  model: InventoryModel;
  report?: ModelCheck;
  state: AppSettingsState;
  transport: WsTransport;
  busy: boolean;
  checkInProgress: boolean;
  onInspect: (name: string) => void;
  onRemoveRequest: (name: string) => void;
}) {
  const headingId = useId();
  const removeButtonRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  const onConfirmKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    setConfirming(false);
    removeButtonRef.current?.focus();
  };

  // Never trust folder-only metadata to unlock >8k windows. The server
  // inventory is the same verified /api/show snapshot Knowledge uses.
  const supportsContext = !isEmbeddingOnly(model);
  const trainedLimit = model.source === "server" && typeof model.trainedContextLength === "number"
    && model.trainedContextLength > 0 ? model.trainedContextLength : 0;
  const optionLimit = trainedLimit || 8192;
  const choices = OLLAMA_CONTEXT_CHOICES.filter((choice) => choice.tokens <= optionLimit);
  const savedWindow = state.ollamaContextWindows[model.name.toLowerCase()] ?? 8192;
  // The trained limit may not equal a preset (for example 6k). Never show
  // "Select…" for a value that cannot be chosen, or imply that clamping
  // silently changed the persisted preference.
  const activeWindow = choices.some((choice) => choice.tokens === savedWindow)
    ? savedWindow : (choices[choices.length - 1]?.tokens ?? 0);
  const windowNeedsSaving = activeWindow !== savedWindow;
  const contextReady = state.ollamaContextModel.toLowerCase() === model.name.toLowerCase()
    && state.ollamaContextStatus === "ready";
  const weightsKnown = contextReady && Number.isFinite(state.ollamaContextModelSizeBytes)
    && state.ollamaContextModelSizeBytes > 0;
  const estimateValid = contextReady && Number.isFinite(state.ollamaContextKvBytesPerToken)
    && state.ollamaContextKvBytesPerToken > 0;
  const memoryEstimate = estimateValid
    ? (weightsKnown ? state.ollamaContextModelSizeBytes : 0)
      + activeWindow * state.ollamaContextKvBytesPerToken
    : 0;
  const availableMemory = state.ollamaContextAvailableMemoryBytes;
  const exceedsMemory = weightsKnown && estimateValid && Number.isFinite(memoryEstimate)
    && availableMemory > 0 && memoryEstimate > availableMemory;
  const canRemove = model.source === "server" && state.ollamaInventorySource === "server"
    && state.ollamaServerReachable === true;
  const canCheck = canRemove;

  return (
    <article className="settings-ollama-model-card" role="group" aria-labelledby={headingId}
      onFocusCapture={() => { if (canCheck && supportsContext) onInspect(model.name); }}>
      <div className="settings-ollama-card-header">
        <h3 id={headingId} className="settings-ollama-model-name">{model.name}</h3>
        <span className="settings-ollama-state">
          {model.loaded === true ? "Loaded" : model.loaded === false ? "Not loaded" : "Load state unavailable"}
        </span>
      </div>
      <div className="settings-ollama-model-details">
        <span>{formatLocalModelBytes(model.sizeBytes)} on disk</span>
        <span>{model.family || "Unknown family"} · {model.parameterSize || "Unknown parameter size"} · {model.quantization || "Unknown quantization"}</span>
        <span>Trained context: {trainedLimit ? trainedLimit.toLocaleString() + " tokens" : "Unverified"}</span>
        {model.loaded === true && (
          <span>{formatLocalModelBytes(model.memoryBytes)} memory in use · {formatLocalModelBytes(model.vramBytes)} VRAM</span>
        )}
      </div>
      <div className="settings-ollama-capabilities" role="group" aria-label={"Capabilities for " + model.name}>
        {model.capabilities.length
          ? model.capabilities.map((capability) => (
            <span className="settings-ollama-capability" key={capability}>{capability}</span>
          ))
          : <span className="settings-field-hint">Capabilities unavailable</span>}
      </div>
      {model.source !== "server" && (
        <p className="settings-field-hint">Disk scan only. Start Ollama and scan to verify capabilities and memory.</p>
      )}
      {supportsContext && <div className="settings-field">
        <span className="settings-field-label">Context window (num_ctx)</span>
        <CustomSelect
          ariaLabel={"Context window for " + model.name}
          value={String(activeWindow)}
          disabled={choices.length === 0}
          options={choices.map((choice) => ({ id: String(choice.tokens), label: choice.label }))}
          onChange={(choice) => {
            if (canCheck) onInspect(model.name);
            transport.fireIntent("app-settings", "setOllamaContextWindow", [model.name, Number(choice)], undefined, true);
          }}
        />
      </div>}
      {supportsContext && windowNeedsSaving && (
        <p className="settings-field-hint" role="status">
          The saved {savedWindow.toLocaleString()}-token window is not one of this model's
          supported presets. {activeWindow
            ? "The displayed value is a suggestion; select it to save the new setting."
            : "No preset fits the trained limit (below 4,096 tokens)."}
        </p>
      )}
      {supportsContext && contextReady && activeWindow > 0 && (
        <div className="settings-ollama-memory-details">
          <p className="settings-update-status">
            Estimated {weightsKnown ? "model weights + KV cache" : "KV cache (weights unavailable)"}:
            {" "}{estimateValid && Number.isFinite(memoryEstimate) ? gib(memoryEstimate) : "unavailable"}
            {" "}at {activeWindow.toLocaleString()} tokens.
          </p>
          <p className="settings-update-status">
            Available system RAM: {availableMemory > 0 ? gib(availableMemory) : "unavailable"}.
            This estimate is not a measurement of system RAM use: GPU offload can reduce RAM usage;
            parallel requests, KV-cache precision and runtime overhead can change the total.
          </p>
          {!weightsKnown && (
            <p className="settings-field-hint">
              Model weight size is unavailable; the KV-only estimate cannot establish whether the model will fit.
            </p>
          )}
          {exceedsMemory && (
            <p className="settings-update-status" data-level="error" role="alert">
              Estimated model footprint exceeds available system RAM. This window may run out of memory
              depending on GPU offload and other allocations.
            </p>
          )}
        </div>
      )}
      {supportsContext && state.ollamaContextModel.toLowerCase() === model.name.toLowerCase()
        && state.ollamaContextStatus === "loading" && (
        <p className="settings-update-status">Inspecting model context and memory...</p>
      )}
      {supportsContext && !trainedLimit && (
        <p className="settings-field-hint">
          Context options above 8k require a verified trained length from the shared Ollama inventory.
        </p>
      )}
      {supportsContext && state.ollamaContextModel.toLowerCase() === model.name.toLowerCase() && state.ollamaContextNotice && (
        <p className="settings-update-status" role="alert" data-level="error">{state.ollamaContextNotice}</p>
      )}
      <div className="settings-button-row settings-ollama-card-actions">
        <button type="button" className="settings-button" aria-label={"Check " + model.name}
          disabled={checkInProgress || busy || !canCheck}
          title={!canCheck ? "A server-verified model and a running Ollama server are required to check a model" : undefined}
          onClick={() => transport.fireIntent("app-settings", "checkOllamaModel", [model.name])}>
          {report?.status === "running" ? "Checking..." : "Check"}
        </button>
        {report?.status === "running" && (
          <button type="button" className="settings-button" aria-label={"Cancel check " + model.name}
            onClick={() => transport.fireIntent("app-settings", "cancelOllamaModelCheck", [model.name])}>
            Cancel
          </button>
        )}
        {canRemove && (
          <button ref={removeButtonRef} type="button" className="settings-button"
            aria-label={"Remove " + model.name} disabled={busy || checkInProgress}
            onClick={() => setConfirming(true)}>Remove</button>
        )}
      </div>
      {report && (
        <p className="settings-update-status"
          role={report.status === "error" ? "alert" : "status"}
          data-level={report.status === "error" ? "error" :
            report.status === "complete" && (report.toolCallingWorks === false || report.message)
              ? "warning" : undefined}>
          {report.status === "running" ? "Checking model…" :
            report.status === "cancelled" ? "Check cancelled." :
            report.status === "error" ? report.message :
            "Sample load time: " + (report.loadSeconds == null ? "Unavailable" : report.loadSeconds.toFixed(2) + " s") +
            " · Sample generation speed: " + (report.tokensPerSecond == null ? "Unavailable" : report.tokensPerSecond.toFixed(1) + " tokens/s") +
            " · Tool probe: " + (report.toolCallingWorks === true ? "Tool call returned" :
              report.toolCallingWorks === false ? "No tool call observed" : "Inconclusive")}
          {report.status === "complete" && report.message && (" · " + report.message)}
        </p>
      )}
      {confirming && canRemove && (
        <div role="group" aria-label={"Confirm removal of " + model.name} className="settings-ollama-removal">
          <p className="settings-update-status">Remove {model.name} from Ollama? This deletes the local model files.</p>
          <div className="settings-button-row">
            <button ref={confirmRef} type="button" className="settings-button" disabled={busy}
              onKeyDown={onConfirmKeyDown}
              aria-label={"Confirm remove " + model.name}
              onClick={() => {
                onRemoveRequest(model.name);
                transport.fireIntent("app-settings", "removeOllamaModel", [model.name]);
                setConfirming(false);
              }}>Confirm remove</button>
            <button type="button" className="settings-button" onKeyDown={onConfirmKeyDown} onClick={() => {
              setConfirming(false);
              removeButtonRef.current?.focus();
            }}>Keep model</button>
          </div>
        </div>
      )}
    </article>
  );
}

export function OllamaSettingsPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {

  const [draftPullModel, setDraftPullModel] = useState("");
  const [inspectedModel, setInspectedModel] = useState("");
  const [lastRemovedModel, setLastRemovedModel] = useState("");
  const removeFeedbackRef = useRef<HTMLParagraphElement>(null);
  const checkInProgress = state.ollamaModelChecks.some((check) => check.status === "running");
  const pulling = ["running", "cancelling", "finalizing"].includes(state.ollamaPullStatus);
  const busy = pulling || state.ollamaRemoveStatus === "running" || state.ollamaScanStatus === "running";
  const installedModels = state.ollamaInventorySource === "server"
    ? state.ollamaInventory.filter((model) => model.source === "server")
    : [];
  const installedModelNames = installedModels.map((model) => model.name);
  const canDownload = state.ollamaServerStatus === "running" && state.ollamaServerReachable === true;
  const contextModel = state.ollamaInventory.find((model) => model.name === inspectedModel)?.name
    ?? state.ollamaInventory.find((model) => model.name === state.ollamaCurrentModel)?.name
    ?? state.ollamaInventory[0]?.name ?? "";
  const selectedWindow = state.ollamaContextWindows[contextModel.toLowerCase()] ?? 8192;
  const percent = state.ollamaPullTotal > 0
    ? Math.min(100, Math.round((100 * state.ollamaPullCompleted) / state.ollamaPullTotal))
    : null;

  useEffect(() => {
    const model = installedModels.find((entry) => entry.name === contextModel);
    if (model && canDownload && !isEmbeddingOnly(model)) {
      transport.fireIntent("app-settings", "inspectOllamaContextModel", [contextModel]);
    }
  // The backend returns an effective per-token KV cost for the saved window;
  // re-inspect after a changed preset instead of extrapolating past a sliding cap.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextModel, transport, state.ollamaScanStatus, canDownload, selectedWindow]);

  useEffect(() => {
    if (state.ollamaRemoveStatus === "done" && lastRemovedModel && document.activeElement === document.body) {
      removeFeedbackRef.current?.focus();
    }
  }, [state.ollamaRemoveStatus, lastRemovedModel]);

  // Refresh the shared Ollama/Knowledge inventory on page open. Only explicit
  // Pull and Remove clicks mutate models installed on the daemon.
  useEffect(() => {
    transport.fireIntent("app-settings", "scanOllamaSystem", []);
  }, [transport]);


  return (
    <div className="settings-general-page">
      <ProviderModeSwitch mode={MODE_OLLAMA_LOCAL} state={state} transport={transport} />

      <p className="settings-update-status" role="status" aria-label="Ollama server status">
        Ollama server: {OLLAMA_SERVER_STATUS_LABELS[state.ollamaServerStatus] ?? "Status unavailable"}
      </p>
      {state.ollamaServerStatus === "not_found" && (
        <p className="settings-update-status">
          Graphlink could not detect Ollama on this computer.{" "}
          <a href="https://ollama.com/download" target="_blank" rel="noopener noreferrer">
            Install Ollama (official download)
          </a>, launch the application, then select Check Ollama status. Graphlink does not install it for you.
        </p>
      )}
      {state.ollamaServerStatus === "installed" && (
        <p className="settings-update-status">
          Ollama is installed, but its server is not responding. Select Start Ollama to launch it.
          If you started Ollama outside Graphlink, select Check Ollama status instead.
        </p>
      )}
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
      <div className="settings-button-row">
        <button type="button" className="settings-button"
          disabled={state.ollamaStartStatus === "starting"}
          onClick={() => transport.intent("app-settings", "checkOllamaStatus", [])}>
          Check Ollama status
        </button>
      </div>
      {state.ollamaStartStatus === "starting" && (
        <p className="settings-update-status" role="status">
          Starting Ollama and waiting for its server to respond…
        </p>
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
          onClick={() => transport.fireIntent("app-settings", "pickOllamaScanFolder", [])}
        >
          Scan model folder…
        </button>
      </div>
      <p className="settings-update-status">{state.ollamaScanSummary}</p>
      {state.ollamaScanStatus === "running" && (
        <p className="settings-update-status" role="status">Scanning for Ollama models…</p>
      )}
      {state.ollamaInventorySource === "folder" && (
        <p className="settings-field-hint">
          Models were found on disk. With Ollama running, select Refresh installed models to verify capabilities and memory use.
        </p>
      )}

      <fieldset className="settings-fieldset">
        <legend>Local model inventory</legend>
        <p className="settings-field-hint">
          Check temporarily loads a server-verified model and runs two short prompts.
          It reports sample load and generation speed plus whether the model returned a test tool call.
          No tool is executed and no Graphlink model assignment is changed.
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
          <p role="status" className="settings-update-status" data-level="warning">
            Ollama is not responding. Start the installed server or check its status, then refresh models.
            Disk-only entries cannot be checked or removed here.
          </p>
        )}
        {state.ollamaInventory.length === 0 && (
          <p className="settings-field-hint">
            {state.ollamaInventorySource === "server"
              ? "No models installed in this Ollama server. Download a recommended chat model below to begin."
              : "No models verified yet. Start Ollama and refresh, or scan a model folder for offline information."}
          </p>
        )}

        <div className="settings-ollama-model-list">
          {state.ollamaInventory.map((model) => (
            <OllamaModelCard key={model.name} model={model}
              report={state.ollamaModelChecks.find((check) => check.name === model.name)}
              state={state} transport={transport} busy={busy} checkInProgress={checkInProgress}
              onInspect={setInspectedModel} onRemoveRequest={setLastRemovedModel}
            />
          ))}
        </div>
        {state.ollamaRemoveStatus === "running" && (
          <p role="status" className="settings-update-status">Removing {state.ollamaRemoveModel}…</p>
        )}
        {state.ollamaRemoveStatus === "done" && lastRemovedModel && (
          <p ref={removeFeedbackRef} tabIndex={-1} role="status" className="settings-update-status">
            Removed {lastRemovedModel} from Ollama.
          </p>
        )}


      </fieldset>

      <datalist id={OLLAMA_MODELS_DATALIST_ID}>
        {state.ollamaScannedModels.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>

      <fieldset className="settings-fieldset">
        <legend>Model Selection (per task)</legend>
        <p className="settings-field-hint">
          Known embedding-only models cannot be assigned to text-generation tasks.
          Selecting a model here changes the task assignment; downloading or checking one does not.
        </p>
        {OLLAMA_TASKS.map((task) => (
          <OllamaTaskField
            key={task}
            task={task}
            value={state.ollamaModelAssignments[task] ?? "auto"}
            installedModels={installedModels}
            transport={transport}
          />
        ))}
      </fieldset>



      <fieldset className="settings-fieldset">
        <legend>Download recommended models</legend>
        <p className="settings-update-status">
          Sizes are approximate. Downloading is opt-in and does not change your selected chat model.
        </p>
        {OLLAMA_RECOMMENDED_MODELS.map((model) => {
          const installed = installedModelNames.includes(model.id);
          return (
            <div className="settings-button-row" key={model.id}>
              <span>
                <strong>{model.id}</strong> — {model.purpose} (~{model.size})
              </span>
              <button
                type="button"
                className="settings-button"
                aria-label={"Pull " + model.id}
                disabled={busy || installed || !canDownload}
                title={!canDownload ? "Start or connect to Ollama before downloading models" : undefined}
                onClick={() => transport.fireIntent("app-settings", "pullOllamaModel", [model.id])}
              >
                {installed ? "Installed" : "Pull"}
              </button>
            </div>
          );
        })}
      </fieldset>

      {!canDownload && (
        <p className="settings-field-hint">
          Downloads require a reachable Ollama server. Install and start Ollama above before pulling models.
        </p>
      )}
      <label className="settings-field">
        <span className="settings-field-label">Model ID to download</span>
        <input
          type="text"
          className="settings-select"
          list={OLLAMA_MODELS_DATALIST_ID}
          placeholder="Advanced model ID entry"
          aria-label="Model ID to download"
          value={draftPullModel}
          onChange={(event) => setDraftPullModel(event.target.value)}
        />
      </label>
      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button settings-button-primary"
          disabled={!VALID_OLLAMA_MODEL.test(draftPullModel.trim())
            || draftPullModel.trim().length > 255 || busy || !canDownload}
          onClick={() => transport.fireIntent("app-settings", "pullOllamaModel", [draftPullModel.trim()])}
        >
          {pulling ? "Downloading…" : "Download model"}
        </button>
      </div>

      {pulling && (
        <div className="settings-field" aria-live="polite">
          <span className="settings-field-label">
            {state.ollamaPullModel}: {state.ollamaPullMessage}
            {percent !== null && (" — " + percent + "% of current layer (" +
              formatLocalModelBytes(state.ollamaPullCompleted) + " / " +
              formatLocalModelBytes(state.ollamaPullTotal) + ")")}
          </span>
          <progress
            aria-label={"Download progress for " + state.ollamaPullModel}
            max={state.ollamaPullTotal > 0 ? state.ollamaPullTotal : 1}
            value={state.ollamaPullTotal > 0 ? state.ollamaPullCompleted : undefined}
          />
          {state.ollamaPullStatus !== "finalizing" && (
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
          )}
        </div>
      )}
      {state.ollamaPullStatus === "done" && (
        <p role="status" className="settings-update-status">Downloaded {state.ollamaPullModel}.</p>
      )}
      {state.ollamaPullStatus === "cancelled" && (
        <p role="status" className="settings-update-status">Download cancelled.</p>
      )}

      {state.ollamaNotice && (
        <p className="settings-update-status" role="alert" data-level="error">
          {state.ollamaNotice}
        </p>
      )}
    </div>
  );
}
