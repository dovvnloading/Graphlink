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
 *
 * ADR-003 stage 3.5: schema-version negotiation moved here from the dead
 * bridge-core/islandState.ts path (built, unit-tested, never wired to
 * anything live). Every `state`/`patch` frame is checked with the shared
 * checkSchemaCompatibility() algorithm before being dispatched to any
 * listener; an incompatible frame is withheld and the topic's rejection is
 * published through onVersionRejection() instead - see that method and
 * lib/ui/BridgeErrorState.tsx, the component this is finally wired to.
 *
 * ADR-003 stage 3.6: closes the last "vanishes silently" gap (D5) - an
 * intent fired while not open used to be dropped with nothing but a
 * console.error, for every one of the ~130 fireIntent call sites across
 * the app with no exceptions. fireIntent() now takes a `queueable`
 * opt-in: an idempotent, last-write-wins intent (a position, a view
 * setting, a text field - never a create/delete/send/run/approve) is held
 * in a bounded offline queue and replayed in order on the next reconnect;
 * everything else surfaces immediately via the notification banner
 * instead of disappearing. `ConnectionStatus` gained "reconnecting",
 * distinct from the first-ever "connecting", so the UI can say a session
 * is paused rather than looking identical to first load.
 */

import { withAuthToken } from "../auth/token";
import { checkSchemaCompatibility } from "../bridge-core/schemaVersion";
import type { BridgeRejection } from "../bridge-core/islandState";

/** ADR-003 stage 3.6: "reconnecting" is distinct from "connecting" - the
 * latter is the very first connection attempt on a fresh transport (nothing
 * to pause, nothing was ever working), the former is every attempt AFTER a
 * real connection has been lost (there is a session in progress, and the
 * app should visibly say so rather than looking identical to first load).
 * See connect()'s own status-selection logic. */
export type ConnectionStatus = "connecting" | "open" | "closed" | "reconnecting";

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
 * transport instance was disposed).
 *
 * ADR-003 stage 3.6 update: fireIntent() no longer swallows all three
 * unconditionally - only the disposed-transport case still does (nothing
 * left to recover into on real teardown). "Not connected"/"closed
 * mid-request" are now either queued (a `queueable` call - see that
 * method's own doc) or counted and surfaced as a summary banner on the
 * next reconnect (everything else) - "vanishes silently with no
 * exceptions" was the exact D5 gap this stage exists to close, and this
 * class's own three reasons were that gap's entire surface. */
export class WsUnavailableError extends Error {}

/** ADR-003 stage 3.5 review-fix: raised by request() (and so also by
 * fireIntent(), which wraps it) for a topic currently marked blocked via
 * setTopicBlocked() - today, exclusively the "scene" topic while its
 * incoming data has failed schema-version negotiation (see
 * onVersionRejection). Deliberately NOT a WsUnavailableError: the
 * connection is genuinely open, and fireIntent's catch surfaces everything
 * except WsUnavailableError by default specifically so a new rejection
 * reason is visible unless someone explicitly opts it into the swallowed
 * set - this is the case where that visibility matters most, since sending
 * a mutating intent against data the client has just declared it cannot
 * currently trust is exactly the scenario a 4-lens adversarial review of
 * this stage found completely unguarded outside the canvas viewport
 * itself (ViewPopover, Composer, PinOverlay, and the command palette all
 * kept firing real scene-topic intents while BridgeErrorState blocked only
 * the canvas). */
export class WsTopicBlockedError extends Error {}

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

/** ADR-003 stage 3.5: `rejection` is non-null while the last `kind:"state"`
 * or `kind:"patch"` frame for this topic failed schema-version negotiation,
 * and null once a compatible frame has been seen (including the very first
 * one, or none yet). Mirrors StatusListener's own "always fires immediately
 * with the current value on subscribe" contract - see onVersionRejection. */
export type VersionRejectionListener = (rejection: BridgeRejection | null) => void;

/** The one offline intent whose intermediate values are deliberately
 * replaceable. This is exported rather than inferred from `queueable`: most
 * queueable intents (node positions, settings, etc.) still preserve every
 * accepted write in order, while a text draft is explicitly last-write-wins. */
