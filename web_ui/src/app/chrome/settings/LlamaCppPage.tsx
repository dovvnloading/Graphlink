import { useId, useState, type ReactNode } from "react";
import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";
import { Select } from "../../ui/Select";
import { NumberField } from "../../ui/NumberField";
import { TextField } from "../../ui/TextField";
import { Button } from "../../ui/Button";
import { FieldGroup, FormRow } from "../../ui/Field";
import { SegmentedControl } from "../../ui/SegmentedControl";
import { Spinner } from "../../ui/Spinner";
import { formatLocalModelBytes, ProviderModeSwitch, REASONING_LEVEL_OPTIONS } from "../LocalProviderSettings";

const LLAMA_CPP_MODELS_DATALIST_ID = "settings-llama-cpp-scanned-models";

// Llama.cpp has no per-task assignment concept like Ollama's OLLAMA_TASKS -
// just one global chat model path plus an optional title/naming override
// (api_provider.py's _get_llama_cpp_model_path: chart/web-validate/web-
// summarize all silently fall back to the chat model path).
function basename(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

const MODE_LLAMACPP_LOCAL = "Llama.cpp (Local)";

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
    <NumberField label={label} value={draft.trim() ? Number(draft) : ""}
      min={min} max={max} step={step} placeholder={placeholder}
      onChange={(event) => {
        const text = event.currentTarget.value;
        setDraft(text);
        const parsed = Number(text);
        if (text.trim() && Number.isFinite(parsed)) onCommit(parsed);
      }}
      onValueChange={(next) => {
        setDraft(String(next));
        if (next !== "") onCommit(next);
      }}
    />
  );

}

type LlamaCppModel = AppSettingsState["llamaCppModels"][number];

function LlamaCppModelCard({
  path, label, model, children,
}: {
  path: string;
  label: string;
  model?: LlamaCppModel;
  children?: ReactNode;
}) {
  const headingId = useId();
  const name = path ? basename(path) : "No chat model selected";
  const trained = model?.trainedContextLength;
  return (
    <article className="settings-ollama-model-card" role="group" aria-labelledby={headingId}>
      <div className="settings-ollama-card-header">
        <h3 id={headingId} className="settings-ollama-model-name">{name}</h3>
        <span className="settings-ollama-state">{label}</span>
      </div>
      {path ? (
        <>
          <div className="settings-ollama-model-details">
            <span>{formatLocalModelBytes(model?.sizeBytes)} on disk</span>
            <span>{model?.quantization || "Unknown quantization"}</span>
            <span>Trained context: {typeof trained === "number" && trained > 0
              ? trained.toLocaleString() + " tokens" : "Unverified"}</span>
          </div>
          <div className="settings-ollama-capabilities" aria-label={"Capabilities for " + name}>
            {model?.capabilities.length ? model.capabilities.map((capability) => (
              <span className="settings-ollama-capability" key={capability}>{capability}</span>
            )) : <span className="settings-field-hint">Capabilities unavailable</span>}
          </div>
        </>
      ) : <p className="settings-field-hint">Choose a chat GGUF file to see its metadata.</p>}
      {children}
    </article>
  );
}

