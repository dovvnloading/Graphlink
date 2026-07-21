import { describe, expect, it, vi } from "vitest";
import { createMinimapBridge } from "./bridge";
import { initialMinimapState, MinimapState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<MinimapState> = {}): string {
  return JSON.stringify({ ...initialMinimapState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    selectNode: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { minimapBridge: remote } });
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

describe("createMinimapBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createMinimapBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialMinimapState);
  });

  it("intents on the mock bridge do not throw", () => {
    const bridge = createMinimapBridge(() => {});
    expect(() => {
      bridge.selectNode("123");
      bridge.dispose();
    }).not.toThrow();
  });
});

describe("createMinimapBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createMinimapBridge(() => {});
      expect(remote.ready).toHaveBeenCalledTimes(1);
      expect(remote.stateChanged.connect).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("forwards real published nodes to the listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      createMinimapBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(
        stateJson({
          revision: 2,
          nodes: [{ id: "111", isUser: true, preview: "Hello" }],
        }),
      );

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({ nodes: [expect.objectContaining({ id: "111" })] }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("selectNode calls through to the matching remote method with the id", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createMinimapBridge(() => {});

      bridge.selectNode("456");

      expect(remote.selectNode).toHaveBeenCalledWith("456");
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("rejects a payload with an incompatible schema version instead of forwarding it", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      const onRejection = vi.fn();
      createMinimapBridge(listener, onRejection);
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
      const bridge = createMinimapBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
