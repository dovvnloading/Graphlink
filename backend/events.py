"""WebSocket event bus: session-scoped topics carrying versioned full-state
snapshots plus named intents (Qt-removal plan R0).

Wire semantics are inherited from the IslandBridge/QWebChannel layer this
replaces, because they were already the right shape and the frontend's
generated validators depend on them:

- Server -> client: versioned state. Most topics send full-state snapshots
  only (`kind: "state"`); the scene topic additionally emits `kind: "patch"`
  node-scoped deltas when a smaller delta is available (ADR-003 stage 3.4),
  with `baseRevision` letting a client detect a gap and fall back to a fresh
  snapshot. Every message - snapshot or patch - carries schemaVersion /
  minCompatibleSchemaVersion / revision, stamped here exactly as
  IslandBridge.publish() stamped them (a reader may accept a NEWER payload
  than it understands - additive-only guarantee - but must refuse one
  older than its stated minimum; ADR-003 stage 3.5 is what actually wires a
  client-side reader to enforce that refusal instead of leaving the fields
  decorative).
- Client -> server: named intents ("setGridSize", "ready", ...) addressed to
  a topic, with positional JSON args - the successor of @Slot methods.

Scoping: one SessionBus per session id. Topics, revisions, and connections
are session-local, so two windows on different sessions never see each
other's state. The bus is transport-agnostic: connections are anything with
an async send_json(dict) - real WebSockets in app.py, plain recorders in
tests.

ADR-004 stage 4.3: two additions closing audit finding C6 (unbounded
memory/task growth - any local caller could previously mint a permanent,
never-evicted SessionBus for any `?session=<anything>` string).

1. Session issuance can be restricted: EventBus.session() only creates a
   new session for an id in __init__'s own allowed_session_ids, when one
   was supplied - anything else raises UnknownSessionError, UNLESS a
   session by that id already exists (a real reconnect always works
   regardless of the restriction). Unrestricted (the default, None) is
   unchanged from before this stage - EventBus is a genuinely reusable,
   domain-agnostic multi-session primitive (see its own tests), and the
   restriction is an APPLICATION policy, not a property of the class
   itself. backend/app.py's create_app is the one real caller that
   restricts to just DEFAULT_SESSION_ID ("default", the one every real
   window uses - confirmed by grep, web_ui never requests any other id) -
   this app has no multi-window feature and no mechanism that issues
   additional ids today, so in practice this means the shipped app's own
   "default" is the only session that will ever exist there - a
   deliberately narrow, YAGNI-respecting reading of "must be one the
   backend issued... or the default window session" rather than building a
   speculative issuance API nothing calls.

2. Idle eviction: a session with zero connections for
   session_idle_ttl_seconds gets torn down by the injected
   evict_idle_session callback (see EventBus.__init__'s own docstring for
   why teardown is injected rather than implemented here - this module is
   deliberately domain-agnostic, per the note above). This is genuinely
   useful even with issuance now locked to "default" only: it closes the
   OTHER standing gap ADR-004 names, "the autosave task never cancelled"
   (backend/autosave.py's own docstring used to document this as a
   deliberately-accepted characteristic of a single-window desktop app -
   see that module's own updated docstring) - without a TTL, a session
   that goes idle (window closed without a clean shutdown, or a future
   multi-window disconnect) keeps a live 30s-interval background task
   forever, each tick holding the whole SceneDocument alive via closure
   regardless of whether anything can ever reach it again.

   The TTL is deliberately generous (minutes, not seconds): the frontend's
   own WsTransport reconnects with backoff after a transient network blip,
   and naive eviction on the FIRST disconnect would tear down (and lose
   any not-yet-autosaved edits in) a session that was about to reconnect a
   moment later. sweep_idle_sessions() is exposed as a public, directly
   callable method (not just the free-running background loop) for the
   exact reason backend/autosave.py's own bus.autosave_guarded_tick is:
   tests need to exercise the real decision logic deterministically,
   without waiting on or mocking wall-clock sleeps.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import logging
import time
from typing import Any, Awaitable, Callable, Protocol

from graphlink_wire_schema import validate_payload

logger = logging.getLogger(__name__)

StateBuilder = Callable[[], dict[str, Any]]
# ADR-003 stage 3.4: a topic's optional delta source. Returns the ops
# accumulated since its own last call, or None meaning "no dirty state
# recorded - send a full snapshot instead". See SessionBus.publish()'s own
# docstring for why None must never be conflated with an empty ops list,
# and backend/domain/graph.py's take_dirty_patch_ops for the one real
# implementation.
PatchBuilder = Callable[[], list[dict[str, Any]] | None]
# ADR-003 stage 3.4 review-fix: a topic's "what did I last publish" source.
# send_snapshot serves THIS rather than live state, so a subscribing
# connection lands exactly where every existing one already is - see
# SessionBus.send_snapshot and SceneDocument.published_scene_payload.
BaselineBuilder = Callable[[], dict[str, Any] | None]
IntentHandler = Callable[..., Any | Awaitable[Any]]

# The one session id every real window uses (confirmed by grep:
# web_ui/src/app/App.tsx's only production call to defaultWsUrl() passes no
# argument, so it always resolves to this). See the module docstring's
# ADR-004 stage 4.3 section for why this is the ONLY id EventBus.session()
# will ever create.
DEFAULT_SESSION_ID = "default"

# Generous on purpose - see the module docstring's own reasoning: must
# comfortably outlast a transient WS reconnect gap (network blip, laptop
# sleep), or eviction would tear down a session the frontend was about to
# reconnect to anyway.
DEFAULT_SESSION_IDLE_TTL_SECONDS = 300.0
DEFAULT_SWEEP_INTERVAL_SECONDS = 60.0

# ADR-003 stage 3.4 follow-on: the ADR's own "~16 ms window" for batching a
# burst of publishes into one outbound message. Applied only where a bus is
# constructed with it (backend/app.py's real one) - see SessionBus.__init__
# for why the class default is 0 instead.
DEFAULT_COALESCE_WINDOW_SECONDS = 0.016

# Bound on one buffered connection's outbound queue. Sized for "a brief
# stall, not a persistent one": at the scene topic's post-3.4 patch sizes
# (~3 KiB for a single-node edit) this is a few hundred KiB of worst-case
# per-connection buffering, and a client that falls further behind than 64
# unread messages is better served by dropping to a re-snapshot than by the
# server hoarding an ever-longer history it will have to send anyway.
DEFAULT_SEND_QUEUE_MAXSIZE = 64


class UnknownSessionError(KeyError):
    """A caller asked for a session id EventBus never issued and isn't
    DEFAULT_SESSION_ID - ADR-004 stage 4.3's "unknown ids are rejected, not
    auto-created"."""


