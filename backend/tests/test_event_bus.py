"""Event-bus unit tests (Qt-removal plan R0): envelope stamping, session
isolation, broadcast resilience, intent dispatch."""

import asyncio

import pytest

from backend.events import (
    EventBus,
    SessionBus,
    UnknownIntentError,
    UnknownTopicError,
)


class Recorder:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    async def send_json(self, data):
        if self.fail:
            raise ConnectionError("dead socket")
        self.messages.append(data)


def make_session(name="s1"):
    bus = SessionBus(name)
    state = {"count": 0}
    bus.register_topic("counter", lambda: {"count": state["count"]})

    def bump(by):
        state["count"] += by
        return state["count"]

    bus.register_intent("counter", "bump", bump)
    return bus, state


def test_snapshot_envelope_matches_island_bridge_contract():
    bus, _ = make_session()
    snap = asyncio.run(bus.publish("counter"))
    assert snap["schemaVersion"] == 1
    assert snap["minCompatibleSchemaVersion"] == 1
    assert snap["revision"] == 1
    assert snap["count"] == 0
    snap2 = asyncio.run(bus.publish("counter"))
    assert snap2["revision"] == 2, "revision must increment per publish"


def test_broadcast_reaches_all_connections_and_drops_dead_ones():
    async def run():
        bus, _ = make_session()
        alive, dead = Recorder(), Recorder(fail=True)
        bus.attach(alive)
        bus.attach(dead)
        await bus.publish("counter")
        assert len(alive.messages) == 1
        assert alive.messages[0]["kind"] == "state"
        assert alive.messages[0]["topic"] == "counter"
        assert bus.connection_count == 1, "dead connection must be detached"
        # A second publish must not fail because of the removed socket.
        await bus.publish("counter")
        assert len(alive.messages) == 2

    asyncio.run(run())


def test_intent_dispatch_sync_handler_runs_and_returns():
    bus, state = make_session()
    result = asyncio.run(bus.dispatch_intent("counter", "bump", [5]))
    assert result == 5
    assert state["count"] == 5


def test_intent_dispatch_async_handler():
    bus, _ = make_session()

    async def async_intent(x):
        await asyncio.sleep(0)
        return x * 2

    bus.register_intent("counter", "double", async_intent)
    assert asyncio.run(bus.dispatch_intent("counter", "double", [21])) == 42


def test_unknown_topic_and_intent_raise_typed_errors():
    bus, _ = make_session()
    with pytest.raises(UnknownTopicError):
        asyncio.run(bus.publish("nope"))
    with pytest.raises(UnknownTopicError):
        asyncio.run(bus.dispatch_intent("nope", "x", []))
    with pytest.raises(UnknownIntentError):
        asyncio.run(bus.dispatch_intent("counter", "nope", []))


def test_sessions_are_isolated():
    calls = []

    def configure(session_bus):
        calls.append(session_bus.session_id)
        state = {"n": 0}
        session_bus.register_topic("t", lambda: {"n": state["n"]})
        session_bus.register_intent("t", "set", lambda v: state.__setitem__("n", v))

    bus = EventBus(configure_session=configure)
    a, b = bus.session("a"), bus.session("b")
    assert bus.session("a") is a, "same id must return the same session"
    assert calls == ["a", "b"], "configurator runs once per session"

    asyncio.run(a.dispatch_intent("t", "set", [7]))
    snap_a = asyncio.run(a.publish("t"))
    snap_b = asyncio.run(b.publish("t"))
    assert snap_a["n"] == 7
    assert snap_b["n"] == 0, "state must not leak across sessions"


def test_duplicate_registration_is_a_programming_error():
    bus, _ = make_session()
    with pytest.raises(AssertionError):
        bus.register_topic("counter", dict)
    with pytest.raises(AssertionError):
        bus.register_intent("counter", "bump", lambda: None)
