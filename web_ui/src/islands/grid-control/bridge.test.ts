import { describe, expect, it, vi } from "vitest";
import { createGridControlBridge } from "./bridge";
import { initialGridControlState, GridControlState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<GridControlState> = {}): string {
  return JSON.stringify({ ...initialGridControlState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    setGridSize: vi.fn(),
    setGridOpacityPercent: vi.fn(),
    setGridStyle: vi.fn(),
    setGridColor: vi.fn(),
    setSnapToGrid: vi.fn(),
    setOrthogonalConnections: vi.fn(),
    setSmartGuides: vi.fn(),
    setFadeConnections: vi.fn(),
    resize: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { gridControlBridge: remote } });
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

describe("createGridControlBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createGridControlBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialGridControlState);
  });

  it("intents on the mock bridge do not throw", () => {
    const bridge = createGridControlBridge(() => {});
    expect(() => {
      bridge.setGridSize(50);
      bridge.setGridOpacityPercent(80);
      bridge.setGridStyle("Lines");
      bridge.setGridColor("#ABCDEF");
      bridge.setSnapToGrid(true);
      bridge.setOrthogonalConnections(true);
      bridge.setSmartGuides(true);
      bridge.setFadeConnections(true);
      bridge.resize(300);
      bridge.dispose();
    }).not.toThrow();
  });
});

describe("createGridControlBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createGridControlBridge(() => {});
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
      createGridControlBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(stateJson({ revision: 2, gridSize: 50, gridStyle: "Cross" }));

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({ gridSize: 50, gridStyle: "Cross" }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("each intent calls through to the matching remote method with its args", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createGridControlBridge(() => {});

      bridge.setGridSize(20);
      bridge.setGridOpacityPercent(60);
      bridge.setGridStyle("Lines");
      bridge.setGridColor("#123456");
      bridge.setSnapToGrid(true);
      bridge.setOrthogonalConnections(false);
      bridge.setSmartGuides(true);
      bridge.setFadeConnections(false);
      bridge.resize(321);

      expect(remote.setGridSize).toHaveBeenCalledWith(20);
      expect(remote.setGridOpacityPercent).toHaveBeenCalledWith(60);
      expect(remote.setGridStyle).toHaveBeenCalledWith("Lines");
      expect(remote.setGridColor).toHaveBeenCalledWith("#123456");
      expect(remote.setSnapToGrid).toHaveBeenCalledWith(true);
      expect(remote.setOrthogonalConnections).toHaveBeenCalledWith(false);
      expect(remote.setSmartGuides).toHaveBeenCalledWith(true);
      expect(remote.setFadeConnections).toHaveBeenCalledWith(false);
      expect(remote.resize).toHaveBeenCalledWith(321);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("rejects a payload with an incompatible schema version instead of forwarding it", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      const onRejection = vi.fn();
      createGridControlBridge(listener, onRejection);
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
      const bridge = createGridControlBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
