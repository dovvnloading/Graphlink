import { describe, expect, it, vi } from "vitest";
import { createSearchOverlayBridge } from "./bridge";
import { initialSearchOverlayState, SearchOverlayState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<SearchOverlayState> = {}): string {
  return JSON.stringify({ ...initialSearchOverlayState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    search: vi.fn(),
    next: vi.fn(),
    previous: vi.fn(),
    close: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { searchOverlayBridge: remote } });
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

describe("createSearchOverlayBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createSearchOverlayBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialSearchOverlayState);
  });

  it("intents on the mock bridge do not throw", () => {
    const bridge = createSearchOverlayBridge(() => {});
    expect(() => {
      bridge.search("x");
      bridge.next();
      bridge.previous();
      bridge.close();
      bridge.dispose();
    }).not.toThrow();
  });
});

describe("createSearchOverlayBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createSearchOverlayBridge(() => {});
      expect(remote.ready).toHaveBeenCalledTimes(1);
      expect(remote.stateChanged.connect).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("forwards match counts pushed through stateChanged to the listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      createSearchOverlayBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(stateJson({ revision: 2, currentIndex: 1, totalMatches: 3 }));

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({ currentIndex: 1, totalMatches: 3 }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("each intent calls through to the matching remote method", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createSearchOverlayBridge(() => {});

      bridge.search("hello");
      bridge.next();
      bridge.previous();
      bridge.close();

      expect(remote.search).toHaveBeenCalledWith("hello");
      expect(remote.next).toHaveBeenCalledTimes(1);
      expect(remote.previous).toHaveBeenCalledTimes(1);
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
      createSearchOverlayBridge(listener, onRejection);
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
      const bridge = createSearchOverlayBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
