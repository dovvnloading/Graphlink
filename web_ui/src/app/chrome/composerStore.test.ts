import { describe, expect, it, vi } from "vitest";
import { ComposerStore, initialComposerState } from "./composerStore";
import type { WsTransport } from "../../lib/ws/transport";

type StateListener = (payload: Record<string, unknown>) => void;
type StreamListener = (delta: string, done: boolean, reset: boolean, seq: number) => void;

function makeFakeTransport() {
  const listeners = new Map<string, StateListener>();
  const intents: Array<{ topic: string; intent: string; args: unknown[] }> = [];
  const streamListeners = new Map<string, StreamListener>();
  const streamUnsubFns = new Map<string, ReturnType<typeof vi.fn>>();
  const subscribeStream = vi.fn((requestId: string, listener: StreamListener) => {
    streamListeners.set(requestId, listener);
    const unsub = vi.fn(() => {
      streamListeners.delete(requestId);
    });
    streamUnsubFns.set(requestId, unsub);
    return unsub;
  });
  const transport = {
    subscribe: vi.fn((topic: string, listener: StateListener) => {
      listeners.set(topic, listener);
      return () => listeners.delete(topic);
    }),
    intent: vi.fn((topic: string, intent: string, args: unknown[] = []) => {
      intents.push({ topic, intent, args });
    }),
    subscribeStream,
  } as unknown as WsTransport;
  return { transport, listeners, intents, subscribeStream, streamListeners, streamUnsubFns };
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

describe("ComposerStore stream subscription lifecycle (R4.4)", () => {
  it("subscribes to the stream exactly when request.id transitions from null to a real id", () => {
    const { transport, listeners, subscribeStream } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload());
    expect(subscribeStream).not.toHaveBeenCalled();

    listeners.get("app-composer")!(
      validComposerPayload({ request: { id: "req-1", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false } }),
    );
    expect(subscribeStream).toHaveBeenCalledTimes(1);
    expect(subscribeStream).toHaveBeenCalledWith("req-1", expect.any(Function));

    // Same id republished (e.g. an unrelated field changed) - no re-subscribe.
    listeners.get("app-composer")!(
      validComposerPayload({ request: { id: "req-1", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false } }),
    );
    expect(subscribeStream).toHaveBeenCalledTimes(1);
  });

  it("getStreamText() accumulates deltas in order", () => {
    const { transport, listeners, streamListeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(
      validComposerPayload({ request: { id: "req-1", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false } }),
    );
    const streamListener = streamListeners.get("req-1")!;
    streamListener("Hel", false, false, 0);
    streamListener("lo", false, false, 1);
    expect(store.getStreamText()).toBe("Hello");
  });

  it("a reset:true frame clears the buffer before further deltas append", () => {
    const { transport, listeners, streamListeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(
      validComposerPayload({ request: { id: "req-1", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false } }),
    );
    const streamListener = streamListeners.get("req-1")!;
    streamListener("abc", false, false, 0);
    expect(store.getStreamText()).toBe("abc");
    streamListener("", false, true, 1);
    expect(store.getStreamText()).toBe("");
    streamListener("xyz", false, false, 2);
    expect(store.getStreamText()).toBe("xyz");
  });

  it("unsubscribes and clears streamText when request.id flips back to null", () => {
    const { transport, listeners, streamListeners, streamUnsubFns } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(
      validComposerPayload({ request: { id: "req-1", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false } }),
    );
    streamListeners.get("req-1")!("Hello", false, false, 0);
    expect(store.getStreamText()).toBe("Hello");

    listeners.get("app-composer")!(validComposerPayload());
    expect(streamUnsubFns.get("req-1")).toHaveBeenCalledTimes(1);
    expect(store.getStreamText()).toBe("");
  });

  it("a fresh request.id (e.g. immediately re-sending) resubscribes and starts streamText from empty", () => {
    const { transport, listeners, streamListeners, subscribeStream } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(
      validComposerPayload({ request: { id: "req-1", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false } }),
    );
    streamListeners.get("req-1")!("first reply", false, false, 0);

    listeners.get("app-composer")!(
      validComposerPayload({ request: { id: "req-2", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false } }),
    );
    expect(subscribeStream).toHaveBeenCalledTimes(2);
    expect(store.getStreamText()).toBe("");
    streamListeners.get("req-2")!("second reply", false, false, 0);
    expect(store.getStreamText()).toBe("second reply");
  });

  it("dispose() unsubscribes the active stream without double-invoking", () => {
    const { transport, listeners, streamUnsubFns } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(
      validComposerPayload({ request: { id: "req-1", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false } }),
    );
    store.dispose();
    expect(streamUnsubFns.get("req-1")).toHaveBeenCalledTimes(1);
    // dispose() is not re-entrant/double-firing on the same unsub reference.
    store.dispose();
    expect(streamUnsubFns.get("req-1")).toHaveBeenCalledTimes(1);
  });

  it("dispose() with no active stream does not throw", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload());
    expect(() => store.dispose()).not.toThrow();
  });
});
