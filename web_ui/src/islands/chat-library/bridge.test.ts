import { describe, expect, it, vi } from "vitest";
import { createChatLibraryBridge } from "./bridge";
import { initialChatLibraryState, ChatLibraryState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<ChatLibraryState> = {}): string {
  return JSON.stringify({ ...initialChatLibraryState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    refresh: vi.fn(),
    loadChat: vi.fn(),
    deleteChat: vi.fn(),
    renameChat: vi.fn(),
    newChat: vi.fn(),
    close: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { chatLibraryBridge: remote } });
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

describe("createChatLibraryBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createChatLibraryBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialChatLibraryState);
  });

  it("mutating intents on the mock bridge do not throw", () => {
    const bridge = createChatLibraryBridge(() => {});
    expect(() => {
      bridge.loadChat(1);
      bridge.deleteChat(1);
      bridge.renameChat(1, "x");
      bridge.newChat();
      bridge.close();
      bridge.refresh();
      bridge.dispose();
    }).not.toThrow();
  });
});

describe("createChatLibraryBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createChatLibraryBridge(() => {});
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
      createChatLibraryBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(
        stateJson({
          revision: 3,
          rows: [{ id: 7, title: "A chat", createdLabel: "c", updatedLabel: "u" }],
        }),
      );

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({
          revision: 3,
          rows: [{ id: 7, title: "A chat", createdLabel: "c", updatedLabel: "u" }],
        }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("each intent calls through to the matching remote method with its args", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createChatLibraryBridge(() => {});

      bridge.loadChat(5);
      bridge.deleteChat(6);
      bridge.renameChat(7, "New Title");
      bridge.newChat();
      bridge.close();

      expect(remote.loadChat).toHaveBeenCalledWith(5);
      expect(remote.deleteChat).toHaveBeenCalledWith(6);
      expect(remote.renameChat).toHaveBeenCalledWith(7, "New Title");
      expect(remote.newChat).toHaveBeenCalledTimes(1);
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
      createChatLibraryBridge(listener, onRejection);
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
      const bridge = createChatLibraryBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