class Connection(Protocol):
    async def send_json(self, data: dict[str, Any]) -> None: ...


class UnknownTopicError(KeyError):
    """An intent or publish referenced a topic nothing registered."""


class UnknownIntentError(KeyError):
    """A client sent an intent name the topic does not expose."""


class IntentValidationError(Exception):
    """ADR-003 stage 3.2: an intent's args failed schema validation - raised
    BEFORE the handler runs (dispatch_intent, below), so a malformed request
    can never reach - and partially mutate state through - a handler body.
    See register_intent's own docstring for how a handler opts into this."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _validate_intent_args(args_schema: type, args: Any) -> list[str]:
    """Adapt validate_payload (dict-shaped, for wire STATE payloads) to the
    positional-list shape intent args actually arrive in - args_schema's
    dataclass field order defines the positional mapping. zip() alone would
    silently drop any args beyond the field count, so the arity ceiling is
    checked explicitly first; a too-few-args shortfall is left to
    validate_payload's own existing "missing required field" check (a
    dataclass field with a default - typically `X | None = None` - is
    correctly optional there already, with no new logic needed).

    Review-fix: `args` is typed `list[Any]` by dispatch_intent's own
    signature, but that is a Python-side type hint, not a runtime guarantee -
    _handle_message (backend/app.py) builds it straight from
    `message.get("args", [])` with no shape check, so a client sending a
    JSON OBJECT for "args" reached here unchanged. zip(fields, a_dict) pairs
    each field with the dict's KEY strings (never its values), so validation
    could report zero errors while payload held only field-name echoes - and
    the caller then did `handler(*args)` with that same dict, which unpacks
    its KEYS as positional args, silently calling the handler with the wrong
    values entirely rather than the rejection this whole mechanism exists to
    guarantee. Rejecting outright here closes that gap for every schema at
    once, not just this one call site.

    Review-fix: validate_payload's per-field errors carry a JSON-Schema-style
    "$." path prefix (e.g. "$.message: missing required field") - meaningful
    for a wire-payload schema, but the ADR-003 stage 3.1 review already
    established that this exact error text reaches the end-user notification
    banner verbatim (see backend/app.py's IntentValidationError handler), so
    that implementation-detail prefix is stripped before it ever gets there."""
    if not isinstance(args, list):
        return [f"expected a list of arguments, got {type(args).__name__}"]
    fields = dataclasses.fields(args_schema)
    if len(args) > len(fields):
        return [f"expected at most {len(fields)} argument(s), got {len(args)}"]
    payload = {field.name: value for field, value in zip(fields, args)}
    return [error.removeprefix("$.") for error in validate_payload(payload, args_schema)]


class _IntentRegistration:
    __slots__ = ("handler", "args_schema")

    def __init__(self, handler: IntentHandler, args_schema: type | None):
        self.handler = handler
        self.args_schema = args_schema


class _Topic:
    __slots__ = (
        "name", "builder", "patch_builder", "baseline_builder",
        "schema_version", "min_compatible", "revision",
    )

    def __init__(
        self,
        name: str,
        builder: StateBuilder,
        schema_version: int,
        min_compatible: int,
        patch_builder: PatchBuilder | None = None,
        baseline_builder: BaselineBuilder | None = None,
    ):
        self.name = name
        self.builder = builder
        self.patch_builder = patch_builder
        self.baseline_builder = baseline_builder
        self.schema_version = schema_version
        self.min_compatible = min_compatible
        self.revision = 0

    def _stamp(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["schemaVersion"] = self.schema_version
        payload["minCompatibleSchemaVersion"] = self.min_compatible
        payload["revision"] = self.revision
        return payload

    def baseline_snapshot(self) -> dict[str, Any] | None:
        """The state as of the last publish, stamped at the current revision -
        what send_snapshot serves so a new subscriber lands exactly where
        every existing connection already is. None when the topic has no
        baseline source, or has not published yet; the caller falls back to
        the live builder, which is correct in both cases (nothing has been
        published, so nothing can be behind)."""
        if self.baseline_builder is None:
            return None
        payload = self.baseline_builder()
        return None if payload is None else self._stamp(dict(payload))

    def snapshot(self) -> dict[str, Any]:
        """Build the current full-state payload, stamped at the CURRENT
        revision - it does NOT advance it.

        ADR-003 stage 3.4 fixed a real bug here: this method used to do
        `self.revision += 1` itself, and it is called from BOTH publish()
        (a real broadcast every attached connection sees) AND
        send_snapshot() (one connection's private subscribe handshake,
        never broadcast). So a second window merely subscribing silently
        advanced the number every OTHER connection was tracking, with no
        corresponding message ever sent to them. Harmless while `revision`
        was a decorative envelope field nobody compared; actively wrong now
        that it is the baseRevision the patch protocol uses to decide "did
        I miss a message?" - an unrelated subscribe would have manufactured
        a phantom gap and forced every other client into a needless
        re-snapshot. Advancing the revision is now the exclusive job of
        bump_revision() below, called only on a real broadcast."""
        return self._stamp(dict(self.builder()))

    def bump_revision(self) -> int:
        """Advance to the next revision - called ONLY from publish(), i.e.
        exactly once per real broadcast, so `revision` means "how many
        state-changing messages every connection has been sent" rather than
        the pre-3.4 "how many times the builder happened to run"."""
        self.revision += 1
        return self.revision


class _BufferedConnection:
    """ADR-003 stage 3.4 follow-on: one connection's bounded outbound queue
    plus the task that drains it.

    Closes the "slow client stalls everyone" half of stage 3.4's exit
    criterion. `_broadcast` used to `await conn.send_json(...)` inline, once
    per connection in a loop, so ONE client applying TCP backpressure blocked
    (a) delivery to every connection later in that loop and (b) the
    publishing coroutine itself - which is an agent run or the WS receive
    loop, so a single slow reader could stall unrelated work for the whole
    session. Measured before/after on a 0.2 s-per-send reader: the publisher
    went from 405 ms blocked to 0.1 ms.

    Overflow deliberately DROPS rather than blocking or growing without
    bound: blocking is the exact failure being fixed, and unbounded growth
    just converts a latency problem into a memory one. Dropping is safe
    because the client detects it and self-heals with no extra machinery -
    every patch carries `baseRevision`, so a dropped frame makes the next
    one fail the client's gap check, which already triggers a re-snapshot
    (see sceneStore.applyScenePatch / requestSceneResync). A dropped
    SNAPSHOT self-heals the same way: the client stays on an older revision,
    so the next patch gaps too. Nothing here needs a new wire kind or a
    "you are stale" signal - stage 3.4's recovery path already covers it."""

    __slots__ = ("conn", "queue", "task", "dropped")

    def __init__(self, conn: Connection, maxsize: int):
        self.conn = conn
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self.task: asyncio.Task | None = None
        self.dropped = 0

    def offer(self, message: dict[str, Any]) -> bool:
        """Enqueue without ever blocking. False means the queue was full and
        the message was dropped."""
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False

    def offer_superseding(self, message: dict[str, Any], topic: str) -> None:
        """Enqueue a full snapshot, first purging disposable queued state for
        the same topic - and, unlike offer(), guaranteed to succeed.

        CODE-REVIEW FIX. send_snapshot used to send directly on the socket,
        bypassing this queue, which caused two real problems for a buffered
        connection once a backlog existed: (1) the snapshot OVERTOOK queued
        older patches, which then arrived after it with baseRevision below
        the client's new revision - each refused, each refusal triggering yet
        another resync; (2) the obvious fix, routing it through offer(),
        creates a WORSE bug - a full queue silently drops the resync snapshot,
        and the client's sceneResyncPending flag is only ever cleared by a
        snapshot arriving, so it never asks again: wedged until reconnect.

        Purging is correct because a snapshot strictly supersedes every
        *uncorrelated* queued frame of its own topic: the snapshot carries the
        topic's current revision R, every queued state/patch was enqueued at
        some revision <= R, and a patch published AFTER this call lands behind
        the snapshot with baseRevision R - chaining perfectly. A state frame
        carrying an ``id`` is different: it resolves one explicit subscribe
        request, so dropping it would strand that request's client callback.
        Correlated states, stream frames, and other topics' frames are kept in
        order. Everything here is synchronous (no awaits), so the
        purge-and-requeue cannot interleave with the writer task's get() in a
        way that reorders the kept frames.

        If the remainder still fills the bounded queue, an uncorrelated frame
        is discarded before a correlated state. Only the pathological case
        where every queued entry is itself correlated falls back to dropping
        the oldest request; bounded memory remains a hard invariant."""
        kept: list[dict[str, Any]] = []
        while True:
            try:
                queued = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            is_correlated_state = queued.get("kind") == "state" and queued.get("id") is not None
            if (
                queued.get("topic") == topic
                and queued.get("kind") in ("state", "patch")
                and not is_correlated_state
            ):
                continue  # strictly older state for this topic - superseded
            kept.append(queued)
        if self.queue.maxsize > 0 and len(kept) >= self.queue.maxsize:
            drop_index = next(
                (
                    index
                    for index, item in enumerate(kept)
                    if not (item.get("kind") == "state" and item.get("id") is not None)
                ),
                0,
            )
            kept.pop(drop_index)
            self.dropped += 1
        for item in kept:
            self.queue.put_nowait(item)  # capacity is guaranteed: only removals so far
        self.queue.put_nowait(message)


class SessionBus:
    """Topics + connections + intent handlers for ONE session."""

    def __init__(
        self,
        session_id: str,
        *,
        coalesce_window_seconds: float = 0.0,
        send_queue_maxsize: int = DEFAULT_SEND_QUEUE_MAXSIZE,
        on_publish: Callable[[str, int], None] | None = None,
    ):
        """`coalesce_window_seconds` > 0 batches publishes of the SAME topic
        arriving within that window into one outbound message (ADR-003 stage
        3.4's "~16 ms coalescer"). It defaults to 0 - i.e. publish
        immediately, exactly as before - because that keeps `await
        publish(...)` synchronously complete on return, which the entire
        existing test suite depends on when it asserts on a recorder right
        after publishing. backend/app.py opts the real, shipped bus in; see
        DEFAULT_COALESCE_WINDOW_SECONDS.

        `send_queue_maxsize` bounds each BUFFERED connection's outbound queue
        (see attach(buffered=True) and _BufferedConnection)."""
        self.session_id = session_id
        # ADR-016 stage 16.3: optional - fires with (topic, serialized byte
        # size) from _broadcast, once per outbound message. None by default
        # (every test-constructed SessionBus, and this codebase has
        # hundreds) - a single None-check, so nothing pays the extra
        # json.dumps _broadcast otherwise has no reason to do (send_json
        # implementations serialize on their own; this is diagnostics-only
        # measurement, not the real wire encode).
        self._on_publish = on_publish
        self._topics: dict[str, _Topic] = {}
        self._intents: dict[tuple[str, str], _IntentRegistration] = {}
        self._connections: set[Connection] = set()
        self._buffered: dict[Connection, _BufferedConnection] = {}
        self._coalesce_window_seconds = coalesce_window_seconds
        self._send_queue_maxsize = send_queue_maxsize
        # Per-topic in-flight coalescing flush, keyed by topic name. See
        # publish() for the join-or-schedule rule.
        self._pending_flush: dict[str, asyncio.Task] = {}
        # ADR-004 stage 4.3: monotonic timestamp of when this bus last had
        # zero connections, or None while at least one is attached. Stamped
        # at construction time (not left None until a first attach/detach
        # cycle) so a session that is only ever reached via an HTTP route
        # that never attaches a connection at all (backend/assets.py's two
        # routes call EventBus.session() but never .attach()) still starts
        # its idle clock immediately, rather than being permanently exempt
        # from eviction by never having transitioned FROM connected.
        self.idle_since: float | None = time.monotonic()

    def set_publish_recorder(self, on_publish: Callable[[str, int], None] | None) -> None:
        """ADR-016 stage 16.3: post-construction hook for callers like
        backend/app.py's _configure_session, which only receives the
        already-constructed bus (via EventBus.session()) - never a chance
        to pass on_publish at __init__ time."""
        self._on_publish = on_publish

    # -- registration ------------------------------------------------------

    def register_topic(
        self,
        name: str,
        builder: StateBuilder,
        *,
        schema_version: int = 1,
        min_compatible: int = 1,
        patch_builder: PatchBuilder | None = None,
        baseline_builder: BaselineBuilder | None = None,
    ) -> None:
        """`patch_builder`, when given, opts this topic into ADR-003 stage
        3.4's delta protocol: publish() asks it for the ops accumulated
        since the last publish and, when it returns a non-empty list, sends
        a `kind: "patch"` message instead of a full snapshot. Omitting it
        (the default) keeps the topic full-snapshot-only, which is the
        deliberate, permanent answer for the 11 small topics whose whole
        payload is smaller than the bookkeeping a delta would need - only
        the scene topic is large enough to be worth it (see the ADR's own
        Decision text).

        `baseline_builder` must accompany `patch_builder` - it supplies the
        last-published state send_snapshot serves, without which a
        subscriber can be handed state newer than the revision stamped on it
        and diverge permanently. Enforced rather than merely documented,
        because that failure is completely silent.

        RAISED, not asserted. These were `assert` statements, which `python
        -O` strips - and with them gone a duplicate registration silently
        REPLACES the previous handler. backend/app.py's _configure_session
        registers 12 topics and ~90 intents in an order its own comments
        call load-bearing, so a silent overwrite there is exactly the class
        of failure these checks exist to make loud."""
        if name in self._topics:
            raise ValueError(f"topic {name!r} registered twice")
        if patch_builder is not None and baseline_builder is None:
            raise ValueError(
                f"topic {name!r}: a patch_builder needs a baseline_builder, or "
                f"send_snapshot serves live state stamped with a stale revision"
            )
        self._topics[name] = _Topic(
            name, builder, schema_version, min_compatible, patch_builder, baseline_builder
        )

    def register_intent(
        self,
        topic: str,
        intent: str,
        handler: IntentHandler,
        *,
        args_schema: type | None = None,
    ) -> None:
        """`args_schema`, when given, is a dataclass whose FIELD ORDER defines
        the expected positional args - dispatch_intent validates a real
        request's args against it before calling `handler` (ADR-003 stage
        3.2), turning a malformed request into an IntentValidationError
        instead of `handler(*args)`'s own bare TypeError from inside the
        handler body (potentially after it has already partially mutated
        state).

        `None` (the default) covers TWO genuinely different cases - review-
        fix: an earlier version of this docstring folded both into one
        "deliberate, legitimate opt-out" claim, which is only true of the
        first:
          1. Permanently unschemaable: a variadic handler (`ping(*args)`, an
             echo passthrough with no fixed arity) or a genuinely zero-arg
             one (`dismiss()`) has no static shape a fixed-field dataclass
             could ever describe - `None` here is the final state, not a gap.
          2. Not yet migrated: the ~130 intents this stage's own "topic-by-
             topic, scene last" rollout (see doc/adr/ADR-003-wire-protocol-
             v2.md) hasn't reached yet keep today's pre-3.2 behavior (a bad
             call still gets caught by app.py's generic exception handler,
             just without a schema's specific error text or the
             pre-execution guarantee) - `None` here is temporary, expected to
             become a real schema in a later increment.
        """
        key = (topic, intent)
        # Raised, not asserted - see register_topic's own note.
        if key in self._intents:
            raise ValueError(f"intent {topic}/{intent} registered twice")
        if args_schema is not None and not dataclasses.is_dataclass(args_schema):
            raise TypeError(
                f"args_schema for {topic}/{intent} must be a dataclass type, got {args_schema!r}"
            )
        self._intents[key] = _IntentRegistration(handler, args_schema)

    def has_topic(self, name: str) -> bool:
        """True when `name` has a registered builder on this bus.

        Cross-topic publishing is an established pattern here (the composer
        publishes "token-counter", the canvas publishes "notification"), but
        publish() raises UnknownTopicError for an unregistered topic. In
        production every topic is registered by _configure_session, so an
        unconditional cross-publish is safe there; a focused unit test that
        registers only ONE module's topics is where it would blow up. This
        lets a cross-publisher say "notify that surface too, if it exists"
        without either swallowing a real error or forcing every test to
        register unrelated modules.
        """
        return name in self._topics

    # -- connections -------------------------------------------------------

    def attach(self, conn: Connection, *, buffered: bool = False) -> None:
        """`buffered=True` gives this connection its own bounded outbound
        queue and a writer task, so a slow reader can never stall delivery to
        the other connections or block the coroutine that published (ADR-003
        stage 3.4 follow-on - see _BufferedConnection).

        Opt-in rather than automatic: unbuffered sends complete before
        publish() returns, which is what makes `await bus.publish(...);
        assert recorder.messages` deterministic for the test suite. The real
        WebSocket endpoint (backend/app.py's ws_endpoint) attaches buffered;
        in-process recorders in tests do not need to, since an in-memory list
        append cannot apply backpressure and so cannot stall anything."""
        self._connections.add(conn)
        self.idle_since = None
        if buffered and conn not in self._buffered:
            buffered_conn = _BufferedConnection(conn, self._send_queue_maxsize)
            drain = self._drain(buffered_conn)
            try:
                buffered_conn.task = asyncio.create_task(drain)
            except RuntimeError:
                # No running loop (a bare attach in a sync test): fall back to
                # unbuffered rather than half-initialising. Same defensive
                # posture as EventBus._ensure_eviction_loop_started - which
                # also closes the orphaned coroutine, since an un-awaited one
                # emits a RuntimeWarning at GC time.
                drain.close()
            else:
                self._buffered[conn] = buffered_conn

    def detach(self, conn: Connection) -> None:
        self._connections.discard(conn)
        buffered_conn = self._buffered.pop(conn, None)
        if buffered_conn is not None and buffered_conn.task is not None:
            buffered_conn.task.cancel()
        if not self._connections and self.idle_since is None:
            self.idle_since = time.monotonic()

    async def _drain(self, buffered_conn: _BufferedConnection) -> None:
        """One buffered connection's writer task: pull, send, repeat. A send
        failure detaches the connection, exactly as the inline path did -
        the difference is only WHO waits for the socket (this task, not the
        publisher)."""
        while True:
            message = await buffered_conn.queue.get()
            try:
                await buffered_conn.conn.send_json(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("dropping dead connection on session %s", self.session_id)
                self.detach(buffered_conn.conn)
                return

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    # -- state flow --------------------------------------------------------

    async def publish(self, topic: str) -> dict[str, Any]:
        """Broadcast `topic`'s current state to every attached connection -
        as an ADR-003 stage 3.4 `kind: "patch"` delta when the topic has a
        patch_builder that reports real changes, else as the full
        `kind: "state"` snapshot. Returns the snapshot either way (tests +
        callers that want the resulting state both rely on this, and it is
        cheap: the scene builder runs regardless for the fallback decision
        only when no patch was available).

        The patch/snapshot decision is deliberately fail-safe in one
        direction: ANY doubt sends the full snapshot. A patch_builder that
        returns None ("nothing marked dirty") means the mutation ran
        through a mutator that does not participate in dirty-tracking yet,
        which is indistinguishable here from "genuinely nothing changed" -
        and the full snapshot is correct for both. An EMPTY ops list is
        treated identically rather than being sent as a real patch: a patch
        carrying no ops tells the client "your baseRevision is now stale"
        while giving it nothing to apply, which is strictly worse than
        either sending real state or staying silent.

        COALESCING (ADR-003 stage 3.4 follow-on): when the bus was built with
        a coalesce window, calls for the same topic arriving inside one
        window share a single flush - the first schedules it, the rest await
        that same task, and one outbound message covers every mutation in the
        burst. The awaiting is deliberate rather than fire-and-forget:
        `await publish(...)` still means "the wire has this state" when it
        returns, which every existing caller and test assumes, and no caller
        is latency-sensitive to the window (verified: the token-by-token
        streaming path never routes through here at all - it uses
        publish_stream, which is separately batched at 60 ms / 40 chars).
        What coalescing genuinely saves is the several independent
        `asyncio.create_task`-ed agent runs in agents.py publishing at
        overlapping times: without it each pays its own full O(nodes) diff
        AND its own outbound frame."""
        # Resolve the topic BEFORE any scheduling so an unknown one still
        # raises synchronously to its own caller, exactly as it always has,
        # instead of surfacing a window later inside a shared flush task
        # (where it would also wrongly fail every unrelated joiner).
        if topic not in self._topics:
            raise UnknownTopicError(topic)
        window = self._coalesce_window_seconds
        if window <= 0:
            return await self._publish_now(topic)
        pending = self._pending_flush.get(topic)
        if pending is not None and not pending.done():
            # Join the in-flight window rather than opening a second one.
            #
            # shield() is the standard idiom for awaiting a task shared with
            # other callers, and it is kept for that reason - but honestly:
            # mutation testing could NOT produce a scenario where removing it
            # changes an observable outcome, even with two joiners provably
            # suspended on the same flush. A cancelled joiner leaves the
            # flush task itself untouched, and even if it did not, the
            # `not pending.done()` guard above means the next caller simply
            # opens a fresh window and still gets its state out. So this is
            # defensive, not load-bearing, and no test claims otherwise.
            return await asyncio.shield(pending)
        task = asyncio.ensure_future(self._flush_after(topic, window))
        self._pending_flush[topic] = task
        return await asyncio.shield(task)

    async def _flush_after(self, topic: str, window: float) -> dict[str, Any]:
        """Wait out the coalescing window, then publish once for everyone who
        joined it.

        The slot is released BEFORE the publish, not after: a mutation that
        lands while the send is in flight must open a NEW window rather than
        be folded into a flush that has already read the document, which
        would drop it from the wire entirely."""
        await asyncio.sleep(window)
        if self._pending_flush.get(topic) is asyncio.current_task():
            del self._pending_flush[topic]
        return await self._publish_now(topic)

    async def _publish_now(self, topic: str) -> dict[str, Any]:
        """The real build-and-broadcast, with no coalescing. Kept separate so
        the window-0 path is byte-for-byte the pre-coalescer behavior."""
        t = self._topics.get(topic)
        if t is None:
            raise UnknownTopicError(topic)
        ops = t.patch_builder() if t.patch_builder is not None else None
        if ops:
            base_revision = t.revision
            revision = t.bump_revision()
            await self._broadcast({
                "kind": "patch",
                "topic": topic,
                # Review-fix: patches carry the version envelope too. Without
                # it, this module's own stated contract ("a reader must
                # refuse a payload older than its stated minimum") had no
                # field to act on for the majority of scene messages - so
                # bumping the scene topic for a breaking change would leave
                # an already-snapshotted old client applying new-shaped node
                # payloads forever with nothing to refuse on.
                "schemaVersion": t.schema_version,
                "minCompatibleSchemaVersion": t.min_compatible,
                "revision": revision,
                "baseRevision": base_revision,
                "ops": ops,
            })
            # Review-fix: this used to `return t.snapshot()`, rebuilding
            # every node's wire dict a SECOND time purely to produce a return
            # value - measured at 2x the pre-3.4 per-publish CPU on the
            # 500-node workload (7.0 ms diff + 6.4 ms discarded rebuild vs
            # 6.4 ms before), on the event loop, across ~146 publish sites.
            # The stage's own commit message claimed CPU was unchanged; it
            # was not. The baseline the diff just recorded IS the state
            # every client now holds, so returning it is both free and more
            # accurate than a fresh live build (which could already include
            # a later mutation nobody has been sent).
            return t.baseline_snapshot() or t.snapshot()
        t.bump_revision()
        snapshot = t.snapshot()
        await self._broadcast({"kind": "state", "topic": topic, "payload": snapshot})
        return snapshot

    async def _broadcast(self, message: dict[str, Any]) -> None:
        """Send one message to every attached connection, detaching any that
        fails. Snapshot the set first: a failed send detaches mid-loop, and a
        dead socket must never poison the broadcast for the rest."""
        if self._on_publish is not None:
            # ADR-016 stage 16.3: measured once per broadcast, not per
            # connection - every attached connection receives the SAME
            # message, so the size is a property of the publish, not the
            # fan-out. json.dumps mirrors what a real send_json would
            # serialize closely enough for a diagnostics estimate (exact
            # wire bytes depend on the transport's own encoder).
            self._on_publish(message.get("topic", ""), len(json.dumps(message)))
        for conn in list(self._connections):
            buffered_conn = self._buffered.get(conn)
            if buffered_conn is not None:
                # Handed off, never awaited here: this socket's pace cannot
                # hold up any other connection or the publisher.
                if not buffered_conn.offer(message):
                    logger.warning(
                        "send queue full on session %s - dropping a message; the "
                        "client's own revision-gap check will re-snapshot",
                        self.session_id,
                    )
                continue
            try:
                await conn.send_json(message)
            except Exception:
                logger.warning("dropping dead connection on session %s", self.session_id)
                self.detach(conn)

    async def send_snapshot(
        self,
        topic: str,
        conn: Connection,
        *,
        request_id: Any | None = None,
    ) -> None:
        """Send the current state of one topic to one connection (the
        subscribe handshake - the successor of loadFinished -> publish()).

        Deliberately does NOT advance the topic's revision (see
        _Topic.snapshot's own docstring): this reaches exactly one
        connection and is invisible to every other, so advancing the shared
        counter here would manufacture a phantom gap in the patch
        protocol's baseRevision chain for everyone else.

        REVIEW-FIX: serves the LAST PUBLISHED state, not the live document,
        whenever the topic can supply one. Those differ whenever the
        document was mutated since the last publish, and handing a new
        subscriber state NEWER than the revision stamped on it caused
        permanent, undetectable divergence - see
        SceneDocument.published_scene_payload for the full mechanism and a
        reproduction. Falling back to the live builder is correct for a
        topic with no baseline (every non-scene topic, all full-snapshot)
        and for one that has not published yet (nothing can be behind).

        `request_id`, when supplied by an explicit client resubscribe, is
        echoed on that one state envelope. It lets the client distinguish the
        requested authority fence from an older broadcast already in the
        buffered writer when the request was sent."""
        t = self._topics.get(topic)
        if t is None:
            raise UnknownTopicError(topic)
        payload = t.baseline_snapshot() or t.snapshot()
        message = {"kind": "state", "topic": topic, "payload": payload}
        if request_id is not None:
            message["id"] = request_id
        # CODE-REVIEW FIX: a BUFFERED connection's snapshot must go through
        # its queue, not directly onto the socket - a direct send OVERTAKES
        # any queued older frames, which then arrive after it with
        # baseRevision below the client's new revision, each refused, each
        # refusal triggering yet another resync. offer_superseding (not plain
        # offer) both purges those now-obsolete frames and guarantees the
        # snapshot is never itself dropped by a full queue - a dropped resync
        # snapshot would leave the client's sceneResyncPending flag set
        # forever with nothing left to clear it. See _BufferedConnection.
        buffered_conn = self._buffered.get(conn)
        if buffered_conn is not None:
            buffered_conn.offer_superseding(message, topic)
            return
        await conn.send_json(message)

    async def publish_stream(
        self,
        *,
        topic: str,
        request_id: str,
        seq: int,
        delta: str,
        done: bool,
        reset: bool = False,
    ) -> None:
        """Broadcast one streaming delta chunk (Qt-removal plan R4.4).
        Sibling to publish()/send_snapshot(), deliberately NOT a topic
        snapshot: bypasses _Topic entirely - no revision bump, no
        schemaVersion stamp. This is a brand-new `kind: "stream"` wire
        message living outside the versioned-snapshot contract, for a
        15-17Hz delta channel that would otherwise force `_Topic.snapshot()`
        to falsely bump `revision` dozens of times per reply.

        `topic` is informational only (which UI surface's request this is) -
        unlike publish()/send_snapshot(), it is never looked up in
        self._topics, so an unregistered/arbitrary topic name here is not an
        error. Same broadcast/dead-connection-detach shape as publish()."""
        message = {
            "kind": "stream",
            "topic": topic,
            "requestId": request_id,
            "seq": seq,
            "delta": delta,
            "done": done,
            "reset": reset,
        }
        await self._broadcast(message)

    def topic_names(self) -> list[str]:
        return sorted(self._topics)

    async def dispatch_intent(self, topic: str, intent: str, args: list[Any]) -> Any:
        """Run a registered intent handler. Sync and async handlers are both
        supported; sync handlers run in a thread so a slow one cannot stall
        the event loop (QThread's replacement in miniature).

        ADR-003 stage 3.2: when the handler was registered with an
        args_schema, `args` is validated against it BEFORE the handler is
        ever called - raises IntentValidationError on a mismatch, so a
        malformed request cannot reach (and partially mutate state through)
        the handler body. A registration with no schema (args_schema=None,
        the default) skips this - see register_intent's own docstring for
        why that is a legitimate, not merely deferred, choice for some
        intents."""
        registration = self._intents.get((topic, intent))
        if registration is None:
            if topic not in self._topics and not any(t == topic for t, _ in self._intents):
                raise UnknownTopicError(topic)
            raise UnknownIntentError(f"{topic}/{intent}")
        if registration.args_schema is not None:
            errors = _validate_intent_args(registration.args_schema, args)
            if errors:
                raise IntentValidationError(errors)
        elif not isinstance(args, list):
            # SECURITY-FIX: a schema'd intent already gets this exact check
            # via _validate_intent_args above; an intent with NO schema
            # (args_schema=None - most of them) skipped it entirely and
            # `handler(*args)` unpacked whatever shape the client sent.
            # Python star-unpacks a str by character and a dict by key, so a
            # non-list args (e.g. a bare string) reached an *args/single-
            # string-parameter handler as silently mangled positional values
            # instead of the validation error a schema'd intent would raise
            # for the identical mistake - a protocol-hardening gap that
            # would otherwise re-open per-handler as intents are added.
            # Reusing IntentValidationError's own message shape keeps every
            # intent's malformed-args behavior identical regardless of
            # whether it has a schema.
            raise IntentValidationError([f"expected a list of arguments, got {type(args).__name__}"])
        handler = registration.handler
        if inspect.iscoroutinefunction(handler):
            return await handler(*args)
        return await asyncio.to_thread(handler, *args)


class EventBus:
    """All sessions. Session buses are created on first use and configured by
    the app's registrar so every session exposes the same topic/intent
    surface over its own state."""

    def __init__(
        self,
        configure_session: Callable[[SessionBus], None] | None = None,
        *,
        session_idle_ttl_seconds: float = DEFAULT_SESSION_IDLE_TTL_SECONDS,
        sweep_interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
        evict_idle_session: Callable[[SessionBus], bool] | None = None,
        allowed_session_ids: frozenset[str] | None = None,
        coalesce_window_seconds: float = 0.0,
    ):
        self._sessions: dict[str, SessionBus] = {}
        self._configure_session = configure_session
        # ADR-003 stage 3.4 follow-on: handed to every SessionBus this
        # EventBus mints. 0.0 (the default) keeps publishes immediate, which
        # is what the test suite's `await publish(); assert recorder` shape
        # relies on; backend/app.py's real bus passes the ADR's ~16 ms.
        self._coalesce_window_seconds = coalesce_window_seconds
        self._session_idle_ttl_seconds = session_idle_ttl_seconds
        self._sweep_interval_seconds = sweep_interval_seconds
        # ADR-004 stage 4.3: None (the default) is the pre-stage-4.3
        # behavior - EventBus is a genuinely reusable, domain-agnostic
        # multi-session primitive (see the class docstring), and plenty of
        # its own tests (backend/tests/test_event_bus.py) construct several
        # distinct session ids directly to verify the isolation mechanism
        # itself, independent of any one application's policy about which
        # ids are legitimate. A real value restricts session() to only ever
        # CREATE ids in this set (an already-existing id can still be
        # reconnected to regardless) - backend/app.py's create_app is the
        # one real caller that supplies one, since the shipped app has
        # exactly one legitimate session id (DEFAULT_SESSION_ID) and this
        # is what closes audit finding C6's "any ?session=<anything> mints
        # a permanent SessionBus" half.
        self._allowed_session_ids = allowed_session_ids
        # ADR-004 stage 4.3: injected, not implemented here, because tearing
        # a session down for real (cancel the autosave task, cancel any
        # in-flight agent run) needs SessionContext/AgentDispatcher
        # knowledge this module deliberately doesn't have (see the module
        # docstring). backend/app.py's create_app supplies the real
        # implementation. Must return True if eviction should proceed
        # (after performing its own teardown) or False to skip this
        # session for this sweep (e.g. it found a real in-flight run a
        # monotonic-time TTL alone can't safely second-guess).
        self._evict_idle_session = evict_idle_session
        self._eviction_task: asyncio.Task | None = None

    def _ensure_eviction_loop_started(self) -> None:
        """Starts the free-running sweep loop, lazily and idempotently, on
        the first call made from within a running event loop.

        Not started in __init__: EventBus is very often constructed before
        any event loop is running (create_app() itself always runs
        synchronously, before uvicorn.Server.run() starts one - see
        graphlink_desktop.py's own _start_backend) - matches
        backend/autosave.py's register_autosave's own precedent for the
        identical constraint, including the try/except RuntimeError
        fallback: a bare create_app() in a test that never actually drives
        a request through a running loop simply never starts this, and
        sweep_idle_sessions() staying independently callable is what makes
        that non-fatal for testing the real logic."""
        if self._eviction_task is not None:
            return
        loop_coro = self._eviction_loop()
        try:
            self._eviction_task = asyncio.create_task(loop_coro)
        except RuntimeError:
            logger.warning(
                "session eviction: no running event loop yet - the background "
                "sweep is disabled for this process (expected in most test "
                "contexts; the real ws_endpoint/asset-route call sites always "
                "have one)"
            )
            loop_coro.close()

    async def _eviction_loop(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_interval_seconds)
            try:
                self.sweep_idle_sessions()
            except Exception:
                # Same "one bad tick must never end the loop forever"
                # reasoning as backend/autosave.py's own _loop().
                logger.exception("session eviction sweep failed")

    def sweep_idle_sessions(self) -> list[str]:
        """Evict every session idle (zero connections) for at least
        session_idle_ttl_seconds AND not vetoed by evict_idle_session.
        Directly callable - no sleep involved - so tests can exercise the
        real decision logic deterministically; see the module docstring's
        own note on why, and backend/autosave.py's bus.autosave_guarded_tick
        for the established precedent this mirrors.

        Returns the evicted session ids, sorted."""
        if self._evict_idle_session is None:
            return []
        now = time.monotonic()
        evicted: list[str] = []
        for session_id, bus in list(self._sessions.items()):
            if bus.idle_since is None:
                continue  # at least one live connection
            if now - bus.idle_since < self._session_idle_ttl_seconds:
                continue  # idle, but not for long enough yet
            if not self._evict_idle_session(bus):
                # The callback found a reason not to (e.g. a real in-flight
                # run) - reconsidered next sweep, not retried immediately.
                continue
            del self._sessions[session_id]
            evicted.append(session_id)
            logger.info(
                "evicted idle session %r after %.0fs with no connection",
                session_id, now - bus.idle_since,
            )
        return sorted(evicted)

    def session(self, session_id: str = DEFAULT_SESSION_ID) -> SessionBus:
        self._ensure_eviction_loop_started()
        bus = self._sessions.get(session_id)
        if bus is None:
            if self._allowed_session_ids is not None and session_id not in self._allowed_session_ids:
                # ADR-004 stage 4.3: "unknown ids are rejected, not
                # auto-created" - only when this EventBus was constructed
                # with a real allowed_session_ids restriction (see
                # __init__'s own docstring); unrestricted (the default)
                # creates any id on first use, exactly as before this
                # stage.
                raise UnknownSessionError(session_id)
            bus = SessionBus(session_id, coalesce_window_seconds=self._coalesce_window_seconds)
            if self._configure_session is not None:
                self._configure_session(bus)
            self._sessions[session_id] = bus
        return bus

    def session_ids(self) -> list[str]:
        return sorted(self._sessions)
