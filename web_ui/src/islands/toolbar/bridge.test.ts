import { describe, expect, it, vi } from "vitest";
import { createToolbarBridge } from "./bridge";
import { initialToolbarState, ToolbarState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<ToolbarState> = {}): string {
  return JSON.stringify({ ...initialToolbarState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    reportAnchorRect: vi.fn(),
    openLibrary: vi.fn(),
    saveChat: vi.fn(),
    togglePins: vi.fn(),
    organizeNodes: vi.fn(),
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    resetZoom: vi.fn(),
    fitAll: vi.fn(),
    toggleControls: vi.fn(),
    togglePlugins: vi.fn(),
    selectMode: vi.fn(),
    openSettings: vi.fn(),
    openAbout: vi.fn(),
    openHelp: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { toolbarBridge: remote } });
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

describe("createToolbarBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createToolbarBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialToolbarState);
  });

  it("every intent on the mock bridge does not throw", () => {
    const bridge = createToolbarBridge(() => {});
    expect(() => {
      bridge.reportAnchorRect("pins", 1, 2, 3, 4);
      bridge.openLibrary();
      bridge.saveChat();
      bridge.togglePins();
      bridge.organizeNodes();
      bridge.zoomIn();
      bridge.zoomOut();
      bridge.resetZoom();
      bridge.fitAll();
      bridge.toggleControls(true);
      bridge.togglePlugins();
      bridge.selectMode("Ollama (Local)");
      bridge.openSettings();
      bridge.openAbout();
      bridge.openHelp();
      bridge.dispose();
    }).not.toThrow();
  });
});

describe("createToolbarBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createToolbarBridge(() => {});
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
      createToolbarBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(stateJson({ revision: 2, pinsChecked: true, currentMode: "API Endpoint" }));

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({ pinsChecked: true, currentMode: "API Endpoint" }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("each intent calls through to the matching remote method with its args", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createToolbarBridge(() => {});

      bridge.reportAnchorRect("plugins", 10, 20, 30, 40);
      bridge.openLibrary();
      bridge.saveChat();
      bridge.togglePins();
      bridge.organizeNodes();
      bridge.zoomIn();
      bridge.zoomOut();
      bridge.resetZoom();
      bridge.fitAll();
      bridge.toggleControls(true);
      bridge.togglePlugins();
      bridge.selectMode("API Endpoint");
      bridge.openSettings();
      bridge.openAbout();
      bridge.openHelp();

      expect(remote.reportAnchorRect).toHaveBeenCalledWith("plugins", 10, 20, 30, 40);
      expect(remote.openLibrary).toHaveBeenCalledTimes(1);
      expect(remote.saveChat).toHaveBeenCalledTimes(1);
      expect(remote.togglePins).toHaveBeenCalledTimes(1);
      expect(remote.organizeNodes).toHaveBeenCalledTimes(1);
      expect(remote.zoomIn).toHaveBeenCalledTimes(1);
      expect(remote.zoomOut).toHaveBeenCalledTimes(1);
      expect(remote.resetZoom).toHaveBeenCalledTimes(1);
      expect(remote.fitAll).toHaveBeenCalledTimes(1);
      expect(remote.toggleControls).toHaveBeenCalledWith(true);
      expect(remote.togglePlugins).toHaveBeenCalledTimes(1);
      expect(remote.selectMode).toHaveBeenCalledWith("API Endpoint");
      expect(remote.openSettings).toHaveBeenCalledTimes(1);
      expect(remote.openAbout).toHaveBeenCalledTimes(1);
      expect(remote.openHelp).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("rejects a payload with an incompatible schema version instead of forwarding it", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      const onRejection = vi.fn();
      createToolbarBridge(listener, onRejection);
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
      const bridge = createToolbarBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
