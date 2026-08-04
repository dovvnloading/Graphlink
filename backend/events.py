"""WebSocket event bus: session-scoped topics carrying versioned full-state
snapshots plus named intents (Qt-removal plan R0).

Wire semantics are inherited from the IslandBridge/QWebChannel layer this
replaces, because they were already the right shape and the frontend's
generated validators depend on them:

- Server -> client: full-state snapshots only, never diffs. Every snapshot
  carries schemaVersion / minCompatibleSchemaVersion / revision, stamped
  here exactly as IslandBridge.publish() stamped them (a reader may accept a
  NEWER payload than it understands - additive-only guarantee - but must
  refuse one older than its stated minimum).
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
import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)

StateBuilder = Callable[[], dict[str, Any]]
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


class _Topic:
    __slots__ = ("name", "builder", "schema_version", "min_compatible", "revision")

    def __init__(self, name: str, builder: StateBuilder, schema_version: int, min_compatible: int):
        self.name = name
        self.builder = builder
        self.schema_version = schema_version
        self.min_compatible = min_compatible
        self.revision = 0

    def snapshot(self) -> dict[str, Any]:
        self.revision += 1
        payload = dict(self.builder())
        payload["schemaVersion"] = self.schema_version
        payload["minCompatibleSchemaVersion"] = self.min_compatible
        payload["revision"] = self.revision
        return payload


class SessionBus:
    """Topics + connections + intent handlers for ONE session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._topics: dict[str, _Topic] = {}
        self._intents: dict[tuple[str, str], IntentHandler] = {}
        self._connections: set[Connection] = set()
        # ADR-004 stage 4.3: monotonic timestamp of when this bus last had
        # zero connections, or None while at least one is attached. Stamped
        # at construction time (not left None until a first attach/detach
        # cycle) so a session that is only ever reached via an HTTP route
        # that never attaches a connection at all (backend/assets.py's two
        # routes call EventBus.session() but never .attach()) still starts
        # its idle clock immediately, rather than being permanently exempt
        # from eviction by never having transitioned FROM connected.
        self.idle_since: float | None = time.monotonic()

    # -- registration ------------------------------------------------------

    def register_topic(
        self,
        name: str,
        builder: StateBuilder,
        *,
        schema_version: int = 1,
        min_compatible: int = 1,
    ) -> None:
        assert name not in self._topics, f"topic {name!r} registered twice"
        self._topics[name] = _Topic(name, builder, schema_version, min_compatible)

    def register_intent(self, topic: str, intent: str, handler: IntentHandler) -> None:
        key = (topic, intent)
        assert key not in self._intents, f"intent {topic}/{intent} registered twice"
        self._intents[key] = handler

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

    def attach(self, conn: Connection) -> None:
        self._connections.add(conn)
        self.idle_since = None

    def detach(self, conn: Connection) -> None:
        self._connections.discard(conn)
        if not self._connections and self.idle_since is None:
            self.idle_since = time.monotonic()

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    # -- state flow --------------------------------------------------------

    async def publish(self, topic: str) -> dict[str, Any]:
        """Build a fresh snapshot for `topic` and broadcast it to every
        attached connection. Returns the snapshot (tests + send-current-state
        on subscribe both want it)."""
        t = self._topics.get(topic)
        if t is None:
            raise UnknownTopicError(topic)
        snapshot = t.snapshot()
        message = {"kind": "state", "topic": topic, "payload": snapshot}
        # Snapshot the set: a failed send detaches the connection mid-loop.
        for conn in list(self._connections):
            try:
                await conn.send_json(message)
            except Exception:
                # A dead socket must never poison the broadcast for the rest.
                logger.warning("dropping dead connection on session %s", self.session_id)
                self.detach(conn)
        return snapshot

    async def send_snapshot(self, topic: str, conn: Connection) -> None:
        """Send the current state of one topic to one connection (the
        subscribe handshake - the successor of loadFinished -> publish())."""
        t = self._topics.get(topic)
        if t is None:
            raise UnknownTopicError(topic)
        await conn.send_json({"kind": "state", "topic": topic, "payload": t.snapshot()})

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
        # Snapshot the set: a failed send detaches the connection mid-loop -
        # same defensive shape as publish() above.
        for conn in list(self._connections):
            try:
                await conn.send_json(message)
            except Exception:
                logger.warning("dropping dead connection on session %s", self.session_id)
                self.detach(conn)

    def topic_names(self) -> list[str]:
        return sorted(self._topics)

    async def dispatch_intent(self, topic: str, intent: str, args: list[Any]) -> Any:
        """Run a registered intent handler. Sync and async handlers are both
        supported; sync handlers run in a thread so a slow one cannot stall
        the event loop (QThread's replacement in miniature)."""
        handler = self._intents.get((topic, intent))
        if handler is None:
            if topic not in self._topics and not any(t == topic for t, _ in self._intents):
                raise UnknownTopicError(topic)
            raise UnknownIntentError(f"{topic}/{intent}")
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
    ):
        self._sessions: dict[str, SessionBus] = {}
        self._configure_session = configure_session
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
            bus = SessionBus(session_id)
            if self._configure_session is not None:
                self._configure_session(bus)
            self._sessions[session_id] = bus
        return bus

    def session_ids(self) -> list[str]:
        return sorted(self._sessions)
