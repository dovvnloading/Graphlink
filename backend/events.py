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
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)

StateBuilder = Callable[[], dict[str, Any]]
IntentHandler = Callable[..., Any | Awaitable[Any]]


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

    # -- connections -------------------------------------------------------

    def attach(self, conn: Connection) -> None:
        self._connections.add(conn)

    def detach(self, conn: Connection) -> None:
        self._connections.discard(conn)

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

    def __init__(self, configure_session: Callable[[SessionBus], None] | None = None):
        self._sessions: dict[str, SessionBus] = {}
        self._configure_session = configure_session

    def session(self, session_id: str = "default") -> SessionBus:
        bus = self._sessions.get(session_id)
        if bus is None:
            bus = SessionBus(session_id)
            if self._configure_session is not None:
                self._configure_session(bus)
            self._sessions[session_id] = bus
        return bus

    def session_ids(self) -> list[str]:
        return sorted(self._sessions)
