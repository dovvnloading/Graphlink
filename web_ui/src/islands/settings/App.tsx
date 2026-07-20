import { RefObject, useEffect, useRef, useState } from "react";
import {
  API_PROVIDERS,
  API_TASKS,
  API_TASK_LABELS,
  NOTIFICATION_TYPES,
  OLLAMA_TASKS,
  OLLAMA_TASK_LABELS,
  SECTION_NAMES,
  SettingsState,
  initialSettingsState,
} from "./bridgeTypes";
import { BridgeRejection, SettingsBridge, createSettingsBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

const THEME_OPTIONS = [
  { value: "dark", label: "Dark" },
  { value: "muted", label: "Muted" },
  { value: "mono", label: "Monochromatic" },
];

const NOTIFICATION_TYPE_LABELS: Record<(typeof NOTIFICATION_TYPES)[number], string> = {
  info: "Info",
  success: "Success",
  warning: "Warning",
  error: "Error",
};

interface GeneralPageProps {
  state: SettingsState;
  bridgeRef: RefObject<SettingsBridge | null>;
}

// Check for Updates / Open Repository are deliberately not here yet - both
// need a real window reference (increment 8's job), see
// graphlink_settings_bridge.py's module docstring. bridgeRef is passed as
// the ref object itself (not bridgeRef.current) - every intent call below
// happens inside an event handler, never during render, so reading
// .current there is the sanctioned pattern (react-hooks/refs only flags
// dereferencing a ref while rendering).
function GeneralPage({ state, bridgeRef }: GeneralPageProps) {
  return (
    <div className="settings-general-page">
      <label className="settings-field">
        <span className="settings-field-label">Theme</span>
        <select
          className="settings-select"
          value={state.theme}
          onChange={(event) => bridgeRef.current?.setTheme(event.target.value)}
        >
          {THEME_OPTIONS.map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.showTokenCounter}
          onChange={(event) => bridgeRef.current?.setShowTokenCounter(event.target.checked)}
        />
        Show Token Counter Overlay
      </label>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.enableSystemPrompt}
          onChange={(event) => bridgeRef.current?.setEnableSystemPrompt(event.target.checked)}
        />
        Enable Assistant System Prompt
      </label>

      <fieldset className="settings-fieldset">
        <legend>Notification types</legend>
        {NOTIFICATION_TYPES.map((type) => (
          <label className="settings-checkbox-row" key={type}>
            <input
              type="checkbox"
              checked={state.notificationPreferences[type] ?? true}
              onChange={(event) => bridgeRef.current?.setNotificationPreference(type, event.target.checked)}
            />
            {NOTIFICATION_TYPE_LABELS[type]}
          </label>
        ))}
      </fieldset>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.updateNotificationsEnabled}
          onChange={(event) => bridgeRef.current?.setUpdateNotificationsEnabled(event.target.checked)}
        />
        Enable Update Notifications on Startup
      </label>

      <p className="settings-update-status" data-level={state.updateStatusLevel}>
        {state.updateStatusMessage}
        {state.updateLastCheckedAt && ` (last checked: ${state.updateLastCheckedAt})`}
        {state.updateLatestVersion && ` (GitHub signal: ${state.updateLatestVersion})`}
      </p>

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button"
          disabled={state.updateCheckInProgress}
          onClick={() => bridgeRef.current?.checkForUpdates()}
        >
          {state.updateCheckInProgress ? "Checking..." : "Check for Updates"}
        </button>
        <button type="button" className="settings-button" onClick={() => bridgeRef.current?.openRepository()}>
          Open Repository
        </button>
      </div>
    </div>
  );
}

interface IntegrationsPageProps {
  state: SettingsState;
  bridgeRef: RefObject<SettingsBridge | null>;
}

