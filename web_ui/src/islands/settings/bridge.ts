import { SettingsState, initialSettingsState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateSettingsState } from "../../lib/bridge-core/generated/settings-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: SettingsState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

export interface SaveApiConfigurationArgs {
  provider: string;
  baseUrl: string;
  apiKey: string;
  taskModels: Record<string, string>;
}

interface QtSettingsObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  setActiveSection: (section: string) => void;
  setTheme: (theme: string) => void;
  setShowTokenCounter: (enabled: boolean) => void;
  setEnableSystemPrompt: (enabled: boolean) => void;
  setNotificationPreference: (notificationType: string, enabled: boolean) => void;
  setUpdateNotificationsEnabled: (enabled: boolean) => void;
  checkForUpdates: () => void;
  openRepository: () => void;
  setGithubToken: (token: string) => void;
  clearGithubToken: () => void;
  setApiProvider: (provider: string) => void;
  saveApiConfiguration: (configJson: string) => void;
  loadAvailableModels: (apiKey: string) => void;
  resetApiSettings: () => void;
  setOllamaReasoningMode: (mode: string) => void;
  setOllamaModelAssignment: (task: string, value: string) => void;
  scanOllamaSystem: () => void;
  pickOllamaScanFolder: () => void;
  pullOllamaModel: (modelName: string) => void;
  setLlamaCppReasoningMode: (mode: string) => void;
  setLlamaCppChatFormat: (chatFormat: string) => void;
  setLlamaCppNCtx: (nCtx: number) => void;
  setLlamaCppNGpuLayers: (nGpuLayers: number) => void;
  setLlamaCppNThreads: (nThreads: number) => void;
  pickLlamaCppChatModelFile: () => void;
  pickLlamaCppTitleModelFile: () => void;
  setLlamaCppChatModelPath: (path: string) => void;
  setLlamaCppTitleModelPath: (path: string) => void;
  scanLlamaCppSystem: () => void;
  pickLlamaCppScanFolder: () => void;
  saveLlamaCppSettings: () => void;
}

export interface SettingsBridge {
  ready(): void;
  setActiveSection(section: string): void;
  setTheme(theme: string): void;
  setShowTokenCounter(enabled: boolean): void;
  setEnableSystemPrompt(enabled: boolean): void;
  setNotificationPreference(notificationType: string, enabled: boolean): void;
  setUpdateNotificationsEnabled(enabled: boolean): void;
  checkForUpdates(): void;
  openRepository(): void;
  setGithubToken(token: string): void;
  clearGithubToken(): void;
  setApiProvider(provider: string): void;
  saveApiConfiguration(args: SaveApiConfigurationArgs): void;
  loadAvailableModels(apiKey: string): void;
  resetApiSettings(): void;
  setOllamaReasoningMode(mode: string): void;
  setOllamaModelAssignment(task: string, value: string): void;
  scanOllamaSystem(): void;
  pickOllamaScanFolder(): void;
  pullOllamaModel(modelName: string): void;
  setLlamaCppReasoningMode(mode: string): void;
  setLlamaCppChatFormat(chatFormat: string): void;
  setLlamaCppNCtx(nCtx: number): void;
  setLlamaCppNGpuLayers(nGpuLayers: number): void;
  setLlamaCppNThreads(nThreads: number): void;
  pickLlamaCppChatModelFile(): void;
  pickLlamaCppTitleModelFile(): void;
  setLlamaCppChatModelPath(path: string): void;
  setLlamaCppTitleModelPath(path: string): void;
  scanLlamaCppSystem(): void;
  pickLlamaCppScanFolder(): void;
  saveLlamaCppSettings(): void;
  dispose(): void;
}

/**
 * Each intent applies and republishes immediately - see
 * graphlink_settings_bridge.py's module docstring for why this departs
 * from the original AppearanceSettingsWidget's single batched "Apply"
 * button.
 */
function parseState(payload: string) {
  return parseIslandState(payload, validateSettingsState);
}

