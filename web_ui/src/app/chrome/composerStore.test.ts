import { describe, expect, it, vi } from "vitest";
import { ComposerStore, initialComposerState } from "./composerStore";
import { subscribeAnnouncer, getAnnouncement } from "../announcer";
import type { WsTransport } from "../../lib/ws/transport";

type StateListener = (payload: Record<string, unknown>) => void;
type StreamListener = (delta: string, done: boolean, reset: boolean, seq: number) => void;

function makeFakeTransport() {
  const listeners = new Map<string, StateListener>();
  const intents: Array<{ topic: string; intent: string; args: unknown[] }> = [];
  const intentCallbacks: Array<{
    onSettled?: () => void;
    onOfflineCoalesced?: () => void;
  }> = [];
  const resubscribeListeners: StateListener[] = [];
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
    // ADR-003 stage 3.1: ComposerStore's own mutating intent call sites now
    // go through fireIntent, not the bare intent() above - recorded into the
    // SAME `intents` array (real WsTransport.fireIntent's own error-recovery
    // path is exercised by transport.test.ts, not re-tested at every call
    // site) so this file's existing assertions don't need to distinguish
    // the two.
    fireIntent: vi.fn((
      topic: string,
      intent: string,
      args: unknown[] = [],
      _timeoutMs?: number,
      _queueable?: boolean,
      _offlineCoalesceKey?: string,
      onSettled?: () => void,
      onOfflineCoalesced?: () => void,
    ) => {
      intents.push({ topic, intent, args });
      intentCallbacks.push({ onSettled, onOfflineCoalesced });
    }),
    resubscribe: vi.fn((_topic: string, listener?: StateListener) => {
      if (listener) resubscribeListeners.push(listener);
      return true;
    }),
    subscribeStream,
  } as unknown as WsTransport;
  return {
    transport,
    listeners,
    intents,
    intentCallbacks,
    resubscribeListeners,
    subscribeStream,
    streamListeners,
    streamUnsubFns,
  };
}

