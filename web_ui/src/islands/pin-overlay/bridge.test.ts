import { describe, expect, it, vi } from "vitest";
import { createPinOverlayBridge } from "./bridge";
import { initialPinOverlayState, PinOverlayState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<PinOverlayState> = {}): string {
  return JSON.stringify({ ...initialPinOverlayState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    selectPin: vi.fn(),
    deletePin: vi.fn(),
    createPin: vi.fn(),
    editPin: vi.fn(),
    resize: vi.fn(),
    close: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { pinOverlayBridge: remote } });
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

describe("createPinOverlayBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createPinOverlayBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialPinOverlayState);
  });

  it("intents on the mock bridge do not throw", () => {
    const bridge = createPinOverlayBridge(() => {});
    expect(() => {
      bridge.selectPin("p1");
      bridge.deletePin("p1");
      bridge.createPin();
      bridge.editPin("p1");
      bridge.resize(300);
      bridge.close();
      bridge.dispose();
    }).not.toThrow();
  });
});

describe("createPinOverlayBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createPinOverlayBridge(() => {});
      expect(remote.ready).toHaveBeenCalledTimes(1);
      expect(remote.stateChanged.connect).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("forwards rows pushed through stateChanged to the listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      createPinOverlayBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(stateJson({ revision: 2, rows: [{ id: "p1", title: "A", note: "" }] }));

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({ rows: [{ id: "p1", title: "A", note: "" }] }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("each intent calls through to the matching remote method with its args", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createPinOverlayBridge(() => {});

      bridge.selectPin("p1");
      bridge.deletePin("p2");
      bridge.createPin();
      bridge.editPin("p3");
      bridge.resize(321);
      bridge.close();

      expect(remote.selectPin).toHaveBeenCalledWith("p1");
      expect(remote.deletePin).toHaveBeenCalledWith("p2");
      expect(remote.createPin).toHaveBeenCalledTimes(1);
      expect(remote.editPin).toHaveBeenCalledWith("p3");
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
      createPinOverlayBridge(listener, onRejection);
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
      const bridge = createPinOverlayBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
