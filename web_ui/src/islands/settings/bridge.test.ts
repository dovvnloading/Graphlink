import { describe, expect, it, vi } from "vitest";
import { createSettingsBridge } from "./bridge";
import { initialSettingsState, SettingsState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<SettingsState> = {}): string {
  return JSON.stringify({ ...initialSettingsState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    setActiveSection: vi.fn(),
    setTheme: vi.fn(),
    setShowTokenCounter: vi.fn(),
    setEnableSystemPrompt: vi.fn(),
    setNotificationPreference: vi.fn(),
    setUpdateNotificationsEnabled: vi.fn(),
    checkForUpdates: vi.fn(),
    openRepository: vi.fn(),
    setGithubToken: vi.fn(),
    clearGithubToken: vi.fn(),
    setApiProvider: vi.fn(),
    saveApiConfiguration: vi.fn(),
    loadAvailableModels: vi.fn(),
    resetApiSettings: vi.fn(),
    setOllamaReasoningMode: vi.fn(),
    setOllamaModelAssignment: vi.fn(),
    scanOllamaSystem: vi.fn(),
    pickOllamaScanFolder: vi.fn(),
    pullOllamaModel: vi.fn(),
    setLlamaCppReasoningMode: vi.fn(),
    setLlamaCppChatFormat: vi.fn(),
    setLlamaCppNCtx: vi.fn(),
    setLlamaCppNGpuLayers: vi.fn(),
    setLlamaCppNThreads: vi.fn(),
    pickLlamaCppChatModelFile: vi.fn(),
    pickLlamaCppTitleModelFile: vi.fn(),
    scanLlamaCppSystem: vi.fn(),
    pickLlamaCppScanFolder: vi.fn(),
    saveLlamaCppSettings: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { settingsBridge: remote } });
    }
  }

  const qtWindow = window as unknown as QtWindow;
  qtWindow.QWebChannel = FakeQWebChannel as unknown as QtWindow["QWebChannel"];
  qtWindow.qt = { webChannelTransport: {} };

  return remote;
}

function uninstallFakeQWebChannel() {
  const qtWindow = window as unknown as QtWindow;
  delete qtWindow.QWebChannel;
  delete qtWindow.qt;
}