export function LlamaCppPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
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
      <h2 className="settings-page-title">Llama.cpp</h2>
      <p className="settings-page-description">Configure your local GGUF models, runtime and reasoning preferences.</p>
      {/* Compares against the PERSISTED Llama.cpp config, same as
          apply_provider_mode's own Llama.cpp branch reads
          (settings_manager.get_llama_cpp_settings()) - if the model path
          fields below have unsaved edits, switching here still activates
          the last-saved config, not the draft on screen. Save Settings
          first to switch to what you just typed. */}
      <ProviderModeSwitch mode={MODE_LLAMACPP_LOCAL} state={state} transport={transport} />

      <FieldGroup title="Reasoning Level">
        <FormRow label="Reasoning level" layout="inline">
          <SegmentedControl label="Reasoning level"
            options={REASONING_LEVEL_OPTIONS} value={state.llamaCppReasoningLevel}
            onChange={(value) => transport.fireIntent("app-settings", "setLlamaCppReasoningLevel", [value], undefined, true)} />
        </FormRow>
      </FieldGroup>

      <p className="settings-update-status">
        Current Active GGUF: <strong>{state.llamaCppChatModelPath ? basename(state.llamaCppChatModelPath) : "No model selected"}</strong>
      </p>

      <div className="settings-button-row">
        <Button variant="secondary"
          disabled={state.llamaCppScanStatus === "running"}
          onClick={() => transport.fireIntent("app-settings", "scanLlamaCppSystem", [])}
        >
          {state.llamaCppScanStatus === "running" ? "Scanning…" : "System Scan"}
        </Button>
        <Button variant="secondary"
          disabled={state.llamaCppScanStatus === "running"}
          onClick={() => transport.fireIntent("app-settings", "pickLlamaCppScanFolder", [])}
        >
          Scan folder…
        </Button>
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
          <Select
            ariaLabel="Scanned Chat Model"
            value={scannedChatValue}
            options={[
              { value: "", label: "Select a scanned model…" },
              ...state.llamaCppScannedModels.map((path) => ({ value: path, label: basename(path) })),
            ]}
            onChange={(path) => transport.fireIntent("app-settings", "setLlamaCppChatModelPath", [path], undefined, true)}
          />
        </div>
      )}

      <div className="settings-field">
        <span className="settings-field-label">Chat Model File</span>
        <span className="settings-update-status">{state.llamaCppChatModelPath || "No file selected"}</span>
        <Button variant="secondary"
          onClick={() => transport.fireIntent("app-settings", "pickLlamaCppChatModelFile", [])}
        >
          Browse…
        </Button>
      </div>

      {state.llamaCppScannedModels.length > 0 && (
        <div className="settings-field">
          <span className="settings-field-label">Scanned Naming Model</span>
          <Select
            ariaLabel="Scanned Naming Model"
            value={scannedTitleValue}
            options={[
              { value: "", label: "Select a scanned model…" },
              ...state.llamaCppScannedModels.map((path) => ({ value: path, label: basename(path) })),
            ]}
            onChange={(path) => transport.fireIntent("app-settings", "setLlamaCppTitleModelPath", [path], undefined, true)}
          />
        </div>
      )}

      <div className="settings-field">
        <span className="settings-field-label">Chat Naming File (optional)</span>
        <span className="settings-update-status">{state.llamaCppTitleModelPath || "Reusing the main chat model"}</span>
        <Button variant="secondary"
          onClick={() => transport.fireIntent("app-settings", "pickLlamaCppTitleModelFile", [])}
        >
          Browse…
        </Button>
      </div>

      <div className="settings-field">
        <span className="settings-field-label">Configured GGUF models</span>
        <div className="settings-ollama-model-list" role="group" aria-label="Configured GGUF models">
          <LlamaCppModelCard
            path={state.llamaCppChatModelPath}
            label={state.llamaCppTitleModelPath && state.llamaCppTitleModelPath !== state.llamaCppChatModelPath
              ? "Chat model" : "Chat / naming model"}
            model={state.llamaCppModels.find((model) => model.path === state.llamaCppChatModelPath)}
          >
            <LlamaCppNumberField
              label="Context Window"
              value={state.llamaCppNCtx}
              min={256}
              max={131072}
              step={256}
              onCommit={(n) => transport.fireIntent("app-settings", "setLlamaCppNCtx", [n], undefined, true)}
            />
            <p className="settings-field-hint">
              This context window is shared by llama.cpp's chat and naming models.
            </p>
          </LlamaCppModelCard>
          {state.llamaCppTitleModelPath && state.llamaCppTitleModelPath !== state.llamaCppChatModelPath && (
            <LlamaCppModelCard
              path={state.llamaCppTitleModelPath}
              label="Naming model"
              model={state.llamaCppModels.find((model) => model.path === state.llamaCppTitleModelPath)}
            />
          )}
        </div>
        <p className="settings-field-hint">
          Details come from local GGUF headers; capabilities and context are not runtime-verified.
        </p>
      </div>

      <TextField label="Chat Format Override" type="text"
        placeholder="Leave blank to let the GGUF metadata decide" value={draftChatFormat} mono
        onValueChange={(value) => {
          setDraftChatFormat(value);
          transport.fireIntent("app-settings", "setLlamaCppChatFormat", [value], undefined, true);
        }} />

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
        <Button variant="primary"
          onClick={() => transport.fireIntent("app-settings", "saveLlamaCppSettings", [])}
        >
          Save Settings
        </Button>
      </div>
    </div>
  );
}
