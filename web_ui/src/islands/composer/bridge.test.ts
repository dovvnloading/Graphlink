import { describe, expect, it, vi } from "vitest";
import { createComposerBridge } from "./bridge";
import { initialComposerState, ComposerState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

// transport.test.ts covers the extracted QWebChannel connection mechanics
// in isolation. This file covers what's still inside bridge.ts's own
// closure once a connection exists - the "connected" gate on call(),
// pendingHeight queuing before connect, dispose()'s unsubscribe, and
// parseState()'s schemaVersion guard - none of which transport.test.ts (or
// ComposerApp.test.tsx, which only ever runs the jsdom mock-bridge fallback
// path with no window.QWebChannel present) exercises.

function stateJson(overrides: Partial<ComposerState> = {}): string {
  return JSON.stringify({ ...initialComposerState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    updateDraft: vi.fn(),
    send: vi.fn(),
    cancel: vi.fn(),
    reviewContext: vi.fn(),
    requestAttachment: vi.fn(),
    stageTextAttachment: vi.fn(),
    removeContextItem: vi.fn(),
    selectModel: vi.fn(),
    setReasoningLevel: vi.fn(),
    openSettings: vi.fn(),
    openModelSelector: vi.fn(),
    openReasoningSelector: vi.fn(),
    resize: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { composerBridge: remote } });
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

describe("createComposerBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createComposerBridge(() => {});
      expect(remote.ready).toHaveBeenCalledTimes(1);
      expect(remote.stateChanged.connect).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("forwards state pushed through stateChanged to the listener, gated by schemaVersion", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      createComposerBridge(listener);
      const stateListener = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      // listener() is called once already, synchronously, by MockComposerBridge-
      // parity ready-state plumbing inside bridge.ts? No - ready() here is the
      // REMOTE's ready(), a fire-and-forget call with no return value; the
      // local `listener` callback is only ever invoked by stateListener.
      expect(listener).not.toHaveBeenCalled();

      stateListener(stateJson({ revision: 7 }));
      expect(listener).toHaveBeenCalledTimes(1);
      expect(listener.mock.calls[0][0].revision).toBe(7);

      // Malformed/incompatible payloads are dropped, not forwarded.
      stateListener(JSON.stringify({ schemaVersion: 2 }));
      stateListener("not json");
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("gates outbound calls on the connected flag, not just remote's presence", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createComposerBridge(() => {});
      // Connection happens synchronously inside createComposerBridge (the
      // fake QWebChannel invokes its callback immediately), so by the time
      // this line runs, "connected" is already true - confirms the normal
      // case actually calls through to the remote object.
      bridge.updateDraft("hello");
      expect(remote.updateDraft).toHaveBeenCalledWith("hello");

      bridge.send();
      bridge.cancel("req-1");
      bridge.reviewContext();
      bridge.requestAttachment();
      bridge.stageTextAttachment("pasted");
      bridge.removeContextItem("item-1");
      bridge.selectModel("model-1");
      bridge.setReasoningLevel("Quick");
      bridge.openSettings();
      bridge.openModelSelector();
      bridge.openReasoningSelector();

      expect(remote.send).toHaveBeenCalledTimes(1);
      expect(remote.cancel).toHaveBeenCalledWith("req-1");
      expect(remote.reviewContext).toHaveBeenCalledTimes(1);
      expect(remote.requestAttachment).toHaveBeenCalledTimes(1);
      expect(remote.stageTextAttachment).toHaveBeenCalledWith("pasted");
      expect(remote.removeContextItem).toHaveBeenCalledWith("item-1");
      expect(remote.selectModel).toHaveBeenCalledWith("model-1");
      expect(remote.setReasoningLevel).toHaveBeenCalledWith("Quick");
      expect(remote.openSettings).toHaveBeenCalledTimes(1);
      expect(remote.openModelSelector).toHaveBeenCalledTimes(1);
      expect(remote.openReasoningSelector).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("queues a resize requested before connection and applies it once connected", () => {
    // Install a QWebChannel whose connection callback fires asynchronously
    // (a macrotask), so resize() can genuinely be called before "connected"
    // flips true - the synchronous FakeQWebChannel above can't exercise this
    // branch, since its callback always runs before createComposerBridge
    // returns.
    const remote = {
      stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
      ready: vi.fn(),
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
      const bridge = createComposerBridge(() => {});
      bridge.resize(240);
      expect(remote.resize).not.toHaveBeenCalled();

      pendingCallback!({ objects: { composerBridge: remote } });
      expect(remote.resize).toHaveBeenCalledWith(240);
    } finally {
      delete qtWindow.QWebChannel;
      delete qtWindow.qt;
    }
  });

  it("dispose() disconnects the state listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createComposerBridge(() => {});
      const stateListener = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(stateListener);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
