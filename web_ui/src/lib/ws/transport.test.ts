import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WsRequestError, WsTimeoutError, WsTopicBlockedError, WsTransport, WsUnavailableError } from "./transport";
import { READER_SCHEMA_VERSION } from "../bridge-core/schemaVersion";

// ADR-003 stage 3.5: every real `kind:"state"`/`kind:"patch"` frame carries
// a schemaVersion (backend/events.py's _Topic._stamp/`_publish_now` stamp it
// unconditionally, for every topic, not just scene - see
// test_event_bus.py's own assertion on a generic test topic). Fixtures below
// that predate this stage used bare payloads because nothing checked the
// field yet; they now use the reader's OWN current version rather than a
// hardcoded number, so a future version bump does not silently make them
// start failing for a reason unrelated to what each test actually verifies.

class FakeSocket {
  static instances: FakeSocket[] = [];
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.closed = true;
    this.onclose?.();
  }

  open() {
    this.onopen?.();
  }

  receive(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  lastSent(): Record<string, unknown> {
    return JSON.parse(this.sent[this.sent.length - 1]);
  }
}

function makeTransport(opts: { requestTimeoutMs?: number } = {}) {
  return new WsTransport("ws://test/ws", {
    webSocketFactory: (url) => new FakeSocket(url),
    reconnectDelayMs: 10,
    ...opts,
  });
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("WsTransport", () => {
  it("subscribes pre-registered topics on open", () => {
    const t = makeTransport();
    const seen: unknown[] = [];
    t.subscribe("system", (p) => seen.push(p));
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    expect(socket.lastSent()).toEqual({ kind: "subscribe", topics: ["system"] });

    socket.receive({
      kind: "state",
      topic: "system",
      payload: { schemaVersion: READER_SCHEMA_VERSION, app: "graphlink", revision: 1 },
    });
    expect(seen).toEqual([{ schemaVersion: READER_SCHEMA_VERSION, app: "graphlink", revision: 1 }]);
  });

  it("subscribing a NEW topic while open sends subscribe immediately", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    t.subscribe("canvas", () => {});
    expect(socket.lastSent()).toEqual({ kind: "subscribe", topics: ["canvas"] });
  });

  it("routes snapshots only to their topic's listeners", () => {
    const t = makeTransport();
    const a: unknown[] = [];
    const b: unknown[] = [];
    t.subscribe("a", (p) => a.push(p));
    t.subscribe("b", (p) => b.push(p));
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    socket.receive({ kind: "state", topic: "a", payload: { schemaVersion: READER_SCHEMA_VERSION, x: 1 } });
    expect(a).toHaveLength(1);
    expect(b).toHaveLength(0);
  });

  it("intent() is a silent no-op before the socket opens (bridge parity)", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    t.intent("system", "ping", []);
    expect(socket.sent).toHaveLength(0);
    socket.open();
    t.intent("system", "ping", ["x"]);
    expect(socket.lastSent()).toEqual({ kind: "intent", topic: "system", intent: "ping", args: ["x"] });
  });

  it("request() resolves on result and rejects on error, matched by id", async () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();

    const ok = t.request("system", "ping", ["hi"]);
    const first = socket.lastSent();
    expect(first.id).toBeDefined();
    socket.receive({ kind: "result", id: first.id, value: { echo: ["hi"] } });
    await expect(ok).resolves.toEqual({ echo: ["hi"] });

    const bad = t.request("system", "nope", []);
    const second = socket.lastSent();
    socket.receive({ kind: "error", id: second.id, error: "unknown intent" });
    await expect(bad).rejects.toThrow("unknown intent");
  });

  // ADR-003 stage 3.1
  it("request() rejects with a WsRequestError specifically on a real {kind:error} server reply", async () => {
    // Distinguishes a genuine server-side rejection from a transport-level
    // one (not connected/closed/disposed/timeout, all still plain Error) -
    // fireIntent() below relies on this to decide when to surface a
    // notification and when to swallow silently.
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const p = t.request("system", "nope", []);
    const sent = socket.lastSent();
    socket.receive({ kind: "error", id: sent.id, error: "unknown intent" });
    await expect(p).rejects.toBeInstanceOf(WsRequestError);
  });

  it("request() rejects with WsUnavailableError, NOT WsRequestError, when never connected", async () => {
    const t = makeTransport();
    await expect(t.request("system", "ping", [])).rejects.toBeInstanceOf(WsUnavailableError);
  });

  describe("fireIntent()", () => {
    it("sends the intent id-tracked, like request(), not fire-and-forget like intent()", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      t.fireIntent("scene", "setPyCoderMode", ["n1", "manual"]);
      const sent = socket.lastSent();
      expect(sent).toEqual({ kind: "intent", topic: "scene", intent: "setPyCoderMode", args: ["n1", "manual"], id: sent.id });
      expect(sent.id).toBeDefined();
    });

    it("on a real server error reply, fires notification/showError with the error message", async () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      t.fireIntent("scene", "nope", []);
      const sent = socket.lastSent();
      socket.receive({ kind: "error", id: sent.id, error: "unknown intent: scene/nope" });
      // The rejection is handled asynchronously (a .catch callback) - flush
      // the microtask queue before asserting the follow-up intent fired.
      await Promise.resolve();
      await Promise.resolve();
      expect(socket.lastSent()).toEqual({
        kind: "intent",
        topic: "notification",
        intent: "showError",
        args: ["unknown intent: scene/nope"],
      });
    });

    it("on success, does NOT fire notification/showError", async () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      t.fireIntent("scene", "setPyCoderMode", ["n1", "manual"]);
      const sent = socket.lastSent();
      socket.receive({ kind: "result", id: sent.id, value: null });
      await Promise.resolve();
      await Promise.resolve();
      expect(socket.sent.map((raw) => JSON.parse(raw))).not.toContainEqual(
        expect.objectContaining({ intent: "showError" }),
      );
    });

    it("swallows a transport-level failure (not connected) silently, matching intent()'s own pre-migration behavior", async () => {
      // No .connect() at all - request() rejects synchronously with a
      // WsUnavailableError("not connected"), the ONE class fireIntent's
      // review-fixed catch still swallows, so it must not attempt a
      // showError round trip that could not reach the server anyway.
      const t = makeTransport();
      expect(() => t.fireIntent("scene", "setPyCoderMode", ["n1", "manual"])).not.toThrow();
      await Promise.resolve();
      await Promise.resolve();
      expect(FakeSocket.instances).toHaveLength(0);
    });

    it("review-fix: surfaces a request timeout even though the connection stays open the whole time", async () => {
      // The original version of this stage swallowed EVERY non-WsRequestError
      // rejection, including a timeout - but a timeout with the connection
      // still "open" the whole time (never closed here) is a real forced
      // failure this stage's own exit criterion covers, not a
      // connection-status condition the indicator already communicates. A
      // WsTimeoutError is deliberately NOT a WsUnavailableError, so it must
      // now surface via showError rather than being silently dropped.
      const t = makeTransport({ requestTimeoutMs: 100 });
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      t.fireIntent("scene", "setPyCoderMode", ["n1", "manual"]);
      vi.advanceTimersByTime(150);
      await Promise.resolve();
      await Promise.resolve();
      expect(t.getStatus()).toBe("open");
      expect(socket.sent.map((raw) => JSON.parse(raw))).toContainEqual({
        kind: "intent",
        topic: "notification",
        intent: "showError",
        args: ["request timed out: scene/setPyCoderMode"],
      });
    });

    it("passes a per-call timeoutMs through to request(), overriding the transport-wide default", async () => {
      // The transport-wide default here is 100ms; a call site passing its
      // own much longer override must not time out within that window - the
      // exact scenario NATIVE_DIALOG_TIMEOUT_MS-style call sites need.
      const t = makeTransport({ requestTimeoutMs: 100 });
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      t.fireIntent("scene", "pickGitlinkLocalRoot", ["n1"], 5_000);
      vi.advanceTimersByTime(150);
      await Promise.resolve();
      await Promise.resolve();
      expect(socket.sent.map((raw) => JSON.parse(raw))).not.toContainEqual(
        expect.objectContaining({ intent: "showError" }),
      );
    });

    it("review-fix: dedups an identical consecutive showError message within the dedupe window", async () => {
      // A rapid-fire call site (e.g. a text field committing on every
      // keystroke) failing with the SAME message repeatedly must not flood
      // the single-slot notification banner with a fresh showError on every
      // attempt while the underlying problem persists.
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();

      t.fireIntent("app-composer", "updateDraft", ["d1", "hello"]);
      socket.receive({ kind: "error", id: socket.lastSent().id, error: "boom" });
      await Promise.resolve();
      await Promise.resolve();

      t.fireIntent("app-composer", "updateDraft", ["d1", "hello2"]);
      socket.receive({ kind: "error", id: socket.lastSent().id, error: "boom" });
      await Promise.resolve();
      await Promise.resolve();

      const showErrors = socket.sent
        .map((raw) => JSON.parse(raw))
        .filter((m) => m.intent === "showError");
      expect(showErrors).toHaveLength(1);
    });

    it("review-fix: does NOT dedup a DIFFERENT message that arrives within the same window", async () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();

      t.fireIntent("app-composer", "updateDraft", ["d1", "hello"]);
      socket.receive({ kind: "error", id: socket.lastSent().id, error: "boom one" });
      await Promise.resolve();
      await Promise.resolve();

      t.fireIntent("app-composer", "updateDraft", ["d1", "hello2"]);
      socket.receive({ kind: "error", id: socket.lastSent().id, error: "boom two" });
      await Promise.resolve();
      await Promise.resolve();

      const showErrors = socket.sent
        .map((raw) => JSON.parse(raw))
        .filter((m) => m.intent === "showError");
      expect(showErrors).toHaveLength(2);
    });

    it("review-fix: re-fires the identical message once the dedupe window has passed", async () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();

      t.fireIntent("app-composer", "updateDraft", ["d1", "hello"]);
      socket.receive({ kind: "error", id: socket.lastSent().id, error: "boom" });
      await Promise.resolve();
      await Promise.resolve();

      vi.advanceTimersByTime(3_100);

      t.fireIntent("app-composer", "updateDraft", ["d1", "hello2"]);
      socket.receive({ kind: "error", id: socket.lastSent().id, error: "boom" });
      await Promise.resolve();
      await Promise.resolve();

      const showErrors = socket.sent
        .map((raw) => JSON.parse(raw))
        .filter((m) => m.intent === "showError");
      expect(showErrors).toHaveLength(2);
    });
  });

  it("request() rejects with a WsTimeoutError specifically when the server never answers in time", async () => {
    const t = makeTransport({ requestTimeoutMs: 100 });
    t.connect();
    FakeSocket.instances[0].open();
    const p = t.request("system", "ping", []);
    const assertion = expect(p).rejects.toBeInstanceOf(WsTimeoutError);
    vi.advanceTimersByTime(150);
    await assertion;
  });

  it("request() times out if the server never answers", async () => {
    const t = makeTransport({ requestTimeoutMs: 100 });
    t.connect();
    FakeSocket.instances[0].open();
    const p = t.request("system", "ping", []);
    const assertion = expect(p).rejects.toThrow("timed out");
    vi.advanceTimersByTime(150);
    await assertion;
  });

  it("reconnects after close and re-subscribes every topic", () => {
    const t = makeTransport();
    t.subscribe("system", () => {});
    t.connect();
    const first = FakeSocket.instances[0];
    first.open();
    first.close();
    // ADR-003 stage 3.6 review-fix: this asserted "closed" until a reconnect
    // was scheduled AND entered. A drop that will be retried is now reported
    // as "reconnecting" from the moment it happens, so the badge can hold the
    // paused state across the backoff wait instead of flickering.
    expect(t.getStatus()).toBe("reconnecting");

    vi.advanceTimersByTime(50);
    expect(FakeSocket.instances).toHaveLength(2);
    const second = FakeSocket.instances[1];
    second.open();
    expect(second.lastSent()).toEqual({ kind: "subscribe", topics: ["system"] });
    expect(t.getStatus()).toBe("open");
  });

  it("dispose() stops reconnecting and rejects pending requests", async () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const p = t.request("system", "ping", []);
    const assertion = expect(p).rejects.toThrow();
    t.dispose();
    await assertion;
    vi.advanceTimersByTime(1000);
    // No reconnect after dispose: still exactly the one original socket.
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("connect() after dispose() re-arms the transport (StrictMode remount safety)", () => {
    const t = makeTransport();
    t.subscribe("system", () => {});
    t.connect();
    const first = FakeSocket.instances[0];
    t.dispose();
    expect(first.closed).toBe(true);

    t.connect();
    expect(FakeSocket.instances).toHaveLength(2);
    const second = FakeSocket.instances[1];
    second.open();
    expect(t.getStatus()).toBe("open");
    expect(second.lastSent()).toEqual({ kind: "subscribe", topics: ["system"] });

    // The first socket's close was already delivered synchronously by
    // dispose() above; simulate it arriving again (a real WebSocket can
    // still fire a queued close event after .close() was called) and
    // confirm it doesn't clobber the second, live connection.
    first.onclose?.();
    expect(t.getStatus()).toBe("open");
  });

  it("a truly unrecognized kind still logs the existing 'unknown message kind' error (contrast with stream's silent drop)", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    socket.receive({ kind: "not-a-real-kind" });
    expect(consoleError).toHaveBeenCalledWith("[ws] unknown message kind:", "not-a-real-kind");
    consoleError.mockRestore();
  });

  // ADR-003 stage 3.4 review-fix: the patch branch, subscribePatch and
  // resubscribe shipped with ZERO tests at this level - sceneStore.test.ts
  // drives a hand-written fake that REIMPLEMENTS this routing rather than
  // exercising it, so a routing bug here would have been invisible.
  it("subscribePatch: routes a kind:'patch' frame to that topic's patch listener", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const seen: unknown[] = [];
    t.subscribePatch("scene", (patch) => seen.push(patch));

    socket.receive({
      kind: "patch",
      topic: "scene",
      schemaVersion: READER_SCHEMA_VERSION,
      revision: 7,
      baseRevision: 6,
      ops: [{ op: "removeNodes", ids: ["n0"] }],
    });

    expect(seen).toEqual([{ revision: 7, baseRevision: 6, ops: [{ op: "removeNodes", ids: ["n0"] }] }]);
  });

  it("subscribePatch: a patch frame never reaches the topic's SNAPSHOT listener", () => {
    // The two registries are parallel and must stay disjoint - a patch
    // delivered as though it were a snapshot would hit the generated
    // validator with an envelope it cannot understand.
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const snapshots: unknown[] = [];
    t.subscribe("scene", (payload) => snapshots.push(payload));

    socket.receive({
      kind: "patch",
      topic: "scene",
      schemaVersion: READER_SCHEMA_VERSION,
      revision: 2,
      baseRevision: 1,
      ops: [],
    });

    expect(snapshots).toEqual([]);
  });

  it("subscribePatch: a patch for a topic with no patch listener is dropped silently, not logged", () => {
    // Routine, not anomalous: every topic except scene is snapshot-only.
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    socket.receive({
      kind: "patch",
      topic: "grid-control",
      schemaVersion: READER_SCHEMA_VERSION,
      revision: 2,
      baseRevision: 1,
      ops: [],
    });
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("subscribePatch: unsubscribing stops delivery", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const seen: unknown[] = [];
    const off = t.subscribePatch("scene", (patch) => seen.push(patch));
    off();
    socket.receive({
      kind: "patch",
      topic: "scene",
      schemaVersion: READER_SCHEMA_VERSION,
      revision: 2,
      baseRevision: 1,
      ops: [],
    });
    expect(seen).toEqual([]);
  });

  it("a patch topic is re-subscribed on reconnect even with no snapshot listener", () => {
    // Review-fix: the reconnect topic list came from stateListeners alone,
    // so a patch-only consumer was never subscribed server-side at all and
    // never re-subscribed - it silently received nothing forever.
    const t = makeTransport();
    t.connect();
    FakeSocket.instances[0].open();
    t.subscribePatch("scene", () => {});

    FakeSocket.instances[0].onclose?.();
    vi.advanceTimersByTime(10_000);
    const reconnected = FakeSocket.instances[1];
    reconnected.open();

    expect(reconnected.lastSent()).toEqual({ kind: "subscribe", topics: ["scene"] });
  });

  it("resubscribe() re-requests a snapshot for an already-subscribed topic", () => {
    // subscribe() sends its message only for a topic's FIRST listener, so
    // this is the only way an already-subscribed topic can ask for fresh
    // state - the scene store's gap recovery depends on it.
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    t.subscribe("scene", () => {});

    t.resubscribe("scene");

    expect(socket.lastSent()).toEqual({ kind: "subscribe", topics: ["scene"] });
  });

  it("resubscribe() is a no-op while the socket is not open", () => {
    const t = makeTransport();
    t.connect();
    // never opened - lastSent() parses JSON, so assert on the raw list.
    expect(() => t.resubscribe("scene")).not.toThrow();
    expect(FakeSocket.instances[0].sent).toEqual([]);
  });

  it("subscribeStream: a stream frame with no matching requestId subscriber is silently dropped", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    socket.receive({ kind: "stream", topic: "app-composer", requestId: "no-such-request", seq: 0, delta: "hi", done: false, reset: false });
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("subscribeStream: routes deltas only to the matching requestId and fans out to every listener on it", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const forA1: unknown[] = [];
    const forA2: unknown[] = [];
    const forB: unknown[] = [];
    t.subscribeStream("req-a", (delta, done, reset, seq) => forA1.push({ delta, done, reset, seq }));
    t.subscribeStream("req-a", (delta, done, reset, seq) => forA2.push({ delta, done, reset, seq }));
    t.subscribeStream("req-b", (delta, done, reset, seq) => forB.push({ delta, done, reset, seq }));

    socket.receive({ kind: "stream", topic: "app-composer", requestId: "req-a", seq: 0, delta: "Hel", done: false, reset: false });
    socket.receive({ kind: "stream", topic: "app-composer", requestId: "req-a", seq: 1, delta: "lo", done: true, reset: false });

    expect(forA1).toEqual([
      { delta: "Hel", done: false, reset: false, seq: 0 },
      { delta: "lo", done: true, reset: false, seq: 1 },
    ]);
    expect(forA2).toEqual(forA1);
    expect(forB).toEqual([]);
  });

  it("subscribeStream: the returned unsubscribe stops further delivery and cleans up an empty requestId entry", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const seen: unknown[] = [];
    const unsub = t.subscribeStream("req-a", (delta) => seen.push(delta));
    socket.receive({ kind: "stream", topic: "app-composer", requestId: "req-a", seq: 0, delta: "one", done: false, reset: false });
    unsub();
    socket.receive({ kind: "stream", topic: "app-composer", requestId: "req-a", seq: 1, delta: "two", done: false, reset: false });
    expect(seen).toEqual(["one"]);

    // Now that no listener remains, this must silently drop (not error).
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    socket.receive({ kind: "stream", topic: "app-composer", requestId: "req-a", seq: 2, delta: "three", done: true, reset: false });
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  // ADR-011 review-fix (HIGH): ADR-011's onlyRenderVisibleElements
  // virtualization genuinely UNMOUNTS a live-streaming node's component
  // (ChatNodeView/ConversationNodeView/CodeSandboxNodeView) when panned
  // off-screen, then mounts a BRAND NEW instance when panned back - and this
  // class has no server-side subscribe to replay from (see subscribeStream's
  // own doc: "the server broadcasts stream frames to every connection
  // unconditionally"). Without a client-side buffer, every delta broadcast
  // during the unmounted window used to be lost forever, and the new
  // instance's freshly-initialized local state had nothing to fall back on.
  it("subscribeStream: replays everything accumulated so far to a listener that (re)subscribes after an earlier listener unsubscribed mid-stream (ADR-011 virtualization unmount/remount)", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();

    const firstMount: unknown[] = [];
    const unsub = t.subscribeStream("req-a", (delta, done, reset, seq) => firstMount.push({ delta, done, reset, seq }));
    socket.receive({ kind: "stream", topic: "chat", requestId: "req-a", seq: 0, delta: "Hel", done: false, reset: false });
    socket.receive({ kind: "stream", topic: "chat", requestId: "req-a", seq: 1, delta: "lo ", done: false, reset: false });
    // Simulates the node scrolling off-screen: the component unmounts and
    // its effect cleanup unsubscribes.
    unsub();

    // A delta broadcast while nothing is subscribed - the exact "off-screen"
    // window this fix exists for.
    socket.receive({ kind: "stream", topic: "chat", requestId: "req-a", seq: 2, delta: "there", done: false, reset: false });

    // Simulates the node scrolling back into view: a BRAND NEW component
    // instance subscribes fresh.
    const secondMount: unknown[] = [];
    t.subscribeStream("req-a", (delta, done, reset, seq) => secondMount.push({ delta, done, reset, seq }));

    // It must catch up on everything broadcast so far, as a single synthetic
    // reset frame - not just deltas that arrive from this point forward -
    // and the first (now-unsubscribed) mount must not receive it.
    expect(secondMount).toEqual([{ delta: "Hello there", done: false, reset: true, seq: 2 }]);
    expect(firstMount).toEqual([
      { delta: "Hel", done: false, reset: false, seq: 0 },
      { delta: "lo ", done: false, reset: false, seq: 1 },
    ]);

    // Live deltas continue to arrive normally afterward.
    socket.receive({ kind: "stream", topic: "chat", requestId: "req-a", seq: 3, delta: "!", done: true, reset: false });
    expect(secondMount).toEqual([
      { delta: "Hello there", done: false, reset: true, seq: 2 },
      { delta: "!", done: true, reset: false, seq: 3 },
    ]);
  });

  it("subscribeStream: a brand-new requestId nothing has streamed for yet gets no replay (unchanged first-subscribe behavior)", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const seen: unknown[] = [];
    t.subscribeStream("req-fresh", (delta, done, reset, seq) => seen.push({ delta, done, reset, seq }));
    expect(seen).toEqual([]);
  });

  it("subscribeStream: does not replay once the request has already completed (buffer is cleared on done)", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    const unsub = t.subscribeStream("req-a", () => {});
    socket.receive({ kind: "stream", topic: "chat", requestId: "req-a", seq: 0, delta: "finished text", done: true, reset: false });
    unsub();

    // A later subscriber (e.g. an unrelated remount racing a stale
    // requestId) must not see the completed run's text replayed - by this
    // point the persisted `content` field is the real source of truth.
    const seen: unknown[] = [];
    t.subscribeStream("req-a", (delta, done, reset, seq) => seen.push({ delta, done, reset, seq }));
    expect(seen).toEqual([]);
  });

  it("notifies status listeners through the lifecycle", () => {
    const t = makeTransport();
    const statuses: string[] = [];
    t.onStatus((s) => statuses.push(s));
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    socket.close();
    // ADR-003 stage 3.6 review-fix: the final "closed" became "reconnecting".
    // A drop that WILL be retried is not the same state as a transport that
    // has given up, and conflating them is what left the badge showing the
    // bare word "closed" for the whole backoff wait. "closed" is now reserved
    // for a disposed transport - nothing is coming back.
    expect(statuses).toEqual(["closed", "connecting", "open", "reconnecting"]);
  });

  // ADR-003 stage 3.5: version negotiation moved here from the dead
  // bridge-core/islandState.ts path. A `minCompatibleSchemaVersion` set
  // absurdly high (rather than hand-computing "one more than the current
  // reader") is deliberate - it stays correct across any future reader
  // version bump instead of silently under-testing the boundary again the
  // way transport.test.ts's own pre-3.5 fixtures under-tested this exact
  // gap by omitting the field entirely.
  describe("schema-version negotiation (ADR-003 stage 3.5)", () => {
    it("withholds an incompatible state frame from the topic's snapshot listener", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      const seen: unknown[] = [];
      t.subscribe("scene", (p) => seen.push(p));

      socket.receive({
        kind: "state",
        topic: "scene",
        payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [] },
      });

      expect(seen).toEqual([]);
    });

    it("withholds an incompatible patch frame from the topic's patch listener", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      const seen: unknown[] = [];
      t.subscribePatch("scene", (p) => seen.push(p));

      socket.receive({
        kind: "patch",
        topic: "scene",
        schemaVersion: 2,
        minCompatibleSchemaVersion: 99,
        revision: 2,
        baseRevision: 1,
        ops: [],
      });

      expect(seen).toEqual([]);
    });

    it("publishes the rejection through onVersionRejection, with a human-readable reason", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      const seen: (unknown | null)[] = [];
      t.onVersionRejection("scene", (r) => seen.push(r));

      socket.receive({
        kind: "state",
        topic: "scene",
        payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [] },
      });

      // First call is the immediate "no rejection yet" delivery on subscribe.
      expect(seen).toHaveLength(2);
      expect(seen[0]).toBeNull();
      expect(seen[1]).toMatchObject({ kind: "version" });
      expect((seen[1] as { reason: string }).reason).toContain("99");
    });

    it("onVersionRejection delivers the CURRENT state immediately on subscribe, matching onStatus's contract", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      socket.receive({
        kind: "state",
        topic: "scene",
        payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [] },
      });

      // Subscribing AFTER the rejection already happened must not require a
      // second incompatible frame to find out about it.
      const seen: (unknown | null)[] = [];
      t.onVersionRejection("scene", (r) => seen.push(r));
      expect(seen).toHaveLength(1);
      expect(seen[0]).toMatchObject({ kind: "version" });
    });

    it("a subsequent compatible frame clears the rejection and resumes normal dispatch", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      const rejections: (unknown | null)[] = [];
      const snapshots: unknown[] = [];
      t.onVersionRejection("scene", (r) => rejections.push(r));
      t.subscribe("scene", (p) => snapshots.push(p));

      socket.receive({
        kind: "state",
        topic: "scene",
        payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [] },
      });
      expect(snapshots).toEqual([]);

      socket.receive({
        kind: "state",
        topic: "scene",
        payload: { schemaVersion: 2, minCompatibleSchemaVersion: 2, nodes: [] },
      });

      expect(rejections.at(-1)).toBeNull();
      expect(snapshots).toEqual([{ schemaVersion: 2, minCompatibleSchemaVersion: 2, nodes: [] }]);
    });

    it("does not re-fire the rejection listener for repeated frames with the identical reason", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      const seen: (unknown | null)[] = [];
      t.onVersionRejection("scene", (r) => seen.push(r));

      const badFrame = {
        kind: "state" as const,
        topic: "scene",
        payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [] },
      };
      socket.receive(badFrame);
      socket.receive(badFrame);
      socket.receive(badFrame);

      // The immediate null delivery on subscribe, plus exactly ONE rejection.
      expect(seen).toHaveLength(2);
    });

    it("rejection on one topic does not affect a different topic's listener", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      const sceneRejections: (unknown | null)[] = [];
      const gridSeen: unknown[] = [];
      t.onVersionRejection("scene", (r) => sceneRejections.push(r));
      t.subscribe("grid-control", (p) => gridSeen.push(p));

      socket.receive({
        kind: "state",
        topic: "scene",
        payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [] },
      });
      socket.receive({
        kind: "state",
        topic: "grid-control",
        payload: { schemaVersion: READER_SCHEMA_VERSION, snapToGrid: false },
      });

      expect(sceneRejections.at(-1)).toMatchObject({ kind: "version" });
      expect(gridSeen).toEqual([{ schemaVersion: READER_SCHEMA_VERSION, snapToGrid: false }]);
    });

    // Review-fix (test-coverage gap): the test above only pairs one
    // REJECTED topic with one COMPATIBLE topic - nothing proved two
    // DIFFERENT topics could be simultaneously rejected, each keeping its
    // own distinct reason and clearing independently. The dedup/rejection
    // state is correctly per-topic by construction (keyed Maps), but
    // nothing would have caught a future refactor that accidentally
    // hoisted any of it onto a shared field.
    it("review-fix: two topics can be simultaneously rejected with independent reasons, and clear independently", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      const sceneRejections: (unknown | null)[] = [];
      const gridRejections: (unknown | null)[] = [];
      t.onVersionRejection("scene", (r) => sceneRejections.push(r));
      t.onVersionRejection("grid-control", (r) => gridRejections.push(r));

      socket.receive({
        kind: "state",
        topic: "scene",
        payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [] },
      });
      socket.receive({
        kind: "state",
        topic: "grid-control",
        payload: { schemaVersion: 1, minCompatibleSchemaVersion: 50, snapToGrid: false },
      });

      expect(sceneRejections.at(-1)).toMatchObject({ kind: "version" });
      expect(gridRejections.at(-1)).toMatchObject({ kind: "version" });
      expect((sceneRejections.at(-1) as { reason: string }).reason).toContain("99");
      expect((gridRejections.at(-1) as { reason: string }).reason).toContain("50");
      expect(sceneRejections.at(-1)).not.toEqual(gridRejections.at(-1));

      // Re-sending scene's identical bad frame must not re-fire scene NOR
      // touch grid-control's independent rejection.
      socket.receive({
        kind: "state",
        topic: "scene",
        payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [] },
      });
      expect(sceneRejections).toHaveLength(2); // immediate-null + the one rejection
      expect(gridRejections).toHaveLength(2);

      // Clearing scene must not clear grid-control.
      socket.receive({
        kind: "state",
        topic: "scene",
        payload: { schemaVersion: 2, minCompatibleSchemaVersion: 2, nodes: [] },
      });
      expect(sceneRejections.at(-1)).toBeNull();
      expect(gridRejections.at(-1)).toMatchObject({ kind: "version" });
    });
  });

  // ADR-003 stage 3.5 review-fix: closes a real gap a 4-lens adversarial
  // review found - only SceneCanvas checked any form of version-rejection
  // signal; every OTHER scene-mutating call site (ViewPopover, Composer,
  // PinOverlay, the command palette - all ~84 of sceneStore.ts's own
  // fireIntent call sites) fired real intents completely unguarded while
  // the client had just declared it could not trust the topic's data.
  // setTopicBlocked is the single choke point that closes all of them at
  // once, since every one of those call sites already funnels through
  // request()/intent().
  describe("setTopicBlocked (ADR-003 stage 3.5 review-fix)", () => {
    it("request() (and so fireIntent()) rejects with WsTopicBlockedError for a blocked topic", async () => {
      const t = makeTransport();
      t.connect();
      FakeSocket.instances[0].open();
      t.setTopicBlocked("scene", true);

      await expect(t.request("scene", "addNode", [0, 0, "x"])).rejects.toBeInstanceOf(WsTopicBlockedError);
    });

    it("fireIntent() surfaces a blocked-topic rejection via showError - NOT swallowed like WsUnavailableError", async () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      t.setTopicBlocked("scene", true);

      t.fireIntent("scene", "addNode", [0, 0, "x"]);
      await Promise.resolve();
      await Promise.resolve();

      expect(socket.sent.map((raw) => JSON.parse(raw))).toContainEqual(
        expect.objectContaining({ topic: "notification", intent: "showError" }),
      );
      // And the blocked intent itself was never actually sent.
      expect(socket.sent.map((raw) => JSON.parse(raw))).not.toContainEqual(
        expect.objectContaining({ topic: "scene", intent: "addNode" }),
      );
    });

    it("intent() silently no-ops for a blocked topic, same posture as not-connected", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      t.setTopicBlocked("scene", true);

      t.intent("scene", "setSnapToGrid", [true]);
      expect(socket.sent).toHaveLength(0);
    });

    it("unblocking resumes normal send behavior", async () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      t.setTopicBlocked("scene", true);
      t.setTopicBlocked("scene", false);

      const p = t.request("scene", "addNode", [0, 0, "x"]);
      const sent = socket.lastSent();
      expect(sent).toMatchObject({ topic: "scene", intent: "addNode" });
      socket.receive({ kind: "result", id: sent.id, value: null });
      await expect(p).resolves.toBeNull();
    });

    it("blocking one topic does not affect a different topic's request()/intent()", () => {
      const t = makeTransport();
      t.connect();
      const socket = FakeSocket.instances[0];
      socket.open();
      t.setTopicBlocked("scene", true);

      t.intent("notification", "showInfo", ["still works"]);
      expect(socket.lastSent()).toEqual({
        kind: "intent",
        topic: "notification",
        intent: "showInfo",
        args: ["still works"],
      });
    });
  });

  // Review-fix (LOW, defensive): versionRejections had no cleanup path when
  // a topic's listeners all unsubscribe, unlike every sibling registry in
  // this file. Not a live bug (the app's one real subscriber never
  // unsubscribes), but a genuine asymmetry - a short-lived subscriber that
  // unsubscribed from a rejected topic and later resubscribed would
  // otherwise see the transport immediately replay the stale prior
  // rejection instead of starting clean.
  it("review-fix: forgets a topic's cached rejection once every listener of every kind has unsubscribed", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();

    const off = t.onVersionRejection("scene", () => {});
    socket.receive({
      kind: "state",
      topic: "scene",
      payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [] },
    });
    off();

    // A brand-new subscriber must see a clean "no rejection yet" state, not
    // the stale one left behind by the unsubscribed listener above.
    const seen: (unknown | null)[] = [];
    t.onVersionRejection("scene", (r) => seen.push(r));
    expect(seen).toEqual([null]);
  });

  it("review-fix: does NOT forget the cached rejection while a state/patch listener is still attached", () => {
    // The cleanup must be keyed off ALL three per-topic registries, not
    // versionRejectionListeners alone - a topic can have a live snapshot
    // listener with no rejection listener attached.
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();

    t.subscribe("scene", () => {}); // stays attached
    const offRejection = t.onVersionRejection("scene", () => {});
    socket.receive({
      kind: "state",
      topic: "scene",
      payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [] },
    });
    offRejection();

    const seen: (unknown | null)[] = [];
    t.onVersionRejection("scene", (r) => seen.push(r));
    expect(seen).toEqual([{ kind: "version", reason: expect.any(String), details: [] }]);
  });
});