describe("createSettingsBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialSettingsState);
  });

  it("setActiveSection on the mock bridge updates state and republishes", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.setActiveSection("Integrations");

    expect(listener).toHaveBeenCalledWith(
      expect.objectContaining({ activeSection: "Integrations" }),
    );
  });

  it("setTheme on the mock bridge updates state", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.setTheme("muted");

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ theme: "muted" }));
  });

  it("setGithubToken on the mock bridge reports configured without exposing the value", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.setGithubToken("ghp_secret");

    const [state] = listener.mock.calls[0];
    expect(state.githubTokenConfigured).toBe(true);
    expect(JSON.stringify(state)).not.toContain("ghp_secret");
  });

  it("clearGithubToken on the mock bridge reports not configured", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    bridge.setGithubToken("ghp_secret");
    listener.mockClear();

    bridge.clearGithubToken();

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ githubTokenConfigured: false }));
  });

  it("checkForUpdates on the mock bridge simulates a finished, successful check", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.checkForUpdates();

    expect(listener).toHaveBeenCalledWith(
      expect.objectContaining({ updateCheckInProgress: false, updateStatusLevel: "success" }),
    );
  });

  it("openRepository on the mock bridge does not throw", () => {
    const bridge = createSettingsBridge(() => {});
    expect(() => bridge.openRepository()).not.toThrow();
  });

  it("setNotificationPreference on the mock bridge is a partial update", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.setNotificationPreference("warning", false);

    const [state] = listener.mock.calls[0];
    expect(state.notificationPreferences.warning).toBe(false);
    expect(state.notificationPreferences.info).toBe(true);
  });

  it("setApiProvider on the mock bridge switches provider and resets load state", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.setApiProvider("Google Gemini");

    const [state] = listener.mock.calls[0];
    expect(state.apiProvider).toBe("Google Gemini");
    expect(state.notice).toBeNull();
  });

  it("saveApiConfiguration on the mock bridge reports configured without exposing the key", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.saveApiConfiguration({
      provider: "OpenAI-Compatible",
      baseUrl: "https://api.openai.com/v1",
      apiKey: "sk-secret",
      taskModels: { task_chat: "gpt-4o" },
    });

    const [state] = listener.mock.calls[0];
    expect(state.openaiKeyConfigured).toBe(true);
    expect(JSON.stringify(state)).not.toContain("sk-secret");
  });

  it("saveApiConfiguration on the mock bridge rejects a missing key with a notice", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.saveApiConfiguration({
      provider: "OpenAI-Compatible",
      baseUrl: "https://api.openai.com/v1",
      apiKey: "",
      taskModels: {},
    });

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ notice: "Please enter your API Key." }));
  });

  it("resetApiSettings on the mock bridge clears configured flags", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    bridge.saveApiConfiguration({
      provider: "OpenAI-Compatible",
      baseUrl: "https://api.openai.com/v1",
      apiKey: "sk-secret",
      taskModels: {},
    });
    listener.mockClear();

    bridge.resetApiSettings();

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ openaiKeyConfigured: false }));
  });

  it("setOllamaReasoningMode on the mock bridge updates state", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.setOllamaReasoningMode("Quick");

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ ollamaReasoningMode: "Quick" }));
  });

  it("setOllamaModelAssignment on the mock bridge is a partial update", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.setOllamaModelAssignment("task_chart", "llama3:8b");

    const [state] = listener.mock.calls[0];
    expect(state.ollamaModelAssignments.task_chart).toBe("llama3:8b");
  });

  it("pullOllamaModel on the mock bridge rejects an empty model name", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.pullOllamaModel("");

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ notice: "Model name cannot be empty." }));
  });

  it("dispose() on the mock bridge does not throw", () => {
    const bridge = createSettingsBridge(() => {});
    expect(() => bridge.dispose()).not.toThrow();
  });

  it("setLlamaCppReasoningMode on the mock bridge updates state", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.setLlamaCppReasoningMode("Quick");

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ llamaCppReasoningMode: "Quick" }));
  });

  it("setLlamaCppNCtx on the mock bridge updates state", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.setLlamaCppNCtx(8192);

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ llamaCppNCtx: 8192 }));
  });

  it("saveLlamaCppSettings on the mock bridge rejects an empty chat model path with a notice", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.saveLlamaCppSettings();

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ notice: "Chat Model File cannot be empty." }));
  });

  it("scanLlamaCppSystem on the mock bridge reports found models", () => {
    const listener = vi.fn();
    const bridge = createSettingsBridge(listener);
    bridge.ready();
    listener.mockClear();

    bridge.scanLlamaCppSystem();

    const [state] = listener.mock.calls[0];
    expect(state.llamaCppScanStatus).toBe("done");
    expect(state.llamaCppScannedModels.length).toBeGreaterThan(0);
  });
});