class MockSettingsBridge implements SettingsBridge {
  private state: SettingsState = structuredClone(initialSettingsState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  private publish(next: Partial<SettingsState>) {
    this.state = { ...this.state, ...next, revision: this.state.revision + 1 };
    this.listener(this.state);
  }

  ready(): void {
    this.listener(this.state);
  }

  setActiveSection(section: string): void {
    this.publish({ activeSection: section });
  }

  setTheme(theme: string): void {
    this.publish({ theme });
  }

  setShowTokenCounter(enabled: boolean): void {
    this.publish({ showTokenCounter: enabled });
  }

  setEnableSystemPrompt(enabled: boolean): void {
    this.publish({ enableSystemPrompt: enabled });
  }

  setNotificationPreference(notificationType: string, enabled: boolean): void {
    this.publish({
      notificationPreferences: { ...this.state.notificationPreferences, [notificationType]: enabled },
    });
  }

  setUpdateNotificationsEnabled(enabled: boolean): void {
    this.publish({ updateNotificationsEnabled: enabled });
  }

  checkForUpdates(): void {
    // No real UpdateCheckWorker in the mock - simulates an immediate,
    // successful check so the dev-mode UI has something to show.
    this.publish({
      updateCheckInProgress: false,
      updateStatusMessage: "You're up to date.",
      updateStatusLevel: "success",
      updateLatestVersion: "0.0.0-dev",
    });
  }

  openRepository(): void {
    // No real browser to open in the mock/test environment - a no-op,
    // same treatment as pickOllamaScanFolder's native-dialog stand-in.
  }

  setGithubToken(token: string): void {
    // Mirrors the real bridge's write-only contract even in the mock: the
    // token value itself is never retained in state, only whether one was
    // set - so a dev-mode/test consumer can't come to rely on getting it
    // back, a behavior the real bridge structurally cannot provide.
    this.publish({ githubTokenConfigured: token.trim().length > 0 });
  }

  clearGithubToken(): void {
    this.publish({ githubTokenConfigured: false });
  }

  setApiProvider(provider: string): void {
    const isGemini = provider === "Google Gemini";
    this.publish({
      apiProvider: provider,
      apiLoadStatus: "idle",
      notice: null,
      apiAvailableModels: isGemini ? ["gemini-2.5-flash", "gemini-2.5-pro"] : [],
      // Faithful to the real bridge: Gemini's image task takes a distinct
      // curated list, empty for every other provider.
      apiImageModels: isGemini ? ["gemini-2.5-flash-image", "gemini-3.1-flash-image-preview"] : [],
      apiTaskModels: {},
    });
  }

  saveApiConfiguration(args: SaveApiConfigurationArgs): void {
    // A lightweight stand-in for saveApiConfiguration()'s real validation -
    // enough to exercise the UI meaningfully in npm run dev / jsdom smoke
    // tests, not a faithful reimplementation of the Python-side logic
    // (that's covered by tests/test_settings_bridge_api_page.py against
    // the real bridge).
    if (args.provider === "OpenAI-Compatible" && !args.baseUrl.trim()) {
      this.publish({ notice: "Please enter the Base URL for the OpenAI-compatible provider." });
      return;
    }
    if (!args.apiKey.trim()) {
      this.publish({ notice: "Please enter your API Key." });
      return;
    }
    const keyField =
      args.provider === "OpenAI-Compatible"
        ? "openaiKeyConfigured"
        : args.provider === "Anthropic Claude"
          ? "anthropicKeyConfigured"
          : "geminiKeyConfigured";
    this.publish({
      apiProvider: args.provider,
      apiBaseUrl: args.baseUrl,
      apiTaskModels: args.taskModels,
      notice: null,
      [keyField]: true,
    });
  }

  loadAvailableModels(apiKey: string): void {
    if (!apiKey.trim()) {
      this.publish({ notice: "Please enter the API Key." });
      return;
    }
    this.publish({ apiLoadStatus: "done", apiAvailableModels: ["gpt-4o", "gpt-4o-mini"], notice: null });
  }

  resetApiSettings(): void {
    this.publish({
      apiProvider: "OpenAI-Compatible",
      apiBaseUrl: "https://api.openai.com/v1",
      openaiKeyConfigured: false,
      anthropicKeyConfigured: false,
      geminiKeyConfigured: false,
      apiTaskModels: {},
      apiAvailableModels: [],
      apiImageModels: [],
      apiLoadStatus: "idle",
      notice: null,
    });
  }

  setOllamaReasoningMode(mode: string): void {
    this.publish({ ollamaReasoningMode: mode });
  }

  setOllamaModelAssignment(task: string, value: string): void {
    this.publish({
      ollamaModelAssignments: { ...this.state.ollamaModelAssignments, [task]: value || "auto" },
      ollamaCurrentModel: task === "task_chat" && value ? value : this.state.ollamaCurrentModel,
    });
  }

  scanOllamaSystem(): void {
    this.publish({
      ollamaScanStatus: "done",
      ollamaScannedModels: ["llama3:8b", "mistral:7b"],
      ollamaScanSummary: "Using saved system scan results from local Ollama locations.",
    });
  }

  pickOllamaScanFolder(): void {
    // The mock has no real native file dialog to show - treated the same
    // as a real "picker cancelled" outcome (a no-op), since there's no
    // meaningful dev-mode stand-in for a native OS folder picker.
  }

  pullOllamaModel(modelName: string): void {
    if (!modelName.trim()) {
      this.publish({ notice: "Model name cannot be empty." });
      return;
    }
    this.publish({ ollamaPullStatus: "done", ollamaCurrentModel: modelName, notice: null });
  }

  setLlamaCppReasoningMode(mode: string): void {
    this.publish({ llamaCppReasoningMode: mode });
  }

  setLlamaCppChatFormat(chatFormat: string): void {
    this.publish({ llamaCppChatFormat: chatFormat });
  }

  setLlamaCppNCtx(nCtx: number): void {
    this.publish({ llamaCppNCtx: nCtx });
  }

  setLlamaCppNGpuLayers(nGpuLayers: number): void {
    this.publish({ llamaCppNGpuLayers: nGpuLayers });
  }

  setLlamaCppNThreads(nThreads: number): void {
    this.publish({ llamaCppNThreads: nThreads });
  }

  pickLlamaCppChatModelFile(): void {
    // No real native file dialog to show in the mock - same "cancelled
    // picker" no-op treatment as pickOllamaScanFolder above.
  }

  pickLlamaCppTitleModelFile(): void {
    // See pickLlamaCppChatModelFile.
  }

  setLlamaCppChatModelPath(path: string): void {
    this.publish({ llamaCppChatModelPath: path.trim() });
  }

  setLlamaCppTitleModelPath(path: string): void {
    this.publish({ llamaCppTitleModelPath: path.trim() });
  }

  scanLlamaCppSystem(): void {
    this.publish({
      llamaCppScanStatus: "done",
      llamaCppScannedModels: ["/models/chat.gguf", "/models/title.gguf"],
      llamaCppScanSummary: "Using saved system scan results from common local model folders.",
    });
  }

  pickLlamaCppScanFolder(): void {
    // See pickLlamaCppChatModelFile.
  }

  saveLlamaCppSettings(): void {
    // Lightweight stand-in, same caveat as saveApiConfiguration above - the
    // real path-existence/.gguf validation lives in
    // tests/test_settings_bridge_llamacpp_page.py against the real bridge.
    if (!this.state.llamaCppChatModelPath.trim()) {
      this.publish({ notice: "Chat Model File cannot be empty." });
      return;
    }
    this.publish({ notice: null });
  }

  dispose(): void {}
}

export function createSettingsBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): SettingsBridge {
  const fallback = new MockSettingsBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtSettingsObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[settings bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.settingsBridge as QtSettingsObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtSettingsObject>(
    method: K,
    ...args: QtSettingsObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    setActiveSection: (section) => call("setActiveSection", section),
    setTheme: (theme) => call("setTheme", theme),
    setShowTokenCounter: (enabled) => call("setShowTokenCounter", enabled),
    setEnableSystemPrompt: (enabled) => call("setEnableSystemPrompt", enabled),
    setNotificationPreference: (notificationType, enabled) =>
      call("setNotificationPreference", notificationType, enabled),
    setUpdateNotificationsEnabled: (enabled) => call("setUpdateNotificationsEnabled", enabled),
    checkForUpdates: () => call("checkForUpdates"),
    openRepository: () => call("openRepository"),
    setGithubToken: (token) => call("setGithubToken", token),
    clearGithubToken: () => call("clearGithubToken"),
    setApiProvider: (provider) => call("setApiProvider", provider),
    saveApiConfiguration: (args) => call("saveApiConfiguration", JSON.stringify(args)),
    loadAvailableModels: (apiKey) => call("loadAvailableModels", apiKey),
    resetApiSettings: () => call("resetApiSettings"),
    setOllamaReasoningMode: (mode) => call("setOllamaReasoningMode", mode),
    setOllamaModelAssignment: (task, value) => call("setOllamaModelAssignment", task, value),
    scanOllamaSystem: () => call("scanOllamaSystem"),
    pickOllamaScanFolder: () => call("pickOllamaScanFolder"),
    pullOllamaModel: (modelName) => call("pullOllamaModel", modelName),
    setLlamaCppReasoningMode: (mode) => call("setLlamaCppReasoningMode", mode),
    setLlamaCppChatFormat: (chatFormat) => call("setLlamaCppChatFormat", chatFormat),
    setLlamaCppNCtx: (nCtx) => call("setLlamaCppNCtx", nCtx),
    setLlamaCppNGpuLayers: (nGpuLayers) => call("setLlamaCppNGpuLayers", nGpuLayers),
    setLlamaCppNThreads: (nThreads) => call("setLlamaCppNThreads", nThreads),
    pickLlamaCppChatModelFile: () => call("pickLlamaCppChatModelFile"),
    pickLlamaCppTitleModelFile: () => call("pickLlamaCppTitleModelFile"),
    setLlamaCppChatModelPath: (path) => call("setLlamaCppChatModelPath", path),
    setLlamaCppTitleModelPath: (path) => call("setLlamaCppTitleModelPath", path),
    scanLlamaCppSystem: () => call("scanLlamaCppSystem"),
    pickLlamaCppScanFolder: () => call("pickLlamaCppScanFolder"),
    saveLlamaCppSettings: () => call("saveLlamaCppSettings"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
