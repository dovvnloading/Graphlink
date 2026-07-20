import { describe, expect, it, vi } from "vitest";
import { createAboutBridge } from "./bridge";
import { initialAboutState, AboutState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<AboutState> = {}): string {
  return JSON.stringify({ ...initialAboutState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    close: vi.fn(),
    openExternal: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { aboutBridge: remote } });
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

describe("createAboutBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createAboutBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialAboutState);
  });

  it("close() on the mock bridge does not throw", () => {
    const bridge = createAboutBridge(() => {});
    expect(() => bridge.close()).not.toThrow();
  });

  it("openExternal() on the mock bridge does not throw", () => {
    const bridge = createAboutBridge(() => {});
    expect(() => bridge.openExternal("https://example.com")).not.toThrow();
  });

  it("dispose() on the mock bridge does not throw", () => {
    const bridge = createAboutBridge(() => {});
    expect(() => bridge.dispose()).not.toThrow();
  });
});

describe("createAboutBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createAboutBridge(() => {});
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
      createAboutBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(stateJson({ appVersion: "9.9.9" }));

      expect(listener).toHaveBeenCalledWith(expect.objectContaining({ appVersion: "9.9.9" }));
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("close and openExternal call through to their own remote methods", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createAboutBridge(() => {});

      bridge.close();
      bridge.openExternal("https://example.com");

      expect(remote.close).toHaveBeenCalledTimes(1);
      expect(remote.openExternal).toHaveBeenCalledWith("https://example.com");
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("rejects a payload with an incompatible schema version instead of forwarding it", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      const onRejection = vi.fn();
      createAboutBridge(listener, onRejection);
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
      const bridge = createAboutBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
