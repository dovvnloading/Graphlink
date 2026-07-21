import { describe, expect, it, vi } from "vitest";
import { createPluginPickerBridge } from "./bridge";
import { initialPluginPickerState, PluginPickerState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<PluginPickerState> = {}): string {
  return JSON.stringify({ ...initialPluginPickerState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    executePlugin: vi.fn(),
    resize: vi.fn(),
    close: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { pluginPickerBridge: remote } });
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

describe("createPluginPickerBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createPluginPickerBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialPluginPickerState);
  });

  it("intents on the mock bridge do not throw", () => {
    const bridge = createPluginPickerBridge(() => {});
    expect(() => {
      bridge.executePlugin("Py-Coder");
      bridge.resize(300);
      bridge.close();
      bridge.dispose();
    }).not.toThrow();
  });
});

describe("createPluginPickerBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createPluginPickerBridge(() => {});
      expect(remote.ready).toHaveBeenCalledTimes(1);
      expect(remote.stateChanged.connect).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("forwards categories pushed through stateChanged to the listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      createPluginPickerBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(
        stateJson({
          revision: 2,
          categories: [
            {
              name: "Build & Execution",
              description: "Code generation and execution tools.",
              plugins: [{ name: "Py-Coder", description: "Run Python." }],
            },
          ],
        }),
      );

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({
          categories: [expect.objectContaining({ name: "Build & Execution" })],
        }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("each intent calls through to the matching remote method with its args", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createPluginPickerBridge(() => {});

      bridge.executePlugin("Gitlink");
      bridge.resize(321);
      bridge.close();

      expect(remote.executePlugin).toHaveBeenCalledWith("Gitlink");
      expect(remote.resize).toHaveBeenCalledWith(321);
      expect(remote.close).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("rejects a payload with an incompatible schema version instead of forwarding it", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      const onRejection = vi.fn();
      createPluginPickerBridge(listener, onRejection);
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
      const bridge = createPluginPickerBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
