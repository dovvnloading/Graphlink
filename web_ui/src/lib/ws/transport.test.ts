import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WsRequestError, WsTimeoutError, WsTransport, WsUnavailableError } from "./transport";

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

    socket.receive({ kind: "state", topic: "system", payload: { app: "graphlink", revision: 1 } });
    expect(seen).toEqual([{ app: "graphlink", revision: 1 }]);
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
    socket.receive({ kind: "state", topic: "a", payload: { x: 1 } });
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
    expect(t.getStatus()).toBe("closed");

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

  it("notifies status listeners through the lifecycle", () => {
    const t = makeTransport();
    const statuses: string[] = [];
    t.onStatus((s) => statuses.push(s));
    t.connect();
    const socket = FakeSocket.instances[0];
    socket.open();
    socket.close();
    expect(statuses).toEqual(["closed", "connecting", "open", "closed"]);
  });
});
