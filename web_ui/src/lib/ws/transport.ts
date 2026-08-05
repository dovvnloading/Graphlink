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
 *                   {kind:"stream", topic, requestId, seq, delta, done, reset}
 *                     (R4.4 token streaming - a sibling delta channel outside
 *                     the topic/revision system; addressed by requestId, not
 *                     subscribed like a topic. See subscribeStream().)
 * Client -> server: {kind:"subscribe", topics}
 *                   {kind:"intent", topic, intent, args, id?}
 *
 * Reconnect: exponential-ish backoff, capped; every subscribed topic is
 * re-subscribed automatically on reopen so a backend restart re-hydrates
 * the UI without any component doing anything.
 *
 * ADR-003 stage 3.1: fireIntent() is the new default a store's own mutating
 * intent call sites use in place of the old bare intent() - internally
 * id-tracked, and a real failure (WsRequestError or WsTimeoutError) is
 * surfaced via the existing notification banner instead of silently lost.
 * intent() itself still exists for the rare genuinely-fire-and-forget case
 * (e.g. surfacing an error THROUGH the notification system itself, which
 * must not recurse back into fireIntent's own error handling).
 *
 * Review-fix (a 4-lens adversarial review of the first version of this
 * stage): the original design swallowed EVERY non-WsRequestError rejection,
 * including a client-side timeout - but a timeout with the connection still
 * "open" the whole time is a real forced failure this stage's own exit
 * criterion ("error visible in UI for a forced failure") covers, not a
 * connection-status condition the existing indicator already communicates.
 * fireIntent() now surfaces everything EXCEPT WsUnavailableError (the three
 * genuinely connection-level rejection reasons: not connected/closed/
 * disposed) - inverted from "surface only what I explicitly recognize" to
 * "swallow only what I explicitly know is already visible elsewhere",
 * closing the gap a future new rejection reason could otherwise silently
 * fall into again.
 */

import { withAuthToken } from "../auth/token";

export type ConnectionStatus = "connecting" | "open" | "closed";

/** ADR-003 stage 3.1: raised ONLY when the server actually replied with a
 * structured {"kind":"error"} frame (unknown topic/intent, or an unhandled
 * exception inside a handler) - i.e. the connection genuinely round-tripped
 * and the server itself rejected the request. */
export class WsRequestError extends Error {}

/** ADR-003 stage 3.1 review-fix: raised when a request() times out waiting
 * for a reply - distinct from WsRequestError (the server never actually
 * answered either way) and from WsUnavailableError (the connection was, as
 * far as this client can tell, genuinely open the whole time - a timeout is
 * NOT a connection-status condition, so it must not be swallowed the way
 * WsUnavailableError is). */
export class WsTimeoutError extends Error {}

/** ADR-003 stage 3.1 review-fix: raised for the three rejection reasons that
 * genuinely correlate with the connection-status indicator already showing
 * the user something is wrong (never connected, closed mid-request, or this
 * transport instance was disposed) - the ONLY rejection reason fireIntent()
 * swallows silently. Real disconnect UX (queue-while-reconnecting or a
 * visible "paused" state) is ADR-003 stage 3.6, not this one; until then,
 * silently dropping THESE specific three matches `intent()`'s own
 * pre-migration fire-and-forget behavior exactly. */
export class WsUnavailableError extends Error {}

export type StateListener = (payload: Record<string, unknown>) => void;
export type StatusListener = (status: ConnectionStatus) => void;
export type StreamListener = (delta: string, done: boolean, reset: boolean, seq: number) => void;

/** ADR-003 stage 3.4: one node-scoped delta from a `kind:"patch"` frame.
 * Deliberately typed loosely (`Record<string, unknown>` for the node/edge
 * bodies) - the transport's job is routing, not validating. Nothing here
 * checks the shape, so a consumer MUST validate before trusting it;
 * SceneStore.applyScenePatch does that by running the generated scene
 * validator over the RESULT of applying a patch and discarding the whole
 * patch if it fails. (An earlier version of this comment asserted that
 * validation as though it were already happening - it was not, which is
 * exactly how unvalidated ops reached the store.) */
export type ScenePatchOp = Record<string, unknown> & { op: string };

/** ADR-003 stage 3.4: a patch frame's contents, handed to a patch listener.
 * `baseRevision` is the revision the server believes this client is at; a
 * client whose own revision differs has missed a frame and must re-snapshot
 * rather than apply this out of order. */
export interface ScenePatch {
  revision: number;
  baseRevision: number;
  ops: ScenePatchOp[];
}

export type PatchListener = (patch: ScenePatch) => void;

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
  // ADR-004 stage 4.1: the capability token rides in the query string
  // rather than a header - the browser WebSocket constructor takes a URL and
  // nothing else, so a header is not an option on this handshake. The
  // backend accepts either form (backend/auth.py's extract_presented_token).
  // A no-op when there is no token (vitest, and the vite-dev workflow).
  return withAuthToken(
    `${proto}//${window.location.host}/ws?session=${encodeURIComponent(sessionId)}`,
  );
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
  /** Keyed by requestId - stream deltas are addressed to a specific in-flight
   * request, not a topic. See handleMessage()'s "stream" branch. */
  private readonly streamListeners = new Map<string, Set<StreamListener>>();
  /** ADR-003 stage 3.4: keyed by topic, like stateListeners, but a separate
   * Map rather than a widened StateListener signature - mirroring how
   * streamListeners is already its own parallel registry. A topic can have
   * both (the scene topic does): its snapshot listener still receives every
   * `kind:"state"` frame unchanged, and only patch frames route here, so a
   * consumer that never opts into patches keeps working untouched. */
  private readonly patchListeners = new Map<string, Set<PatchListener>>();
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

  // Calling connect() always re-arms a disposed transport rather than
  // leaving it permanently inert. React 18 StrictMode's dev-only
  // mount->cleanup->mount cycle calls dispose() then connect() again on the
  // SAME memoized instance to check the component survives a remount; a
  // one-way disposed flag would leave the app stuck "closed" forever after
  // the very first render.
  connect(): void {
    if (this.socket) return;
    this.disposed = false;
    this.setStatus("connecting");
    const socket = this.factory(this.url);
    this.socket = socket;

    // Every handler checks it's still the current socket before touching
    // shared state - a superseded socket's belated close/message (e.g. the
    // one dispose() just closed, right before this same connect() call
    // re-armed and opened a fresh one) must not clobber the live connection.
    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.attempts = 0;
      this.setStatus("open");
      // ADR-003 stage 3.4 review-fix: patch topics are re-subscribed too.
      // This list used to come from stateListeners alone, so a consumer that
      // registered ONLY a patch listener - which subscribePatch's own doc
      // presents as a supported shape - was never subscribed server-side at
      // all and never re-subscribed on reconnect, silently receiving
      // nothing. The scene store happens to call both, but that made the
      // pairing a load-bearing invariant with nothing enforcing it.
      const topics = [...new Set([...this.stateListeners.keys(), ...this.patchListeners.keys()])];
      if (topics.length > 0) {
        socket.send(JSON.stringify({ kind: "subscribe", topics }));
      }
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      this.handleMessage(event.data);
    };
    socket.onerror = () => {
      // onclose always follows; reconnect logic lives there.
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.setStatus("closed");
      this.failAllPending(new WsUnavailableError("connection closed"));
      if (!this.disposed) this.scheduleReconnect();
    };
  }

  dispose(): void {
    this.disposed = true;
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.failAllPending(new WsUnavailableError("transport disposed"));
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

  /** ADR-003 stage 3.4: re-request a topic's current full snapshot.
   *
   * `subscribe()` above sends its subscribe message only for a topic's FIRST
   * listener (the isNewTopic guard), so an already-subscribed topic has no
   * way to ask for fresh state through it - this is that missing half, used
   * by the scene store to self-heal after a detected patch gap. Silently
   * no-ops while the socket is not open, matching intent()'s own
   * pre-connect behavior: reconnecting re-subscribes every topic from
   * scratch anyway, which resolves the gap by itself. */
  resubscribe(topic: string): void {
    if (this.status !== "open" || !this.socket) return;
    this.socket.send(JSON.stringify({ kind: "subscribe", topics: [topic] }));
  }

  /** ADR-003 stage 3.4: listen for a topic's `kind:"patch"` deltas. Sends no
   * subscribe message of its own - patches arrive on the SAME server-side
   * topic subscription `subscribe()` already established, so a consumer that
   * wants both calls both (the scene store does: snapshots are still the
   * reconnect/resync answer, patches are the steady-state path). */
  subscribePatch(topic: string, listener: PatchListener): () => void {
    let set = this.patchListeners.get(topic);
    if (!set) {
      set = new Set();
      this.patchListeners.set(topic, set);
    }
    set.add(listener);
    return () => {
      set.delete(listener);
      if (set.size === 0) this.patchListeners.delete(topic);
    };
  }

  /** Listen for one in-flight request's streaming deltas (`kind:"stream"`
   * frames), addressed by requestId rather than topic. No subscribe message
   * is sent to the server - the server broadcasts stream frames to every
   * connection unconditionally, and this only filters/fans them out
   * client-side. */
  subscribeStream(requestId: string, listener: StreamListener): () => void {
    let set = this.streamListeners.get(requestId);
    if (!set) {
      set = new Set();
      this.streamListeners.set(requestId, set);
    }
    set.add(listener);
    return () => {
      set.delete(listener);
      if (set.size === 0) this.streamListeners.delete(requestId);
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

  /** Intent with a reply (result or error), for request/response flows.
   * `timeoutMs` overrides the transport-wide default for this one call -
   * see fireIntent()'s own doc for why a genuinely user-paced backend
   * operation (a native OS file/folder picker, say) needs a much longer
   * window than the default 10s. */
  request(topic: string, intent: string, args: unknown[] = [], timeoutMs?: number): Promise<unknown> {
    if (this.status !== "open" || !this.socket) {
      return Promise.reject(new WsUnavailableError("not connected"));
    }
    const id = this.nextId++;
    const socket = this.socket;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new WsTimeoutError(`request timed out: ${topic}/${intent}`));
      }, timeoutMs ?? this.requestTimeout);
      this.pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ kind: "intent", topic, intent, args, id }));
    });
  }

  /** ADR-003 stage 3.1: the new default for a store's own "fire this
   * mutating intent" call sites - replaces the old bare `intent()` (which
   * silently dropped a real server-side rejection with nothing but a
   * console.error). Internally still id-tracked via request(), so a real
   * failure (a genuine {"kind":"error"} reply, or a client-side timeout -
   * anything except the three connection-level WsUnavailableError reasons,
   * see that class's own doc) is surfaced to the user through the existing
   * server-authoritative notification banner - reusing that channel rather
   * than introducing a second, parallel client-local notification UI (see
   * notifications.py's own module doc for why that split was rejected).
   *
   * `timeoutMs` overrides the default 10s window for this one call - pass
   * a much longer value for an intent whose backend handler genuinely waits
   * on the USER, not the network (a native OS file/folder picker, say);
   * otherwise an ordinary slow user pace looks identical to a hung backend
   * and gets reported as a spurious failure.
   *
   * Review-fix: a naive per-message de-dup (skip re-firing the IDENTICAL
   * topic/intent/message combination within a short window) guards against
   * a rapid-fire call site (e.g. a text field committing on every keystroke)
   * flooding the single-slot notification banner with the same message on
   * every failed attempt while a backend problem persists - see
   * lastShowError's own doc for what this does and, just as importantly,
   * does NOT fix (two DIFFERENT concurrent failures can still race each
   * other for the one visible banner; that deeper limitation is accepted,
   * documented residual risk, not something a per-message de-dup can close). */
  fireIntent(topic: string, intent: string, args: unknown[] = [], timeoutMs?: number): void {
    this.request(topic, intent, args, timeoutMs).catch((err) => {
      if (err instanceof WsUnavailableError) return;
      this.showErrorDeduped(String(err.message));
    });
  }

  /** ADR-003 stage 3.1 review-fix: the last showError message this transport
   * fired and when, so an identical message re-failing on every keystroke
   * of some rapid-fire call site doesn't re-publish the notification banner
   * on every single attempt. Keyed on the message text alone (not
   * topic/intent) - two DIFFERENT intents that happen to produce the exact
   * same backend error text within the window are rare enough, and
   * indistinguishable enough to the user reading the banner, that treating
   * them as "the same ongoing problem" is the more honest read anyway. */
  private lastShowError: { message: string; at: number } | null = null;
  private static readonly SHOW_ERROR_DEDUPE_MS = 3_000;

  private showErrorDeduped(message: string): void {
    const now = Date.now();
    const last = this.lastShowError;
    if (last && last.message === message && now - last.at < WsTransport.SHOW_ERROR_DEDUPE_MS) {
      return;
    }
    this.lastShowError = { message, at: now };
    this.intent("notification", "showError", [message]);
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
    if (kind === "patch") {
      // ADR-003 stage 3.4. A patch frame for a topic with no patch
      // subscriber is dropped SILENTLY rather than logged: the server sends
      // patches to every connection on the topic, and a consumer opting
      // into snapshots only (every topic except scene today) is a supported
      // configuration, not an anomaly - same posture as the stream branch
      // below, and deliberately unlike the unknown-kind fallback at the end.
      const listeners = this.patchListeners.get(message.topic as string);
      if (listeners) {
        const patch: ScenePatch = {
          revision: message.revision as number,
          baseRevision: message.baseRevision as number,
          ops: (message.ops as ScenePatchOp[]) ?? [],
        };
        for (const listener of [...listeners]) listener(patch);
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
        else entry.reject(new WsRequestError(String(message.error)));
      } else if (kind === "error") {
        console.error("[ws] server error:", message.error);
      }
      return;
    }
    if (kind === "stream") {
      // A stream frame with no matching requestId subscriber is silently
      // dropped - unlike a truly unrecognized kind below, this is an
      // expected, routine occurrence (e.g. a request already completed and
      // unsubscribed a moment before a straggling frame arrives).
      const requestId = message.requestId as string;
      const listeners = this.streamListeners.get(requestId);
      if (listeners) {
        for (const listener of [...listeners]) {
          listener(message.delta as string, Boolean(message.done), Boolean(message.reset), message.seq as number);
        }
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