describe("createSettingsBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createSettingsBridge(() => {});
      expect(remote.ready).toHaveBeenCalledTimes(1);
      expect(remote.stateChanged.connect).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("forwards state pushed through stateChanged to the listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      createSettingsBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(stateJson({ activeSection: "API Endpoint" }));

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({ activeSection: "API Endpoint" }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("setActiveSection calls through to the remote", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createSettingsBridge(() => {});

      bridge.setActiveSection("Ollama (Local)");

      expect(remote.setActiveSection).toHaveBeenCalledWith("Ollama (Local)");
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("each General/Appearance intent calls through to its own remote method", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createSettingsBridge(() => {});

      bridge.setTheme("mono");
      bridge.setShowTokenCounter(false);
      bridge.setEnableSystemPrompt(false);
      bridge.setNotificationPreference("error", false);
      bridge.setUpdateNotificationsEnabled(true);
      bridge.checkForUpdates();
      bridge.openRepository();

      expect(remote.setTheme).toHaveBeenCalledWith("mono");
      expect(remote.setShowTokenCounter).toHaveBeenCalledWith(false);
      expect(remote.setEnableSystemPrompt).toHaveBeenCalledWith(false);
      expect(remote.setNotificationPreference).toHaveBeenCalledWith("error", false);
      expect(remote.setUpdateNotificationsEnabled).toHaveBeenCalledWith(true);
      expect(remote.checkForUpdates).toHaveBeenCalledTimes(1);
      expect(remote.openRepository).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("setGithubToken and clearGithubToken call through to the remote", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createSettingsBridge(() => {});

      bridge.setGithubToken("ghp_secret");
      bridge.clearGithubToken();

      expect(remote.setGithubToken).toHaveBeenCalledWith("ghp_secret");
      expect(remote.clearGithubToken).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("setApiProvider and loadAvailableModels and resetApiSettings call through to the remote", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createSettingsBridge(() => {});

      bridge.setApiProvider("Anthropic Claude");
      bridge.loadAvailableModels("sk-secret");
      bridge.resetApiSettings();

      expect(remote.setApiProvider).toHaveBeenCalledWith("Anthropic Claude");
      expect(remote.loadAvailableModels).toHaveBeenCalledWith("sk-secret");
      expect(remote.resetApiSettings).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("saveApiConfiguration serializes its args to JSON for the remote call", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createSettingsBridge(() => {});
      const args = {
        provider: "OpenAI-Compatible",
        baseUrl: "https://api.openai.com/v1",
        apiKey: "sk-secret",
        taskModels: { task_chat: "gpt-4o" },
      };

      bridge.saveApiConfiguration(args);

      expect(remote.saveApiConfiguration).toHaveBeenCalledWith(JSON.stringify(args));
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("Ollama intents call through to their own remote methods", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createSettingsBridge(() => {});

      bridge.setOllamaReasoningMode("Quick");
      bridge.setOllamaModelAssignment("task_chart", "llama3:8b");
      bridge.scanOllamaSystem();
      bridge.pickOllamaScanFolder();
      bridge.pullOllamaModel("llama3:8b");

      expect(remote.setOllamaReasoningMode).toHaveBeenCalledWith("Quick");
      expect(remote.setOllamaModelAssignment).toHaveBeenCalledWith("task_chart", "llama3:8b");
      expect(remote.scanOllamaSystem).toHaveBeenCalledTimes(1);
      expect(remote.pickOllamaScanFolder).toHaveBeenCalledTimes(1);
      expect(remote.pullOllamaModel).toHaveBeenCalledWith("llama3:8b");
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("LlamaCpp intents call through to their own remote methods", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createSettingsBridge(() => {});

      bridge.setLlamaCppReasoningMode("Quick");
      bridge.setLlamaCppChatFormat("chatml");
      bridge.setLlamaCppNCtx(8192);
      bridge.setLlamaCppNGpuLayers(-1);
      bridge.setLlamaCppNThreads(8);
      bridge.pickLlamaCppChatModelFile();
      bridge.pickLlamaCppTitleModelFile();
      bridge.scanLlamaCppSystem();
      bridge.pickLlamaCppScanFolder();
      bridge.saveLlamaCppSettings();

      expect(remote.setLlamaCppReasoningMode).toHaveBeenCalledWith("Quick");
      expect(remote.setLlamaCppChatFormat).toHaveBeenCalledWith("chatml");
      expect(remote.setLlamaCppNCtx).toHaveBeenCalledWith(8192);
      expect(remote.setLlamaCppNGpuLayers).toHaveBeenCalledWith(-1);
      expect(remote.setLlamaCppNThreads).toHaveBeenCalledWith(8);
      expect(remote.pickLlamaCppChatModelFile).toHaveBeenCalledTimes(1);
      expect(remote.pickLlamaCppTitleModelFile).toHaveBeenCalledTimes(1);
      expect(remote.scanLlamaCppSystem).toHaveBeenCalledTimes(1);
      expect(remote.pickLlamaCppScanFolder).toHaveBeenCalledTimes(1);
      expect(remote.saveLlamaCppSettings).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("rejects a payload with an incompatible schema version instead of forwarding it", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      const onRejection = vi.fn();
      createSettingsBridge(listener, onRejection);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
      listener.mockClear();

      push(stateJson({ minCompatibleSchemaVersion: 999 }));

      expect(listener).not.toHaveBeenCalled();
      expect(onRejection).toHaveBeenCalledWith(expect.objectContaining({ kind: "version" }));
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("dispose() disconnects the stateChanged listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createSettingsBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
