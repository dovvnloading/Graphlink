import { describe, expect, it, vi } from "vitest";
import { createComposerPickerBridge } from "./bridge";
import { initialComposerPickerState, ComposerPickerState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<ComposerPickerState> = {}): string {
  return JSON.stringify({ ...initialComposerPickerState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    selectOption: vi.fn(),
    requestSettings: vi.fn(),
    resize: vi.fn(),
    close: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { composerPickerBridge: remote } });
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

describe("createComposerPickerBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createComposerPickerBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialComposerPickerState);
  });

  it("intents on the mock bridge do not throw", () => {
    const bridge = createComposerPickerBridge(() => {});
    expect(() => {
      bridge.selectOption("gpt");
      bridge.requestSettings();
      bridge.resize(300);
      bridge.close();
      bridge.dispose();
    }).not.toThrow();
  });
});

describe("createComposerPickerBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createComposerPickerBridge(() => {});
      expect(remote.ready).toHaveBeenCalledTimes(1);
      expect(remote.stateChanged.connect).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("forwards options pushed through stateChanged to the listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      createComposerPickerBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(
        stateJson({
          revision: 2,
          kind: "reasoning",
          title: "Choose response depth",
          options: [{ id: "Quick", label: "Quick", meta: "Direct", current: true, unavailable: false }],
        }),
      );

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "reasoning", options: [expect.objectContaining({ id: "Quick" })] }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("each intent calls through to the matching remote method with its args", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createComposerPickerBridge(() => {});

      bridge.selectOption("gpt-4");
      bridge.requestSettings();
      bridge.resize(321);
      bridge.close();

      expect(remote.selectOption).toHaveBeenCalledWith("gpt-4");
      expect(remote.requestSettings).toHaveBeenCalledTimes(1);
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
      createComposerPickerBridge(listener, onRejection);
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
      const bridge = createComposerPickerBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
