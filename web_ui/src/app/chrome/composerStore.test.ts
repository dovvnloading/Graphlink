import { describe, expect, it, vi } from "vitest";
import { ComposerStore, initialComposerState } from "./composerStore";
import type { WsTransport } from "../../lib/ws/transport";

type StateListener = (payload: Record<string, unknown>) => void;

function makeFakeTransport() {
  const listeners = new Map<string, StateListener>();
  const intents: Array<{ topic: string; intent: string; args: unknown[] }> = [];
  const transport = {
    subscribe: vi.fn((topic: string, listener: StateListener) => {
      listeners.set(topic, listener);
      return () => listeners.delete(topic);
    }),
    intent: vi.fn((topic: string, intent: string, args: unknown[] = []) => {
      intents.push({ topic, intent, args });
    }),
  } as unknown as WsTransport;
  return { transport, listeners, intents };
}

function validComposerPayload(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: 1,
    minCompatibleSchemaVersion: 1,
    revision: 1,
    draft: { id: "d1", text: "hi", contextMode: "branch", sendMode: "enter_to_send", restored: false },
    context: { anchor: null, items: [], totalTokens: 0, reviewAvailable: false },
    route: {
      mode: "ollama",
      provider: "Ollama (Local)",
      modelId: "",
      modelLabel: "",
      modelOptions: [],
      reasoning: { level: "quick", label: "Quick Mode (No CoT)", options: [] },
      label: "Ollama (Local)",
      available: true,
      canChange: false,
    },
    request: { id: null, state: "idle", message: "", canSend: false, canCancel: false, canRetry: false },
    capabilities: {
      attachments: false,
      contextReview: false,
      routeSelection: false,
      modelSelection: false,
      reasoningSelection: true,
      settingsShortcut: true,
      cancellation: false,
    },
    ...overrides,
  };
}

describe("ComposerStore", () => {
  it("accepts a valid composer snapshot", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    const seen = vi.fn();
    store.subscribe(seen);
    listeners.get("app-composer")!(validComposerPayload());
    expect(seen).toHaveBeenCalledTimes(1);
    expect(store.getComposer().draft.text).toBe("hi");
  });

  it("rejects a malformed snapshot and keeps the previous state", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    listeners.get("app-composer")!({ draft: "not-an-object" });
    expect(store.getComposer()).toEqual(initialComposerState);
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("routes token-counter and notification snapshots independently", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("token-counter")!({
      schemaVersion: 1,
      minCompatibleSchemaVersion: 1,
      revision: 1,
      inputTokens: 4,
      outputTokens: 0,
      contextTokens: 0,
      totalTokens: 4,
    });
    listeners.get("notification")!({
      schemaVersion: 1,
      minCompatibleSchemaVersion: 1,
      revision: 1,
      visible: true,
      message: "hi",
      msgType: "info",
    });
    expect(store.getTokenCounter().inputTokens).toBe(4);
    expect(store.getNotification().visible).toBe(true);
  });

  it("sends intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.updateDraft("hello world");
    store.setReasoningLevel("thinking");
    store.cancelChatRequest("req-42");
    store.dismissNotification();
    expect(intents).toEqual([
      { topic: "app-composer", intent: "updateDraft", args: ["hello world"] },
      { topic: "app-composer", intent: "setReasoningLevel", args: ["thinking"] },
      { topic: "app-composer", intent: "cancelChatRequest", args: ["req-42"] },
      { topic: "notification", intent: "dismiss", args: [] },
    ]);
  });

  it("dispose() unsubscribes every topic", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    expect(listeners.size).toBe(3);
    store.dispose();
    expect(listeners.size).toBe(0);
  });
});