export const COMPOSER_DRAFT_OFFLINE_COALESCE_KEY = "app-composer/updateDraft";
export type OfflineIntentCoalesceKey = typeof COMPOSER_DRAFT_OFFLINE_COALESCE_KEY;
export type IntentSettledListener = () => void;
export type OfflineIntentCoalescedListener = () => void;

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
  /** Last compatible full snapshot for each still-observed topic. Several
   * dialogs subscribe lazily after an app-level store has already caused
   * the server's one subscribe snapshot to arrive; replaying it gives those
   * listeners the same current-state-on-subscribe contract as onStatus(). */
  private readonly stateSnapshots = new Map<string, Record<string, unknown>>();
  /** Deduplicates snapshot requests when multiple listeners mount before
   * the first response. A patch invalidates stateSnapshots, after which a
   * late state listener uses this set to request one fresh full snapshot. */
  private readonly snapshotRequestsPending = new Set<string>();
  /** Correlated one-shot callbacks for explicit resubscribe() requests.
   * Topic-only "next state" matching is insufficient because a buffered
   * broadcast can arrive after an intent result but before the requested
   * snapshot; the echoed request id identifies the actual authority fence. */
  private readonly resubscribeListeners = new Map<
    number,
    { topic: string; listener: StateListener }
  >();
  private readonly statusListeners = new Set<StatusListener>();
  /** Keyed by requestId - stream deltas are addressed to a specific in-flight
   * request, not a topic. See handleMessage()'s "stream" branch. */
  private readonly streamListeners = new Map<string, Set<StreamListener>>();
  /** ADR-011 review-fix: the full text accumulated so far for a still-in-
   * flight request, keyed by requestId like streamListeners - but a
   * deliberately SEPARATE map that does NOT get cleared when a requestId's
   * listener set goes empty (streamListeners itself deletes its entry the
   * moment that happens - see subscribeStream()'s unsubscribe closure).
   * ADR-011's onlyRenderVisibleElements virtualization (SceneCanvas.tsx) can
   * genuinely unmount a live-streaming node's component (ChatNodeView/
   * ConversationNodeView/CodeSandboxNodeView) when it's panned off-screen,
   * then mount a BRAND NEW instance when it's panned back - and this class
   * has no server-side subscribe concept to replay from ("the server
   * broadcasts stream frames to every connection unconditionally", see this
   * method's own doc below), so without a client-side buffer every delta
   * broadcast during the unmounted window is lost forever. Updated on every
   * inbound stream frame regardless of whether anyone is currently
   * subscribed (handleMessage's "stream" branch), and replayed to a
   * newly-subscribing listener as a synthetic reset frame (subscribeStream
   * below) - the same "deliver current state on subscribe" contract
   * onStatus()/onVersionRejection() already establish elsewhere in this
   * file. Cleared once the request completes (`done`): after that point the
   * persisted `content` field is the source of truth for a (re)mounted
   * component, so nothing is left to replay. */
  private readonly streamBuffers = new Map<string, { text: string; seq: number }>();
  /** ADR-003 stage 3.4: keyed by topic, like stateListeners, but a separate
   * Map rather than a widened StateListener signature - mirroring how
   * streamListeners is already its own parallel registry. A topic can have
   * both (the scene topic does): its snapshot listener still receives every
   * `kind:"state"` frame unchanged, and only patch frames route here, so a
   * consumer that never opts into patches keeps working untouched. */
  private readonly patchListeners = new Map<string, Set<PatchListener>>();
  /** ADR-003 stage 3.5: keyed by topic, like stateListeners/patchListeners.
   * A topic absent from this map has never seen an incompatible frame -
   * getVersionRejection()/onVersionRejection() treat that the same as an
   * explicit null (compatible). */
  private readonly versionRejections = new Map<string, BridgeRejection | null>();
  private readonly versionRejectionListeners = new Map<string, Set<VersionRejectionListener>>();
  /** ADR-003 stage 3.5 review-fix: topics for which intent()/request() (and
   * so fireIntent(), which wraps request()) currently refuse to send - see
   * setTopicBlocked()'s own doc. Generic per-topic gate, not scene-specific
   * logic baked into the transport: any caller can block/unblock any topic
   * for any reason. */
  private readonly blockedTopics = new Set<string>();
  private readonly pending = new Map<
    number,
    { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> }
  >();
  private nextId = 1;
  /** ADR-003 stage 3.6: true once this transport has EVER seen a real
   * `onopen` - what distinguishes "connecting" (first attempt, nothing to
   * pause) from "reconnecting" (a session was in progress) in connect()'s
   * status selection below. */
  private hasEverConnected = false;
  /** ADR-003 stage 3.6: intents fired with `queueable: true` while not
   * open are held here instead of being dropped, and replayed in order on
   * the next successful `onopen` - see fireIntent()'s own doc for exactly
   * which intents this applies to (a per-call opt-in, not a topic-wide
   * default) and flushOfflineQueue() for the replay. Bounded per the ADR's
   * own "~50" - a queue exists to survive a brief drop during a real user
   * action, not to become an unbounded backlog of everything fired while
   * offline. */
  private readonly offlineQueue: Array<{
    topic: string;
    intent: string;
    args: unknown[];
    timeoutMs?: number;
    coalesceKey?: OfflineIntentCoalesceKey;
    onSettled?: IntentSettledListener;
    onOfflineCoalesced?: OfflineIntentCoalescedListener;
  }> = [];
  private static readonly OFFLINE_QUEUE_MAX = 50;

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
    // ADR-003 stage 3.6: "reconnecting", not "connecting", once a real
    // session has existed before - see hasEverConnected's own doc.
    this.setStatus(this.hasEverConnected ? "reconnecting" : "connecting");
    const socket = this.factory(this.url);
    this.socket = socket;

    // Every handler checks it's still the current socket before touching
    // shared state - a superseded socket's belated close/message (e.g. the
    // one dispose() just closed, right before this same connect() call
    // re-armed and opened a fresh one) must not clobber the live connection.
    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.attempts = 0;
      this.hasEverConnected = true;
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
        for (const topic of topics) this.snapshotRequestsPending.add(topic);
      }
      // ADR-003 stage 3.6: AFTER re-subscribing, so a replayed intent
      // (e.g. moveNodes) applies against fresh server state rather than
      // racing the subscribe message.
      this.flushOfflineQueue();
      // A connection can close after an explicit snapshot request is sent
      // but before its correlated response arrives. Reissue those fences
      // after ordinary subscriptions and queued writes on the new socket.
      for (const [id, request] of this.resubscribeListeners) {
        socket.send(JSON.stringify({ kind: "subscribe", topics: [request.topic], id }));
        this.snapshotRequestsPending.add(request.topic);
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
      this.snapshotRequestsPending.clear();
      // ADR-003 stage 3.6 review-fix: the paused state has to cover the WHOLE
      // outage, not just an in-flight connect attempt. This used to publish
      // "closed" here and leave connect() - which only runs after a 500ms-4s
      // backoff sleep - as the sole publisher of "reconnecting", so the badge
      // read the bare word "closed" for essentially all the wall-clock time
      // that intents were being queued and counted, flickering "reconnecting"
      // for the few ms of each attempt against a refused port. That left this
      // stage's own "visible paused state" exit criterion undelivered in
      // practice. "closed" now means what it says: nothing is coming back.
      if (this.disposed) {
        this.setStatus("closed");
      } else {
        this.setStatus(this.hasEverConnected ? "reconnecting" : "connecting");
      }
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
    this.snapshotRequestsPending.clear();
    this.resubscribeListeners.clear();
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  /** Listen for a topic's snapshots. A compatible full snapshot already
   * received for another listener is replayed synchronously; otherwise an
   * open socket requests one (deduplicated while that request is pending). */
  subscribe(topic: string, listener: StateListener): () => void {
    let set = this.stateListeners.get(topic);
    if (!set) {
      set = new Set();
      this.stateListeners.set(topic, set);
    }
    set.add(listener);
    const snapshot = this.stateSnapshots.get(topic);
    if (snapshot) {
      listener(snapshot);
    } else if (
      this.status === "open" &&
      this.socket &&
      !this.snapshotRequestsPending.has(topic)
    ) {
      this.socket.send(JSON.stringify({ kind: "subscribe", topics: [topic] }));
      this.snapshotRequestsPending.add(topic);
    }
    return () => {
      set.delete(listener);
      if (set.size === 0) this.stateListeners.delete(topic);
      this.maybeForgetTopic(topic);
    };
  }

  /** ADR-003 stage 3.4: re-request a topic's current full snapshot.
   *
   * `subscribe()` above replays a valid cached snapshot when one exists, so
   * an already-subscribed consumer that knows its state is stale needs an
   * explicit way to bypass that cache - this is that path, used by the scene
   * store to self-heal after a detected patch gap. Silently no-ops while the
   * socket is not open, matching intent()'s own pre-connect behavior:
   * reconnecting re-subscribes every topic from scratch anyway, which
   * resolves the gap by itself. */
  resubscribe(topic: string, onSnapshot?: StateListener): boolean {
    if (this.status !== "open" || !this.socket) return false;
    let id: number | undefined;
    if (onSnapshot) {
      id = this.nextId++;
      this.resubscribeListeners.set(id, { topic, listener: onSnapshot });
    }
    this.socket.send(JSON.stringify({
      kind: "subscribe",
      topics: [topic],
      ...(id === undefined ? {} : { id }),
    }));
    this.snapshotRequestsPending.add(topic);
    return true;
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
      this.maybeForgetTopic(topic);
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
    // ADR-011 review-fix: replay whatever has accumulated so far as a
    // synthetic reset frame - the exact case this exists for is a
    // component that unmounted (virtualization scrolled it off-screen) and
    // has just remounted while the SAME request is still streaming; without
    // this, every delta broadcast during the unmounted window (see
    // streamBuffers' own doc above) is silently lost. A brand-new
    // subscriber for a request nothing has streamed for yet finds no
    // buffered entry and gets no replay, identical to today's behavior.
    const buffered = this.streamBuffers.get(requestId);
    if (buffered && buffered.text) {
      listener(buffered.text, false, true, buffered.seq);
    }
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

  /** ADR-003 stage 3.5: listen for a topic's schema-version compatibility.
   * Fires immediately with the topic's CURRENT rejection state (null if none
   * yet), then again on every change - same "deliver current state on
   * subscribe" contract as onStatus above, so a component mounting after the
   * first frame already arrived does not have to wait for a second one to
   * find out it was incompatible. See handleMessage()'s state/patch branches
   * for where this is actually decided. */
  onVersionRejection(topic: string, listener: VersionRejectionListener): () => void {
    let set = this.versionRejectionListeners.get(topic);
    if (!set) {
      set = new Set();
      this.versionRejectionListeners.set(topic, set);
    }
    set.add(listener);
    listener(this.versionRejections.get(topic) ?? null);
    return () => {
      set.delete(listener);
      if (set.size === 0) this.versionRejectionListeners.delete(topic);
      this.maybeForgetTopic(topic);
    };
  }

  /** Drops a topic's per-topic caches once nothing is listening to it via
   * ANY of the three
   * per-topic registries - state, patch, or rejection. Keyed off all three
   * (not just versionRejectionListeners alone) deliberately: a topic can
   * still have live state/patch listeners with no rejection listener
   * attached, and forgetting the cache in that case would make the NEXT
   * onVersionRejection subscriber see a false "compatible" reading instead
   * of the topic's real last-known status until another frame arrives.
   * Without this, a short-lived subscriber that unsubscribes from a
   * rejected topic and later re-subscribes would see the transport replay
   * the stale prior rejection immediately, rather than starting clean the
   * way a never-before-seen topic does - harmless today (the app's one
   * real subscriber, SceneStore, never unsubscribes for its own lifetime)
   * but an asymmetry with this file's own established per-topic cleanup
   * convention every other registry already follows. */
  private maybeForgetTopic(topic: string): void {
    if (this.stateListeners.has(topic)) return;
    if (this.patchListeners.has(topic)) return;
    if (this.versionRejectionListeners.has(topic)) return;
    this.versionRejections.delete(topic);
    this.stateSnapshots.delete(topic);
    this.snapshotRequestsPending.delete(topic);
    for (const [id, request] of this.resubscribeListeners) {
      if (request.topic === topic) this.resubscribeListeners.delete(id);
    }
  }

  /** ADR-003 stage 3.5 review-fix: blocks intent()/request() (and so
   * fireIntent()) for `topic`, so a mutating call site cannot send against
   * data this client currently cannot trust - e.g. the scene topic during
   * a version-rejection episode (see onVersionRejection). Closes a real
   * gap a 4-lens adversarial review found: only the canvas viewport was
   * gated on scene version-rejection, while every sibling chrome surface
   * (Composer, ViewPopover, PinOverlay, the command palette) kept firing
   * real scene-topic intents completely unguarded, directly contradicting
   * BridgeErrorState's own stated rationale for existing at all.
   *
   * A single choke point here - not 84 individual call-site edits in
   * sceneStore.ts - because every one of those call sites already funnels
   * through intent()/request(); gating here covers all of them, present
   * and future, without touching any of them. */
  setTopicBlocked(topic: string, blocked: boolean): void {
    if (blocked) this.blockedTopics.add(topic);
    else this.blockedTopics.delete(topic);
  }

  /** Fire-and-forget intent (the @Slot successor). Silently dropped when the
   * socket is not open - matching the old bridge's pre-connect no-op call()
   * semantics that every island already codes against. Also silently
   * dropped for a blocked topic (see setTopicBlocked), same posture: this
   * method's whole contract is "no reply, nothing to surface." */
  intent(topic: string, intent: string, args: unknown[] = []): void {
    if (this.blockedTopics.has(topic)) return;
    if (this.status !== "open" || !this.socket) return;
    this.socket.send(JSON.stringify({ kind: "intent", topic, intent, args }));
  }

  /** Intent with a reply (result or error), for request/response flows.
   * `timeoutMs` overrides the transport-wide default for this one call -
   * see fireIntent()'s own doc for why a genuinely user-paced backend
   * operation (a native OS file/folder picker, say) needs a much longer
   * window than the default 10s. */
  request(topic: string, intent: string, args: unknown[] = [], timeoutMs?: number): Promise<unknown> {
    if (this.blockedTopics.has(topic)) {
      return Promise.reject(
        new WsTopicBlockedError(
          "Can't send changes - the desktop app and this interface currently disagree about the data format.",
        ),
      );
    }
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
   * documented residual risk, not something a per-message de-dup can close).
   *
   * ADR-003 stage 3.6: `queueable` is the new per-call opt-in that decides
   * what happens while the transport is NOT open - it does nothing while
   * open, where this call behaves exactly as before. Pass `true` ONLY for
   * an intent whose effect is idempotent last-write-wins and has no side
   * effect beyond updating already-existing data (a position, a view
   * setting, a text field). Idempotence is a HARD requirement, not a
   * nicety: the WsUnavailableError branch below re-queues an intent that
   * was genuinely in flight when the socket died, and in that window the
   * server may already have applied it with only the reply lost - so a
   * replay is a SECOND application. That rules out every `toggle*` intent,
   * whose backend handler flips (`x = not x`) rather than setting a value;
   * replaying one silently reverts the user's action. See
   * queueableClassificationGuard.test.ts, the ratchet on exactly that
   * mistake - see also the classification this stage did across
   * every real call site in the app (sceneStore.ts/composerStore.ts/
   * SettingsDialog.tsx/ChatLibraryDialog.tsx) for the actual list and the
   * reasoning behind each one. The default (`false`) is the SAFE choice:
   * an intent that creates, deletes, sends, runs, or approves something
   * must never be silently replayed later against scene state the user
   * has not seen and may have since changed their mind about - instead it
   * is COUNTED and surfaced as one summary banner the moment the
   * connection is next able to actually deliver it (see
   * droppedWhileOffline's own doc for why "surface it immediately" does
   * not work here). Previously this was the ONE case fireIntent silently
   * swallowed with no exceptions - "vanish silently" is the exact D5
   * finding this stage exists to close. The disposed-transport case is the
   * only one that still keeps the older silent posture (see below). A
   * BLOCKED topic is refused rather than queued either way, but HOW that
   * surfaces depends on the socket: while open, through request()'s own
   * WsTopicBlockedError banner; while not open - where no banner can be
   * delivered at all - by being counted into the reconnect summary like any
   * other loss. */
  fireIntent(
    topic: string,
    intent: string,
    args: unknown[] = [],
    timeoutMs?: number,
    queueable = false,
    offlineCoalesceKey?: OfflineIntentCoalesceKey,
    onSettled?: IntentSettledListener,
    onOfflineCoalesced?: OfflineIntentCoalescedListener,
  ): void {
    // ADR-003 stage 3.6 x stage 3.5 interaction: a blocked topic must NEVER
    // enter the offline queue. Blocking exists precisely because this client
    // cannot trust the state these args were computed against, so holding
    // them would defer exactly that untrusted send to whenever the block
    // lifts - and would spend the bounded queue's finite slots on intents
    // certain to be refused anyway, displacing recoverable ones from other
    // topics.
    //
    // Review-fix: it must still be COUNTED, though. An earlier version of
    // this guard let blocked-and-offline fall through to request(), which
    // rejects with WsTopicBlockedError - not a WsUnavailableError, so the
    // catch below routed it to a banner that intent() then silently dropped
    // for want of an open socket. Not queued, not counted, no banner ever:
    // the exact D5 "vanishes silently" bug this stage exists to close,
    // reintroduced in the one combination the guard itself created. Blocked
    // topics are refused, and being refused is reported like any other loss.
    if (this.status !== "open") {
      if (queueable && !this.blockedTopics.has(topic)) {
        this.enqueueOffline(
          topic,
          intent,
          args,
          timeoutMs,
          offlineCoalesceKey,
          onSettled,
          onOfflineCoalesced,
        );
      } else {
        this.droppedWhileOffline += 1;
        onSettled?.();
      }
      return;
    }
    this.request(topic, intent, args, timeoutMs).then(
      () => onSettled?.(),
      (err) => {
        // A disposed transport is real teardown (unmount, or StrictMode's
        // dev-only dispose-then-remount check) - there is nothing left to
        // recover into.
        if (this.disposed) {
          onSettled?.();
          return;
        }
        if (err instanceof WsUnavailableError) {
          // Was genuinely in flight (status was "open" when sent) and got cut
          // off mid-request rather than refused up front - same fate as the
          // not-open case above either way: recoverable data is queued,
          // everything else is counted for the next reconnect's summary.
          if (queueable) {
            this.enqueueOffline(
              topic,
              intent,
              args,
              timeoutMs,
              offlineCoalesceKey,
              onSettled,
              onOfflineCoalesced,
            );
          } else {
            this.droppedWhileOffline += 1;
            onSettled?.();
          }
          return;
        }
        this.showErrorDeduped(String(err.message));
        onSettled?.();
      },
    );
  }

  /** ADR-003 stage 3.6: how many non-queueable intents were refused while
   * not open, since the last time this was reported. NOT surfaced
   * immediately as each one happens: the notification banner itself is
   * server-authoritative (notifications.py's own module doc - reused
   * deliberately rather than building a second, client-local notification
   * UI), which means showErrorDeduped()'s own `intent()` call needs a live
   * connection to deliver anything at all. Trying to show a "not
   * connected" banner AT THE MOMENT the connection is unavailable cannot
   * work - it would just silently no-op through the exact same
   * not-open check, reproducing the bug this stage exists to fix one
   * layer down. Reported as a single summary count instead, the moment
   * the connection is next actually able to deliver it - see onopen. */
  private droppedWhileOffline = 0;

  /** ADR-003 stage 3.6: holds one queueable intent for replay on the next
   * successful reconnect - see fireIntent()'s own doc for which intents
   * this applies to, and flushOfflineQueue() for the replay. Bounded: a
   * queue that grows without limit while offline is itself a form of
   * silent-loss risk deferred rather than removed (nothing guarantees the
   * user will ever see all 500 of them applied at once on reconnect) - so
   * past OFFLINE_QUEUE_MAX this counts the new intent as dropped (see
   * droppedWhileOffline) rather than silently evicting an older,
   * already-accepted one: evicting silently would just move the
   * "vanishes with no signal" failure from the un-queued case onto the
   * queued one.
   *
   * The composer draft is the narrow exception: its call site supplies the
   * explicit coalesce key above, so a newer queued value replaces the older
   * value in place. One reserved entry lets that key survive even when all
   * 50 ordinary slots are occupied; this keeps the queue bounded at 51 while
   * never evicting an already-accepted non-draft operation. */
  private enqueueOffline(
    topic: string,
    intent: string,
    args: unknown[],
    timeoutMs?: number,
    offlineCoalesceKey?: OfflineIntentCoalesceKey,
    onSettled?: IntentSettledListener,
    onOfflineCoalesced?: OfflineIntentCoalescedListener,
  ): void {
    // Runtime-check the key's target as well as typing it narrowly. A future
    // JavaScript caller must not gain replacement/reserved-slot semantics for
    // an unrelated operation merely by copying this string.
    const coalesceKey =
      offlineCoalesceKey === COMPOSER_DRAFT_OFFLINE_COALESCE_KEY &&
      topic === "app-composer" &&
      intent === "updateDraft"
        ? offlineCoalesceKey
        : undefined;
    const item = { topic, intent, args, timeoutMs, coalesceKey, onSettled, onOfflineCoalesced };
    if (coalesceKey) {
      const existing = this.offlineQueue.findIndex((queued) => queued.coalesceKey === coalesceKey);
      if (existing >= 0) {
        // Replace in place: latest draft wins without perturbing the replay
        // order of any independent queueable intents around it.
        this.offlineQueue[existing].onOfflineCoalesced?.();
        this.offlineQueue[existing] = item;
        return;
      }
      // This key owns one dedicated slot beyond the 50 ordinary entries, so
      // a draft first edited after saturation is still recoverable.
      this.offlineQueue.push(item);
      return;
    }
    const ordinaryCount = this.offlineQueue.reduce(
      (count, queued) => count + (queued.coalesceKey ? 0 : 1),
      0,
    );
    if (ordinaryCount >= WsTransport.OFFLINE_QUEUE_MAX) {
      this.droppedWhileOffline += 1;
      onSettled?.();
      return;
    }
    this.offlineQueue.push(item);
  }

  /** ADR-003 stage 3.6: replays every queued intent, in the order they were
   * fired, through fireIntent() itself (still `queueable: true` - if the
   * connection drops again mid-flush, an item just re-queues via the exact
   * same path rather than needing special-cased retry logic here). Drains
   * the queue FIRST (rather than iterating it in place) so a re-entrant
   * enqueue during replay - a queued intent whose OWN failure handling
   * queues it again - can never see or grow the list this loop is
   * currently walking.
   *
   * Reports droppedWhileOffline (if any) AFTER the replay, as one summary
   * banner - by this point `status` is genuinely "open" (the caller,
   * onopen, only calls this after setStatus("open")), which is the
   * earliest moment showErrorDeduped's own send can actually succeed. */
  private flushOfflineQueue(): void {
    if (this.offlineQueue.length > 0) {
      const queued = this.offlineQueue.splice(0, this.offlineQueue.length);
      for (const item of queued) {
        this.fireIntent(
          item.topic,
          item.intent,
          item.args,
          item.timeoutMs,
          true,
          item.coalesceKey,
          item.onSettled,
          item.onOfflineCoalesced,
        );
      }
    }
    if (this.droppedWhileOffline > 0) {
      const n = this.droppedWhileOffline;
      this.droppedWhileOffline = 0;
      // Deliberately NOT showErrorDeduped. That de-dup exists to stop one
      // rapid-fire call site re-publishing the SAME ongoing failure on every
      // keystroke; this message is different in kind - it is a COUNT of
      // distinct losses, and it can only fire once per reconnect, so it is
      // already inherently rate-limited. Routing it through the de-dup would
      // mean two outages inside the 3s window that each lost one change
      // report "1 change..." once and swallow the second - under-reporting
      // real data loss, which is precisely what this stage exists to stop.
      this.intent(
        "notification",
        "showError",
        [
          n === 1
            ? "1 change could not be sent while disconnected."
            : `${n} changes could not be sent while disconnected.`,
        ],
      );
    }
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
      const topic = message.topic as string;
      const payload = message.payload as Record<string, unknown>;
      this.snapshotRequestsPending.delete(topic);
      // ADR-003 stage 3.5: the version envelope lives INSIDE payload for a
      // state frame (see _Topic._stamp on the backend) - checked and, on
      // rejection, dispatched to NOTHING below. A rejected payload is stale
      // by definition (BridgeErrorState.tsx's own doc), so silently handing
      // it to a listener would be strictly worse than the pre-3.5 behavior
      // this stage exists to replace: at least the old frozen-UI failure
      // mode didn't also risk running shape validation against fields a
      // breaking version change may have renamed or removed.
      if (!this.checkVersionAndMaybeReject(topic, payload)) {
        this.stateSnapshots.delete(topic);
        return;
      }
      this.stateSnapshots.set(topic, payload);
      const snapshotRequestId = message.id;
      if (typeof snapshotRequestId === "number") {
        const resubscribeRequest = this.resubscribeListeners.get(snapshotRequestId);
        if (resubscribeRequest?.topic === topic) {
          this.resubscribeListeners.delete(snapshotRequestId);
          resubscribeRequest.listener(payload);
        }
      }
      const listeners = this.stateListeners.get(topic);
      if (listeners) {
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
      const topic = message.topic as string;
      // A generic transport cannot safely apply arbitrary topic patches to
      // a cached payload. Drop the now-stale full snapshot; a state listener
      // that mounts later will request a fresh one, while this patch keeps
      // routing only through subscribePatch as before.
      this.stateSnapshots.delete(topic);
      // ADR-003 stage 3.5: unlike a state frame, the version envelope is at
      // the TOP LEVEL of a patch frame, a sibling of ops/revision (see
      // _publish_now's broadcast dict on the backend) - `message` itself is
      // what gets checked, not a sub-object.
      if (!this.checkVersionAndMaybeReject(topic, message)) return;
      const listeners = this.patchListeners.get(topic);
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
      // A stream frame with no matching requestId subscriber has nothing to
      // dispatch to right now - unlike a truly unrecognized kind below, this
      // is an expected, routine occurrence (e.g. a request already completed
      // and unsubscribed a moment before a straggling frame arrives, or a
      // node currently scrolled out of the virtualized viewport - see
      // streamBuffers' own doc). It is still buffered below so a listener
      // that (re)subscribes later can catch up.
      const requestId = message.requestId as string;
      const delta = message.delta as string;
      const done = Boolean(message.done);
      const reset = Boolean(message.reset);
      const seq = message.seq as number;
      if (done) {
        // The request is finished - `content` (persisted server-side) is
        // the source of truth for any component that mounts from here on,
        // so nothing is left to replay.
        this.streamBuffers.delete(requestId);
      } else {
        const existing = this.streamBuffers.get(requestId);
        const text = reset || !existing ? delta : existing.text + delta;
        this.streamBuffers.set(requestId, { text, seq });
      }
      const listeners = this.streamListeners.get(requestId);
      if (listeners) {
        for (const listener of [...listeners]) {
          listener(delta, done, reset, seq);
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

  /** ADR-003 stage 3.5: runs the shared checkSchemaCompatibility() algorithm
   * (bridge-core/schemaVersion.ts - the same one BridgeErrorState's now-live
   * caller uses) against an incoming frame, updates the topic's rejection
   * state, and returns whether the caller should proceed to dispatch. `on`
   * is either the state frame's `payload` or the patch frame's `message`
   * itself - see the two call sites for which. */
  private checkVersionAndMaybeReject(topic: string, on: unknown): boolean {
    const verdict = checkSchemaCompatibility(on);
    if (!verdict.compatible) {
      this.setVersionRejection(topic, { kind: "version", reason: verdict.reason, details: [] });
      return false;
    }
    this.setVersionRejection(topic, null);
    return true;
  }

  private setVersionRejection(topic: string, rejection: BridgeRejection | null): void {
    const current = this.versionRejections.get(topic) ?? null;
    // Dedup by reason text, not reference: two independently-constructed
    // rejections for the same ongoing skew must not re-fire listeners on
    // every single subsequent frame while it persists.
    if (current === rejection) return;
    if (current !== null && rejection !== null && current.reason === rejection.reason) return;
    this.versionRejections.set(topic, rejection);
    const listeners = this.versionRejectionListeners.get(topic);
    if (listeners) {
      for (const listener of [...listeners]) listener(rejection);
    }
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
