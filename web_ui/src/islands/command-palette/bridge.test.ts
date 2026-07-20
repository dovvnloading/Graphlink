import { describe, expect, it, vi } from "vitest";
import { createCommandPaletteBridge } from "./bridge";
import { initialCommandPaletteState, CommandPaletteState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<CommandPaletteState> = {}): string {
  return JSON.stringify({ ...initialCommandPaletteState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    executeCommand: vi.fn(),
    dismiss: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { commandPaletteBridge: remote } });
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

describe("createCommandPaletteBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial state on ready()", () => {
    const listener = vi.fn();
    const bridge = createCommandPaletteBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialCommandPaletteState);
  });

  it("dispose() on the mock bridge does not throw", () => {
    const bridge = createCommandPaletteBridge(() => {});
    expect(() => bridge.dispose()).not.toThrow();
  });
});

describe("createCommandPaletteBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createCommandPaletteBridge(() => {});
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
      createCommandPaletteBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(
        stateJson({
          visible: true,
          commands: [{ id: "0", name: "New Chat", aliases: ["new chat"] }],
        }),
      );

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({
          visible: true,
          commands: [{ id: "0", name: "New Chat", aliases: ["new chat"] }],
        }),
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
      createCommandPaletteBridge(listener, onRejection);
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
      const bridge = createCommandPaletteBridge(() => {});
      bridge.executeCommand("0");
      bridge.dismiss();

      expect(remote.executeCommand).toHaveBeenCalledWith("0");
      expect(remote.dismiss).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("dispose() disconnects the state listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createCommandPaletteBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
