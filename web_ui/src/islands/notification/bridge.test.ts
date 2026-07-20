import { describe, expect, it, vi } from "vitest";
import { createNotificationBridge } from "./bridge";
import { initialNotificationState, NotificationState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<NotificationState> = {}): string {
  return JSON.stringify({ ...initialNotificationState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    copyDetails: vi.fn(),
    dismiss: vi.fn(),
    resize: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { notificationBridge: remote } });
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

describe("createNotificationBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createNotificationBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialNotificationState);
  });

  it("dispose() on the mock bridge does not throw", () => {
    const bridge = createNotificationBridge(() => {});
    expect(() => bridge.dispose()).not.toThrow();
  });
});

describe("createNotificationBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createNotificationBridge(() => {});
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
      createNotificationBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(stateJson({ visible: true, message: "Saved.", msgType: "success" }));

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({ visible: true, message: "Saved.", msgType: "success" }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("rejects a payload with an incompatible schema version instead of forwarding it", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      const onRejection = vi.fn();
      createNotificationBridge(listener, onRejection);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
      listener.mockClear();

      push(stateJson({ minCompatibleSchemaVersion: 999 }));

      expect(listener).not.toHaveBeenCalled();
      expect(onRejection).toHaveBeenCalledWith(expect.objectContaining({ kind: "version" }));
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("gates outbound calls on the connected flag, not just remote's presence", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createNotificationBridge(() => {});
      bridge.copyDetails();
      bridge.dismiss();

      expect(remote.copyDetails).toHaveBeenCalledTimes(1);
      expect(remote.dismiss).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("queues a resize requested before connection and applies it once connected", () => {
    // Deferred callback so resize() can genuinely be called before
    // "connected" flips true - see composer/bridge.test.ts's identical case.
    const remote = {
      stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
      ready: vi.fn(),
      copyDetails: vi.fn(),
      dismiss: vi.fn(),
      resize: vi.fn(),
    };
    let pendingCallback: ((channel: { objects: Record<string, unknown> }) => void) | null = null;

    class DeferredFakeQWebChannel {
      constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
        pendingCallback = callback;
      }
    }

    const qtWindow = window as unknown as QtWindow;
    qtWindow.QWebChannel = DeferredFakeQWebChannel as unknown as QtWindow["QWebChannel"];
    qtWindow.qt = { webChannelTransport: {} };

    try {
      const bridge = createNotificationBridge(() => {});
      bridge.resize(150);
      expect(remote.resize).not.toHaveBeenCalled();

      pendingCallback!({ objects: { notificationBridge: remote } });
      expect(remote.resize).toHaveBeenCalledWith(150);
    } finally {
      delete qtWindow.QWebChannel;
      delete qtWindow.qt;
    }
  });

  it("dispose() disconnects the state listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createNotificationBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