// Write-only, by design (see graphlink_settings_bridge.py's module
// docstring): the token input always starts empty and is never pre-filled
// from state, since the bridge structurally cannot send the current value
// back. draftToken is local-only UI state for what the user is currently
// typing, cleared immediately after Save/Clear - it never becomes part of
// the published SettingsState.
function IntegrationsPage({ state, bridgeRef }: IntegrationsPageProps) {
  const [draftToken, setDraftToken] = useState("");

  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        Store optional external-service tokens used by specialized plugins. The Code Review plugin uses a
        GitHub personal access token to load your private repositories - if you leave this empty, it still
        works with public repositories.
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
            bridgeRef.current?.clearGithubToken();
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
            bridgeRef.current?.setGithubToken(draftToken);
            setDraftToken("");
          }}
        >
          Save Integrations
        </button>
      </div>
    </div>
  );
}

interface ApiPageProps {
  state: SettingsState;
  bridgeRef: RefObject<SettingsBridge | null>;
}

const API_MODELS_DATALIST_ID = "settings-api-available-models";

// Save Configuration is deliberately batched (unlike every other intent on
// this island) - draftBaseUrl/draftApiKey/draftTaskModels are local-only
// until Save, matching saveApiConfiguration()'s own atomic, all-or-nothing
// contract on the Python side (provider init must succeed before anything
// persists). setApiProvider is the one live intent here, same as the
// original provider_combo - it only changes which provider's fields
// display, so switching resets the drafts to that provider's own saved
// values via the "adjust state during render" pattern used elsewhere on
// this island.
function ApiPage({ state, bridgeRef }: ApiPageProps) {
  const [prevProvider, setPrevProvider] = useState(state.apiProvider);
  const [draftBaseUrl, setDraftBaseUrl] = useState(state.apiBaseUrl);
  const [draftApiKey, setDraftApiKey] = useState("");
  const [draftTaskModels, setDraftTaskModels] = useState<Record<string, string>>(state.apiTaskModels);

  if (state.apiProvider !== prevProvider) {
    setPrevProvider(state.apiProvider);
    setDraftBaseUrl(state.apiBaseUrl);
    setDraftApiKey("");
    setDraftTaskModels(state.apiTaskModels);
  }

  const isOpenAi = state.apiProvider === "OpenAI-Compatible";
  const isAnthropic = state.apiProvider === "Anthropic Claude";
  const keyConfigured = isOpenAi
    ? state.openaiKeyConfigured
    : isAnthropic
      ? state.anthropicKeyConfigured
      : state.geminiKeyConfigured;

  return (
    <div className="settings-general-page">
      <label className="settings-field">
        <span className="settings-field-label">API Provider</span>
        <select
          className="settings-select"
          value={state.apiProvider}
          onChange={(event) => bridgeRef.current?.setApiProvider(event.target.value)}
        >
          {API_PROVIDERS.map((provider) => (
            <option key={provider} value={provider}>
              {provider}
            </option>
          ))}
        </select>
      </label>

      {isOpenAi && (
        <label className="settings-field">
          <span className="settings-field-label">Base URL</span>
          <input
            className="settings-select"
            placeholder="https://api.openai.com/v1"
            value={draftBaseUrl}
            onChange={(event) => setDraftBaseUrl(event.target.value)}
          />
        </label>
      )}

      <label className="settings-field">
        <span className="settings-field-label">API Key</span>
        <input
          type="password"
          className="settings-select"
          placeholder="Enter your API key..."
          value={draftApiKey}
          onChange={(event) => setDraftApiKey(event.target.value)}
        />
      </label>

      <p className="settings-update-status">
        {keyConfigured ? "A key is currently configured for this provider." : "No key configured for this provider."}
      </p>

      {!isAnthropic && (
        <div className="settings-button-row">
          <button
            type="button"
            className="settings-button"
            disabled={draftApiKey.trim().length === 0 || state.apiLoadStatus === "running"}
            onClick={() => bridgeRef.current?.loadAvailableModels(draftApiKey)}
          >
            {state.apiLoadStatus === "running" ? "Loading catalog..." : "Load Available Models"}
          </button>
        </div>
      )}

      <datalist id={API_MODELS_DATALIST_ID}>
        {state.apiAvailableModels.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>

      {API_TASKS.map((task) => {
        if (task === "task_image_gen" && isAnthropic) {
          return (
            <p key={task} className="settings-update-status">
              Anthropic Claude does not support image generation in Graphlink yet.
            </p>
          );
        }
        return (
          <label className="settings-field" key={task}>
            <span className="settings-field-label">{API_TASK_LABELS[task]}</span>
            <input
              className="settings-select"
              list={API_MODELS_DATALIST_ID}
              value={draftTaskModels[task] ?? ""}
              onChange={(event) => setDraftTaskModels({ ...draftTaskModels, [task]: event.target.value })}
            />
          </label>
        );
      })}

      {state.notice && (
        <p className="settings-update-status" data-level="error">
          {state.notice}
        </p>
      )}

      <div className="settings-button-row">
        <button type="button" className="settings-button" onClick={() => bridgeRef.current?.resetApiSettings()}>
          Reset API Settings
        </button>
        <button
          type="button"
          className="settings-button settings-button-primary"
          onClick={() => {
            bridgeRef.current?.saveApiConfiguration({
              provider: state.apiProvider,
              baseUrl: draftBaseUrl,
              apiKey: draftApiKey,
              taskModels: draftTaskModels,
            });
            setDraftApiKey("");
          }}
        >
          Save Configuration
        </button>
      </div>
    </div>
  );
}

const OLLAMA_MODELS_DATALIST_ID = "settings-ollama-scanned-models";

interface OllamaTaskFieldProps {
  task: (typeof OLLAMA_TASKS)[number];
  value: string;
  bridgeRef: RefObject<SettingsBridge | null>;
}

// The flat wire value ("inherit"|"auto"|"<explicit model id>") maps onto
// two UI controls: a mode select (mirrors the original's 3 special combo
// entries) and a conditionally-shown text input for the explicit case
// (mirrors the original editable combo's free-text entry, including
// unavailable-model preservation - the input shows whatever string is
// persisted, matched against ollamaScannedModels or not). This is a
// deliberate 2-control simplification of the original's single editable
// combo, recorded in the plan doc rather than silently done.
function OllamaTaskField({ task, value, bridgeRef }: OllamaTaskFieldProps) {
  const isSpecial = value === "inherit" || value === "auto";
  const [uiMode, setUiMode] = useState(isSpecial ? value : "explicit");
  const [prevValue, setPrevValue] = useState(value);

  if (value !== prevValue) {
    setPrevValue(value);
    setUiMode(isSpecial ? value : "explicit");
  }

  return (
    <>
      <label className="settings-field">
        <span className="settings-field-label">{OLLAMA_TASK_LABELS[task]}</span>
        <select
          className="settings-select"
          value={uiMode}
          onChange={(event) => {
            const nextMode = event.target.value;
            setUiMode(nextMode);
            if (nextMode !== "explicit") {
              bridgeRef.current?.setOllamaModelAssignment(task, nextMode);
            }
          }}
        >
          {task !== "task_chat" && <option value="inherit">Use chat model</option>}
          <option value="auto">Auto - choose a compatible installed model</option>
          <option value="explicit">Custom model ID...</option>
        </select>
      </label>
      {uiMode === "explicit" && (
        <label className="settings-field">
          <span className="settings-field-label">{OLLAMA_TASK_LABELS[task]} (custom model ID)</span>
          <input
            className="settings-select"
            list={OLLAMA_MODELS_DATALIST_ID}
            value={isSpecial ? "" : value}
            onChange={(event) => bridgeRef.current?.setOllamaModelAssignment(task, event.target.value)}
          />
        </label>
      )}
    </>
  );
}

interface OllamaPageProps {
  state: SettingsState;
  bridgeRef: RefObject<SettingsBridge | null>;
}

function OllamaPage({ state, bridgeRef }: OllamaPageProps) {
  const [draftPullModel, setDraftPullModel] = useState("");

  return (
    <div className="settings-general-page">
      <fieldset className="settings-fieldset">
        <legend>Reasoning Mode</legend>
        <label className="settings-checkbox-row">
          <input
            type="radio"
            name="ollama-reasoning-mode"
            checked={state.ollamaReasoningMode === "Thinking"}
            onChange={() => bridgeRef.current?.setOllamaReasoningMode("Thinking")}
          />
          Thinking Mode (Enable CoT)
        </label>
        <label className="settings-checkbox-row">
          <input
            type="radio"
            name="ollama-reasoning-mode"
            checked={state.ollamaReasoningMode === "Quick"}
            onChange={() => bridgeRef.current?.setOllamaReasoningMode("Quick")}
          />
          Quick Mode (No CoT)
        </label>
      </fieldset>

      <p className="settings-update-status">
        Current Active Model: <strong>{state.ollamaCurrentModel || "Auto - no compatible installed model found"}</strong>
      </p>

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button"
          disabled={state.ollamaScanStatus === "running"}
          onClick={() => bridgeRef.current?.scanOllamaSystem()}
        >
          {state.ollamaScanStatus === "running" ? "Scanning..." : "System Scan"}
        </button>
        <button
          type="button"
          className="settings-button"
          disabled={state.ollamaScanStatus === "running"}
          onClick={() => bridgeRef.current?.pickOllamaScanFolder()}
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

      {OLLAMA_TASKS.map((task) => (
        <OllamaTaskField key={task} task={task} value={state.ollamaModelAssignments[task] ?? "auto"} bridgeRef={bridgeRef} />
      ))}

      <label className="settings-field">
        <span className="settings-field-label">Validate and Pull Model</span>
        <input
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
          className="settings-button"
          disabled={draftPullModel.trim().length === 0 || state.ollamaPullStatus === "running"}
          onClick={() => bridgeRef.current?.pullOllamaModel(draftPullModel)}
        >
          {state.ollamaPullStatus === "running" ? "Validating..." : "Validate and Pull Model"}
        </button>
      </div>

      {state.notice && (
        <p className="settings-update-status" data-level="error">
          {state.notice}
        </p>
      )}
    </div>
  );
}

interface LlamaCppPageProps {
  state: SettingsState;
  bridgeRef: RefObject<SettingsBridge | null>;
}

// Chat/title model paths are staged bridge-side, set only via the native
// file picker (pickLlamaCppChatModelFile/pickLlamaCppTitleModelFile) - see
// graphlink_settings_bridge.py's saveLlamaCppSettings docstring. Save
// Settings is the one action on this page that isn't a live, per-field
// apply - it mirrors the original widget's own Browse-fills /
// Save-persists-and-validates split, the same kind of deliberate exception
// saveApiConfiguration is elsewhere on this island.
function LlamaCppPage({ state, bridgeRef }: LlamaCppPageProps) {
  return (
    <div className="settings-general-page">
      <fieldset className="settings-fieldset">
        <legend>Reasoning Mode</legend>
        <label className="settings-checkbox-row">
          <input
            type="radio"
            name="llamacpp-reasoning-mode"
            checked={state.llamaCppReasoningMode === "Thinking"}
            onChange={() => bridgeRef.current?.setLlamaCppReasoningMode("Thinking")}
          />
          Thinking Mode (Enable CoT)
        </label>
        <label className="settings-checkbox-row">
          <input
            type="radio"
            name="llamacpp-reasoning-mode"
            checked={state.llamaCppReasoningMode === "Quick"}
            onChange={() => bridgeRef.current?.setLlamaCppReasoningMode("Quick")}
          />
          Quick Mode (No CoT)
        </label>
      </fieldset>

      <p className="settings-update-status">
        Current Active GGUF:{" "}
        <strong>{state.llamaCppChatModelPath ? state.llamaCppChatModelPath.split(/[\\/]/).pop() : "No model selected"}</strong>
      </p>

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button"
          disabled={state.llamaCppScanStatus === "running"}
          onClick={() => bridgeRef.current?.scanLlamaCppSystem()}
        >
          {state.llamaCppScanStatus === "running" ? "Scanning..." : "System Scan"}
        </button>
        <button
          type="button"
          className="settings-button"
          disabled={state.llamaCppScanStatus === "running"}
          onClick={() => bridgeRef.current?.pickLlamaCppScanFolder()}
        >
          Scan Folder...
        </button>
      </div>
      <p className="settings-update-status">{state.llamaCppScanSummary}</p>

      <div className="settings-field">
        <span className="settings-field-label">Chat Model File</span>
        <p className="settings-update-status">{state.llamaCppChatModelPath || "No file selected"}</p>
        <div className="settings-button-row">
          <button
            type="button"
            className="settings-button"
            onClick={() => bridgeRef.current?.pickLlamaCppChatModelFile()}
          >
            Browse...
          </button>
        </div>
      </div>

      <div className="settings-field">
        <span className="settings-field-label">Chat Naming File (optional)</span>
        <p className="settings-update-status">{state.llamaCppTitleModelPath || "Reusing the main chat model"}</p>
        <div className="settings-button-row">
          <button
            type="button"
            className="settings-button"
            onClick={() => bridgeRef.current?.pickLlamaCppTitleModelFile()}
          >
            Browse...
          </button>
        </div>
      </div>

      <label className="settings-field">
        <span className="settings-field-label">Chat Format Override</span>
        <input
          className="settings-select"
          placeholder="Leave blank to let the GGUF metadata decide"
          value={state.llamaCppChatFormat}
          onChange={(event) => bridgeRef.current?.setLlamaCppChatFormat(event.target.value)}
        />
      </label>

      <label className="settings-field">
        <span className="settings-field-label">Context Window</span>
        <input
          type="number"
          className="settings-select"
          min={256}
          max={131072}
          step={256}
          value={state.llamaCppNCtx}
          onChange={(event) => {
            const parsed = Number(event.target.value);
            if (!Number.isNaN(parsed)) bridgeRef.current?.setLlamaCppNCtx(parsed);
          }}
        />
      </label>

      <label className="settings-field">
        <span className="settings-field-label">GPU Layers</span>
        <input
          type="number"
          className="settings-select"
          min={-1}
          max={9999}
          value={state.llamaCppNGpuLayers}
          onChange={(event) => {
            const parsed = Number(event.target.value);
            if (!Number.isNaN(parsed)) bridgeRef.current?.setLlamaCppNGpuLayers(parsed);
          }}
        />
      </label>

      <label className="settings-field">
        <span className="settings-field-label">CPU Threads (0 = Auto)</span>
        <input
          type="number"
          className="settings-select"
          min={0}
          max={256}
          value={state.llamaCppNThreads}
          onChange={(event) => {
            const parsed = Number(event.target.value);
            if (!Number.isNaN(parsed)) bridgeRef.current?.setLlamaCppNThreads(parsed);
          }}
        />
      </label>

      {state.notice && (
        <p className="settings-update-status" data-level="error">
          {state.notice}
        </p>
      )}

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button settings-button-primary"
          onClick={() => bridgeRef.current?.saveLlamaCppSettings()}
        >
          Save Settings
        </button>
      </div>
    </div>
  );
}

function App() {
  const [state, setState] = useState<SettingsState>(initialSettingsState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const bridgeRef = useRef<SettingsBridge | null>(null);

  useEffect(() => {
    const bridge = createSettingsBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  if (rejection) {
    return (
      <BridgeErrorState
        title="Settings unavailable"
        rejection={rejection}
        className="settings-shell bridge-error"
      />
    );
  }

  return (
    <div className="settings-shell">
      <nav className="settings-rail" aria-label="Settings sections">
        {SECTION_NAMES.map((section) => (
          <button
            key={section}
            type="button"
            className="settings-rail-button"
            aria-current={section === state.activeSection ? "page" : undefined}
            onClick={() => bridgeRef.current?.setActiveSection(section)}
          >
            {section}
          </button>
        ))}
      </nav>
      <div className="settings-page" role="region" aria-label={state.activeSection}>
        {state.activeSection === "General" ? (
          <GeneralPage state={state} bridgeRef={bridgeRef} />
        ) : state.activeSection === "Integrations" ? (
          <IntegrationsPage state={state} bridgeRef={bridgeRef} />
        ) : state.activeSection === "API Endpoint" ? (
          <ApiPage state={state} bridgeRef={bridgeRef} />
        ) : state.activeSection === "Ollama (Local)" ? (
          <OllamaPage state={state} bridgeRef={bridgeRef} />
        ) : state.activeSection === "Llama.cpp (Local)" ? (
          <LlamaCppPage state={state} bridgeRef={bridgeRef} />
        ) : (
          state.activeSection
        )}
      </div>
    </div>
  );
}

export default App;
