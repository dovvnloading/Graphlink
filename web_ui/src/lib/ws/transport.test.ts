import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WsTransport } from "./transport";

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
