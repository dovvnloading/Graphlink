/**
 * WebSocket transport for the single-SPA architecture (Qt-removal plan R0).
 *
 * The successor of `bridge-core/transport.ts` (QWebChannel): same conceptual
 * contract - full-state snapshots in, named intents out - carried over a
 * plain WebSocket to the Python backend instead of Qt's webchannel.
 *
 * Server -> client: {kind:"state", topic, payload}   (versioned envelope)
 *                   {kind:"result", id, value}
 *                   {kind:"error", id?, error}
 * Client -> server: {kind:"subscribe", topics}
 *                   {kind:"intent", topic, intent, args, id?}
 *
 * Reconnect: exponential-ish backoff, capped; every subscribed topic is
 * re-subscribed automatically on reopen so a backend restart re-hydrates
 * the UI without any component doing anything.
 */

export type ConnectionStatus = "connecting" | "open" | "closed";

export type StateListener = (payload: Record<string, unknown>) => void;
export type StatusListener = (status: ConnectionStatus) => void;

interface WsLike {
  send(data: string): void;
  close(): void;
  onopen: (() => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
}

export interface WsTransportOptions {
  /** Injectable for tests; defaults to the browser's WebSocket. */
  webSocketFactory?: (url: string) => WsLike;
  /** Base reconnect delay (doubles per attempt, capped at 8x). */
  reconnectDelayMs?: number;
  /** request() timeout. */
  requestTimeoutMs?: number;
}

export function defaultWsUrl(sessionId = "default"): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws?session=${encodeURIComponent(sessionId)}`;
}

export class WsTransport {
  private readonly url: string;
  private readonly factory: (url: string) => WsLike;
  private readonly baseDelay: number;
  private readonly requestTimeout: number;

  private socket: WsLike | null = null;
  private status: ConnectionStatus = "closed";
  private disposed = false;
  private attempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  private readonly stateListeners = new Map<string, Set<StateListener>>();
  private readonly statusListeners = new Set<StatusListener>();
  private readonly pending = new Map<
    number,
    { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> }
  >();
  private nextId = 1;

  constructor(url: string, options: WsTransportOptions = {}) {
    this.url = url;
    this.factory = options.webSocketFactory ?? ((u) => new WebSocket(u) as unknown as WsLike);
    this.baseDelay = options.reconnectDelayMs ?? 500;
    this.requestTimeout = options.requestTimeoutMs ?? 10_000;
  }

  connect(): void {
    if (this.disposed || this.socket) return;
    this.setStatus("connecting");
    const socket = this.factory(this.url);
    this.socket = socket;

    socket.onopen = () => {
      this.attempts = 0;
      this.setStatus("open");
      const topics = [...this.stateListeners.keys()];
      if (topics.length > 0) {
        socket.send(JSON.stringify({ kind: "subscribe", topics }));
      }
    };
    socket.onmessage = (event) => this.handleMessage(event.data);
    socket.onerror = () => {
      // onclose always follows; reconnect logic lives there.
    };
    socket.onclose = () => {
      this.socket = null;
      this.setStatus("closed");
      this.failAllPending(new Error("connection closed"));
      if (!this.disposed) this.scheduleReconnect();
    };
  }

  dispose(): void {
    this.disposed = true;
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.failAllPending(new Error("transport disposed"));
    this.socket?.close();
    this.socket = null;
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  /** Listen for a topic's snapshots. Subscribing while open sends the
   * subscribe immediately so the current snapshot arrives. */
  subscribe(topic: string, listener: StateListener): () => void {
    let set = this.stateListeners.get(topic);
    const isNewTopic = !set;
    if (!set) {
      set = new Set();
      this.stateListeners.set(topic, set);
    }
    set.add(listener);
    if (isNewTopic && this.status === "open" && this.socket) {
      this.socket.send(JSON.stringify({ kind: "subscribe", topics: [topic] }));
    }
    return () => {
      set.delete(listener);
      if (set.size === 0) this.stateListeners.delete(topic);
    };
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => this.statusListeners.delete(listener);
  }

  /** Fire-and-forget intent (the @Slot successor). Silently dropped when the
   * socket is not open - matching the old bridge's pre-connect no-op call()
   * semantics that every island already codes against. */
  intent(topic: string, intent: string, args: unknown[] = []): void {
    if (this.status !== "open" || !this.socket) return;
    this.socket.send(JSON.stringify({ kind: "intent", topic, intent, args }));
  }

  /** Intent with a reply (result or error), for request/response flows. */
  request(topic: string, intent: string, args: unknown[] = []): Promise<unknown> {
    if (this.status !== "open" || !this.socket) {
      return Promise.reject(new Error("not connected"));
    }
    const id = this.nextId++;
    const socket = this.socket;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`request timed out: ${topic}/${intent}`));
      }, this.requestTimeout);
      this.pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ kind: "intent", topic, intent, args, id }));
    });
  }

  // -- internals ---------------------------------------------------------

  private handleMessage(raw: string): void {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(raw);
    } catch {
      console.error("[ws] non-JSON frame dropped");
      return;
    }
    const kind = message.kind;
    if (kind === "state") {
      const listeners = this.stateListeners.get(message.topic as string);
      if (listeners) {
        const payload = message.payload as Record<string, unknown>;
        for (const listener of [...listeners]) listener(payload);
      }
      return;
    }
    if (kind === "result" || kind === "error") {
      const id = message.id as number | null;
      if (id !== null && id !== undefined && this.pending.has(id)) {
        const entry = this.pending.get(id)!;
        this.pending.delete(id);
        clearTimeout(entry.timer);
        if (kind === "result") entry.resolve(message.value);
        else entry.reject(new Error(String(message.error)));
      } else if (kind === "error") {
        console.error("[ws] server error:", message.error);
      }
      return;
    }
    console.error("[ws] unknown message kind:", kind);
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status === status) return;
    this.status = status;
    for (const listener of [...this.statusListeners]) listener(status);
  }

  private scheduleReconnect(): void {
    const delay = Math.min(this.baseDelay * 2 ** this.attempts, this.baseDelay * 8);
    this.attempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private failAllPending(error: Error): void {
    for (const { reject, timer } of this.pending.values()) {
      clearTimeout(timer);
      reject(error);
    }
    this.pending.clear();
  }
}