// ADR-003 stage 3.6: closes the last "vanishes silently" gap (D5). Every
// fireIntent call site across the app used to be dropped with nothing but
// a console.error the instant the socket wasn't open, no exceptions. A
// `queueable: true` intent is now held and replayed in order on the next
// reconnect; everything else is counted and surfaced as one summary banner
// once the connection is actually able to deliver it again (see
// droppedWhileOffline's own doc in transport.ts for why "surface it
// immediately" cannot work - the notification banner is itself
// server-authoritative and needs the very connection that just failed).
describe("offline intent queue (ADR-003 stage 3.6)", () => {
  it("a queueable intent fired before the socket ever opens is held, not dropped", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];

    t.fireIntent("scene", "moveNodes", [[{ id: "n1", x: 1, y: 2 }]], undefined, true);
    expect(socket.sent).toHaveLength(0);

    socket.open();
    expect(socket.sent.map((raw) => JSON.parse(raw))).toContainEqual(
      expect.objectContaining({ topic: "scene", intent: "moveNodes" }),
    );
  });

  it("queued intents replay in the order they were fired", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];

    t.fireIntent("scene", "moveNodes", ["first"], undefined, true);
    t.fireIntent("app-composer", "updateDraft", ["second"], undefined, true);
    t.fireIntent("scene", "setViewState", ["third"], undefined, true);

    socket.open();
    const sent = socket.sent.map((raw) => JSON.parse(raw));
    expect(sent.map((m) => m.args[0])).toEqual(["first", "second", "third"]);
  });

  it("replay happens AFTER the resubscribe message, not before", () => {
    const t = makeTransport();
    t.subscribe("scene", () => {});
    t.connect();
    const socket = FakeSocket.instances[0];
    t.fireIntent("scene", "moveNodes", [[]], undefined, true);

    socket.open();
    const sent = socket.sent.map((raw) => JSON.parse(raw));
    expect(sent[0]).toEqual({ kind: "subscribe", topics: ["scene"] });
    expect(sent[1]).toMatchObject({ topic: "scene", intent: "moveNodes" });
  });

  it("a non-queueable intent fired while not open is dropped and reported as one summary banner on reconnect", async () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];

    t.fireIntent("scene", "sendMessage", ["hello"]); // queueable defaults to false
    expect(socket.sent).toHaveLength(0);

    socket.open();
    // The dropped-message intent itself was never queued for replay...
    expect(socket.sent.map((raw) => JSON.parse(raw))).not.toContainEqual(
      expect.objectContaining({ intent: "sendMessage" }),
    );
    // ...and a visible summary was sent instead, now that the connection
    // that can actually deliver it exists again.
    expect(socket.sent.map((raw) => JSON.parse(raw))).toContainEqual({
      kind: "intent",
      topic: "notification",
      intent: "showError",
      args: ["1 change could not be sent while disconnected."],
    });
  });

  it("multiple dropped non-queueable intents are reported as one combined count, not one banner each", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];

    t.fireIntent("scene", "sendMessage", ["a"]);
    t.fireIntent("scene", "removeNodes", [["n1"]]);
    t.fireIntent("scene", "approveCodeExecution", ["r1"]);

    socket.open();
    const showErrors = socket.sent.map((raw) => JSON.parse(raw)).filter((m) => m.intent === "showError");
    expect(showErrors).toEqual([
      { kind: "intent", topic: "notification", intent: "showError", args: ["3 changes could not be sent while disconnected."] },
    ]);
  });

  it("a queueable intent that was genuinely in flight and gets cut off by a disconnect is replayed, not lost", async () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();

    t.fireIntent("scene", "moveNodes", [[{ id: "n1", x: 5, y: 5 }]], undefined, true);
    expect(socket.sent).toHaveLength(1); // sent normally while open

    // The connection dies before any reply arrives.
    socket.onclose?.();
    await Promise.resolve();
    await Promise.resolve();

    // No error was shown for this one - it's recoverable, not lost.
    expect(socket.sent.map((raw) => JSON.parse(raw))).not.toContainEqual(
      expect.objectContaining({ intent: "showError" }),
    );

    vi.advanceTimersByTime(10_000);
    const reconnected = FakeSocket.instances[1];
    reconnected.open();

    expect(reconnected.sent.map((raw) => JSON.parse(raw))).toContainEqual(
      expect.objectContaining({ topic: "scene", intent: "moveNodes" }),
    );
  });

  it("a non-queueable intent that was in flight and gets cut off is counted, not silently lost", async () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();

    t.fireIntent("scene", "sendMessage", ["hello"]); // queueable defaults to false
    socket.onclose?.();
    await Promise.resolve();
    await Promise.resolve();

    vi.advanceTimersByTime(10_000);
    const reconnected = FakeSocket.instances[1];
    reconnected.open();

    expect(reconnected.sent.map((raw) => JSON.parse(raw))).toContainEqual({
      kind: "intent",
      topic: "notification",
      intent: "showError",
      args: ["1 change could not be sent while disconnected."],
    });
  });

  it("the queue is bounded at 50 - the 51st queueable intent is counted as dropped, not queued", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];

    for (let i = 0; i < 51; i++) {
      t.fireIntent("scene", "moveNodes", [[{ id: `n${i}`, x: i, y: i }]], undefined, true);
    }

    socket.open();
    const sent = socket.sent.map((raw) => JSON.parse(raw));
    const replayed = sent.filter((m) => m.intent === "moveNodes");
    expect(replayed).toHaveLength(50);
    expect(sent).toContainEqual({
      kind: "intent",
      topic: "notification",
      intent: "showError",
      args: ["1 change could not be sent while disconnected."],
    });
  });

  it("status is 'reconnecting', not 'connecting', on every attempt after a real connection was lost", () => {
    const t = makeTransport();
    const statuses: string[] = [];
    t.onStatus((s) => statuses.push(s));
    t.connect();
    FakeSocket.instances[0].open(); // first-ever open: "connecting" was correct
    FakeSocket.instances[0].onclose?.();

    vi.advanceTimersByTime(10_000); // the scheduled retry's connect() fires

    // Review-fix: no "closed" between "open" and "reconnecting". onclose now
    // publishes the paused state directly - see the next test for why.
    expect(statuses).toEqual(["closed", "connecting", "open", "reconnecting"]);
  });

  // The paused state has to cover the whole outage. Publishing "closed" on
  // disconnect and only entering "reconnecting" when the retry timer fires
  // meant the badge showed the bare word "closed" for the entire backoff
  // sleep - which is where all the wall-clock time goes - and flickered
  // "reconnecting" for the few ms of each attempt against a refused port.
  // Intents are being queued and counted that entire time, so this is the
  // half of the exit criterion the user actually sees.
  it("holds 'reconnecting' for the whole backoff wait, never reverting to 'closed'", () => {
    const t = makeTransport();
    const statuses: string[] = [];
    t.connect();
    FakeSocket.instances[0].open();
    t.onStatus((s) => statuses.push(s));

    FakeSocket.instances[0].onclose?.();
    expect(t.getStatus()).toBe("reconnecting"); // immediately, not after the sleep

    // Right through the backoff sleep and a second FAILED attempt, the user
    // never sees "closed" again.
    vi.advanceTimersByTime(400);
    expect(t.getStatus()).toBe("reconnecting");
    vi.advanceTimersByTime(200); // retry fires
    FakeSocket.instances[1].onclose?.();
    vi.advanceTimersByTime(2_000);

    expect(statuses).not.toContain("closed");
    expect(t.getStatus()).toBe("reconnecting");
  });

  // The counterpart to the guard above: a blocked topic is never QUEUED, but
  // it must still be COUNTED. Letting it fall through to request() produced a
  // WsTopicBlockedError whose banner intent() then dropped for want of an
  // open socket - not queued, not counted, no banner ever, which is the exact
  // D5 bug this stage closes, reintroduced in the one combination the
  // blocked-topic guard itself created.
  it("counts a blocked topic's intent dropped while offline instead of losing it silently", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    t.setTopicBlocked("scene", true);
    socket.onclose?.();

    t.fireIntent("scene", "sendMessage", ["hello"]);
    t.fireIntent("scene", "moveNodes", [[{ id: "n1", x: 1, y: 2 }]], undefined, true);

    vi.advanceTimersByTime(500);
    const reconnected = FakeSocket.instances[1];
    reconnected.open();

    const sent = reconnected.sent.map((raw) => JSON.parse(raw));
    // Neither was replayed - the topic is still blocked, still untrusted...
    expect(sent).not.toContainEqual(expect.objectContaining({ intent: "sendMessage" }));
    expect(sent).not.toContainEqual(expect.objectContaining({ intent: "moveNodes" }));
    // ...but the user is told both were lost, rather than nothing at all.
    expect(sent).toContainEqual({
      kind: "intent",
      topic: "notification",
      intent: "showError",
      args: ["2 changes could not be sent while disconnected."],
    });
  });

  it("a genuine server-side rejection on replay still surfaces normally, not silently re-queued", async () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    t.fireIntent("scene", "moveNodes", [[]], undefined, true);

    socket.open();
    const sent = socket.lastSent();
    socket.receive({ kind: "error", id: sent.id, error: "unknown node" });
    await Promise.resolve();
    await Promise.resolve();

    expect(socket.sent.map((raw) => JSON.parse(raw))).toContainEqual({
      kind: "intent",
      topic: "notification",
      intent: "showError",
      args: ["unknown node"],
    });
  });

  // Stage 3.6 x stage 3.5: setTopicBlocked exists because this client cannot
  // trust the state a blocked topic's args were computed against. Queueing
  // such an intent would defer exactly that untrusted send to whenever the
  // block lifts, and would spend the bounded queue's finite slots on intents
  // certain to be refused - displacing recoverable ones from other topics.
  // NB: asserting only "the blocked intent never reaches the wire" would be a
  // FALSE-CONFIDENCE test - request()'s own blockedTopics check already stops
  // it at replay time, so it passes with or without the guard above (verified
  // by mutation). The behaviour that genuinely distinguishes them is what
  // happens when the block LIFTS before the socket returns.
  it("an intent refused while its topic was blocked is not resurrected once the block lifts", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    t.setTopicBlocked("scene", true);

    t.fireIntent("scene", "moveNodes", [[{ id: "n1", x: 1, y: 2 }]], undefined, true);
    // A queueable intent on an UNBLOCKED topic still queues normally, so this
    // also asserts the check is topic-scoped rather than a blanket bypass.
    t.fireIntent("app-composer", "updateDraft", ["kept"], undefined, true);

    // The version-rejection episode ends before the connection returns. Were
    // the scene intent sitting in the queue, it would now be replayed for
    // real - against state this client was explicitly told not to trust.
    t.setTopicBlocked("scene", false);
    socket.open();

    const sent = socket.sent.map((raw) => JSON.parse(raw));
    expect(sent).not.toContainEqual(expect.objectContaining({ intent: "moveNodes" }));
    expect(sent).toContainEqual(expect.objectContaining({ intent: "updateDraft", args: ["kept"] }));
  });

  it("a blocked topic's refused intent does not consume an offline-queue slot", () => {
    const t = makeTransport();
    t.connect();
    const socket = FakeSocket.instances[0];
    t.setTopicBlocked("scene", true);

    // 50 blocked-topic intents would fill the whole queue if they were being
    // enqueued; none of them should be.
    for (let i = 0; i < 50; i++) {
      t.fireIntent("scene", "moveNodes", [[{ id: `n${i}`, x: i, y: i }]], undefined, true);
    }
    t.fireIntent("app-composer", "updateDraft", ["still room"], undefined, true);

    socket.open();
    const sent = socket.sent.map((raw) => JSON.parse(raw));
    expect(sent).toContainEqual(expect.objectContaining({ intent: "updateDraft", args: ["still room"] }));
  });

  // The summary must NOT go through showErrorDeduped. That de-dup suppresses
  // an identical message inside a 3s window - and the reconnect backoff's
  // first retry is 500ms, so two quick outages that each lose one change
  // produce the identical string well inside the window. Swallowing the
  // second would under-report real data loss: the user is told one change was
  // lost when two were. Under-reporting loss is the exact failure this whole
  // stage exists to remove, so it cannot be reintroduced via the de-dup.
  it("reports a second outage's losses even though the count message is identical", () => {
    const t = makeTransport();
    t.connect();
    const first = FakeSocket.instances[0];
    first.open();

    first.onclose?.();
    t.fireIntent("scene", "sendMessage", ["a"]);
    vi.advanceTimersByTime(500);
    const second = FakeSocket.instances[1];
    second.open();
    expect(
      second.sent.map((raw) => JSON.parse(raw)).filter((m) => m.intent === "showError"),
    ).toEqual([
      { kind: "intent", topic: "notification", intent: "showError", args: ["1 change could not be sent while disconnected."] },
    ]);

    second.onclose?.();
    t.fireIntent("scene", "sendMessage", ["b"]);
    vi.advanceTimersByTime(500);
    const third = FakeSocket.instances[2];
    third.open();
    expect(
      third.sent.map((raw) => JSON.parse(raw)).filter((m) => m.intent === "showError"),
    ).toEqual([
      { kind: "intent", topic: "notification", intent: "showError", args: ["1 change could not be sent while disconnected."] },
    ]);
  });
});
