import { describe, expect, it, vi } from "vitest";
import { createFontControlBridge } from "./bridge";
import { initialFontControlState, FontControlState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<FontControlState> = {}): string {
  return JSON.stringify({ ...initialFontControlState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    setFontFamily: vi.fn(),
    setFontSize: vi.fn(),
    setFontColor: vi.fn(),
    resize: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { fontControlBridge: remote } });
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

describe("createFontControlBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createFontControlBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialFontControlState);
  });

  it("intents on the mock bridge do not throw", () => {
    const bridge = createFontControlBridge(() => {});
    expect(() => {
      bridge.setFontFamily("Consolas");
      bridge.setFontSize(12);
      bridge.setFontColor("#ABCDEF");
      bridge.resize(180);
      bridge.dispose();
    }).not.toThrow();
  });
});

describe("createFontControlBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createFontControlBridge(() => {});
      expect(remote.ready).toHaveBeenCalledTimes(1);
      expect(remote.stateChanged.connect).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("forwards a real published state to the listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      createFontControlBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(stateJson({ revision: 2, fontFamilies: ["Consolas", "Arial"] }));

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({ fontFamilies: ["Consolas", "Arial"] }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("each intent calls through to the matching remote method with its args", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createFontControlBridge(() => {});

      bridge.setFontFamily("Georgia");
      bridge.setFontSize(14);
      bridge.setFontColor("#F0F0F0");
      bridge.resize(200);

      expect(remote.setFontFamily).toHaveBeenCalledWith("Georgia");
      expect(remote.setFontSize).toHaveBeenCalledWith(14);
      expect(remote.setFontColor).toHaveBeenCalledWith("#F0F0F0");
      expect(remote.resize).toHaveBeenCalledWith(200);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("rejects a payload with an incompatible schema version instead of forwarding it", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      const onRejection = vi.fn();
      createFontControlBridge(listener, onRejection);
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
      const bridge = createFontControlBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
