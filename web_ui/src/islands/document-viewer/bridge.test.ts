import { describe, expect, it, vi } from "vitest";
import { createDocumentViewerBridge } from "./bridge";
import { initialDocumentViewerState, DocumentViewerState } from "./bridgeTypes";
import { QtWindow } from "../../lib/bridge-core/transport";

function stateJson(overrides: Partial<DocumentViewerState> = {}): string {
  return JSON.stringify({ ...initialDocumentViewerState, ...overrides });
}

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    close: vi.fn(),
  };

  class FakeQWebChannel {
    constructor(_transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
      callback({ objects: { documentViewerBridge: remote } });
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

describe("createDocumentViewerBridge with no QWebChannel available", () => {
  it("falls back to the mock bridge and publishes the initial (empty-content) state on ready()", () => {
    const listener = vi.fn();
    const bridge = createDocumentViewerBridge(listener);

    bridge.ready();

    expect(listener).toHaveBeenCalledExactlyOnceWith(initialDocumentViewerState);
  });

  it("close() on the mock bridge does not throw", () => {
    const bridge = createDocumentViewerBridge(() => {});
    expect(() => bridge.close()).not.toThrow();
  });

  it("dispose() on the mock bridge does not throw", () => {
    const bridge = createDocumentViewerBridge(() => {});
    expect(() => bridge.dispose()).not.toThrow();
  });
});

describe("createDocumentViewerBridge against a real QWebChannel connection", () => {
  it("connects synchronously and calls remote.ready()", () => {
    const remote = installFakeQWebChannel();
    try {
      createDocumentViewerBridge(() => {});
      expect(remote.ready).toHaveBeenCalledTimes(1);
      expect(remote.stateChanged.connect).toHaveBeenCalledTimes(1);
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("forwards content pushed through stateChanged to the listener", () => {
    const remote = installFakeQWebChannel();
    try {
      const listener = vi.fn();
      createDocumentViewerBridge(listener);
      const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

      push(stateJson({ revision: 3, content: "## Code\n\n```python\nprint(1)\n```" }));

      expect(listener).toHaveBeenCalledWith(
        expect.objectContaining({ revision: 3, content: "## Code\n\n```python\nprint(1)\n```" }),
      );
    } finally {
      uninstallFakeQWebChannel();
    }
  });

  it("close calls through to the remote method", () => {
    const remote = installFakeQWebChannel();
    try {
      const bridge = createDocumentViewerBridge(() => {});

      bridge.close();

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
      createDocumentViewerBridge(listener, onRejection);
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
      const bridge = createDocumentViewerBridge(() => {});
      const handler = remote.stateChanged.connect.mock.calls[0][0];

      bridge.dispose();

      expect(remote.stateChanged.disconnect).toHaveBeenCalledWith(handler);
    } finally {
      uninstallFakeQWebChannel();
    }
  });
});