function validComposerPayload(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: 1,
    minCompatibleSchemaVersion: 1,
    revision: 1,
    draft: { id: "d1", text: "hi", contextMode: "branch", sendMode: "enter_to_send" },
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

  it("keeps controlled typing optimistic and ignores stale draft echoes until the newest value is acknowledged", () => {
    const { transport, listeners, intents, intentCallbacks, resubscribeListeners } =
      makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload());
    const seen = vi.fn();
    store.subscribe(seen);

    store.updateDraft("hia");
    expect(store.getComposer().draft.text).toBe("hia");
    expect(seen).toHaveBeenCalledTimes(1);

    // Model a controlled input computing its next value from the immediately
    // rendered store snapshot, before either WebSocket echo has arrived.
    store.updateDraft(`${store.getComposer().draft.text}b`);
    expect(store.getComposer().draft.text).toBe("hiab");
    expect(intents.slice(-2)).toEqual([
      { topic: "app-composer", intent: "updateDraft", args: ["hia"] },
      { topic: "app-composer", intent: "updateDraft", args: ["hiab"] },
    ]);

    // The first intent's stale echo may still carry useful server-owned
    // fields; merge those without rolling the newer local text backward.
    listeners.get("app-composer")!(
      validComposerPayload({
        revision: 2,
        draft: { id: "d1", text: "hia", contextMode: "branch", sendMode: "enter_to_send" },
        request: { id: null, state: "idle", message: "updated", canSend: true, canCancel: false, canRetry: false },
      }),
    );
    expect(store.getComposer().draft.text).toBe("hiab");
    expect(store.getComposer().request.message).toBe("updated");
    expect(store.getComposer().revision).toBe(2);

    // Passive value equality is ambiguous, so authority returns only after
    // both requests settle and their explicit follow-up snapshot arrives.
    intentCallbacks[0].onSettled?.();
    expect(resubscribeListeners).toHaveLength(0);
    intentCallbacks[1].onSettled?.();
    expect(resubscribeListeners).toHaveLength(1);
    const authoritative = validComposerPayload({
      revision: 3,
      draft: { id: "d1", text: "hiab", contextMode: "branch", sendMode: "enter_to_send" },
    });
    resubscribeListeners.shift()!(authoritative);
    listeners.get("app-composer")!(authoritative);

    // Later server snapshots are authoritative again (for example, a
    // successful send clearing the draft).
    listeners.get("app-composer")!(
      validComposerPayload({
        revision: 4,
        draft: { id: "d1", text: "", contextMode: "branch", sendMode: "enter_to_send" },
      }),
    );
    expect(store.getComposer().draft.text).toBe("");
  });

  it("does not mistake an older equal-value echo for acknowledgement in an A-B-A edit", () => {
    const { transport, listeners, intentCallbacks, resubscribeListeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload());

    store.updateDraft("hia");
    store.updateDraft("hiab");
    store.updateDraft("hia");

    for (const [revision, text] of [[2, "hia"], [3, "hiab"], [4, "hia"]] as const) {
      listeners.get("app-composer")!(
        validComposerPayload({
          revision,
          draft: { id: "d1", text, contextMode: "branch", sendMode: "enter_to_send" },
        }),
      );
      expect(store.getComposer().draft.text).toBe("hia");
    }

    for (const callbacks of intentCallbacks.slice(-3)) callbacks.onSettled?.();
    expect(resubscribeListeners).toHaveLength(1);
    const authoritative = validComposerPayload({
      revision: 5,
      draft: { id: "d1", text: "hia", contextMode: "branch", sendMode: "enter_to_send" },
    });
    resubscribeListeners.shift()!(authoritative);
    listeners.get("app-composer")!(authoritative);
    listeners.get("app-composer")!(
      validComposerPayload({
        revision: 6,
        draft: { id: "d1", text: "server value", contextMode: "branch", sendMode: "enter_to_send" },
      }),
    );
    expect(store.getComposer().draft.text).toBe("server value");
  });

  it("retains optimistic text without looping when a correlated snapshot is malformed", () => {
    const { transport, listeners, intentCallbacks, resubscribeListeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload());
    store.updateDraft("local");
    intentCallbacks.at(-1)!.onSettled?.();
    expect(resubscribeListeners).toHaveLength(1);

    resubscribeListeners.shift()!({ schemaVersion: 1 });

    expect(resubscribeListeners).toHaveLength(0);
    expect(store.getComposer().draft.text).toBe("local");
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
      // ADR-006 stage 6.8: real-usage keys (required by the validator).
      promptTokens: null,
      completionTokens: null,
      usageIsReal: false,
      estimatedCostUsd: null,
      // ADR-016 stage 16.2: session-cumulative keys (required by the validator).
      sessionPromptTokens: 0,
      sessionCompletionTokens: 0,
      sessionEstimatedCostUsd: 0,
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

describe("ComposerStore aria-live announcements (ADR-012 stage 12.3)", () => {
  function requestState(state: string, id: string | null = "req-1") {
    return { id, state, message: "", canSend: false, canCancel: false, canRetry: false };
  }

  // announcer.ts appends an invisible zero-width marker on every OTHER call
  // (see its own doc) so a screen reader re-announces even identical
  // repeated text - stripped here since these tests assert the human-
  // readable message, not the marker's presence, and the module-level call
  // count (hence which calls get the marker) depends on total announce()
  // calls across this whole file, not just the current test.
  function plain(text: string): string {
    return text.replace(/[^\x20-\x7E]/g, "");
  }

  it("announces when a request starts generating", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("waiting") }));
    const heard: string[] = [];
    const unsub = subscribeAnnouncer(() => heard.push(getAnnouncement()));
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("generating") }));
    expect(heard.map(plain)).toEqual(["Assistant is responding"]);
    unsub();
  });

  it("announces completion when generating succeeds", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("generating") }));
    const heard: string[] = [];
    const unsub = subscribeAnnouncer(() => heard.push(getAnnouncement()));
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("succeeded") }));
    expect(heard.map(plain)).toEqual(["Response complete"]);
    unsub();
  });

  it("announces failure/cancellation distinctly", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("generating") }));
    const heard: string[] = [];
    const unsub = subscribeAnnouncer(() => heard.push(getAnnouncement()));
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("failed") }));
    expect(heard.map(plain)).toEqual(["Response failed"]);
    unsub();
  });

  it("does not announce a regenerate-in-place (id changes, state stays generating)", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("generating", "req-1") }));
    const heard: string[] = [];
    const unsub = subscribeAnnouncer(() => heard.push(getAnnouncement()));
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("generating", "req-2") }));
    expect(heard.map(plain)).toEqual([]);
    unsub();
  });

  it("does not announce housekeeping transitions before generating starts", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("idle") }));
    const heard: string[] = [];
    const unsub = subscribeAnnouncer(() => heard.push(getAnnouncement()));
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("preparing") }));
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("uploading") }));
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("waiting") }));
    expect(heard.map(plain)).toEqual([]);
    unsub();
  });

  it("does not announce when the terminal state is reached via finalizing", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new ComposerStore(transport);
    store.connect();
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("generating") }));
    const heard: string[] = [];
    const unsub = subscribeAnnouncer(() => heard.push(getAnnouncement()));
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("finalizing") }));
    listeners.get("app-composer")!(validComposerPayload({ request: requestState("succeeded") }));
    expect(heard.map(plain)).toEqual(["Response complete"]);
    unsub();
  });
});
