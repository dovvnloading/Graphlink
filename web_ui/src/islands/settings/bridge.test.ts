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

  it("dispose() on the mock bridge does not throw", () => {
    const bridge = createSettingsBridge(() => {});
    expect(() => bridge.dispose()).not.toThrow();
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
