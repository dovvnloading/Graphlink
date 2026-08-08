"""Event-bus unit tests (Qt-removal plan R0): envelope stamping, session
isolation, broadcast resilience, intent dispatch."""

import asyncio
from dataclasses import dataclass

import pytest

from backend.events import (
    EventBus,
    IntentValidationError,
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


# -- ADR-016 stage 16.3: publish-size recording --------------------------


def test_on_publish_is_a_no_op_by_default():
    # Every SessionBus() in this file (and the hundreds elsewhere in the
    # suite) constructs with no on_publish - must never raise just from
    # publishing.
    bus, _ = make_session()
    asyncio.run(bus.publish("counter"))  # must not raise


def test_on_publish_fires_with_topic_and_a_positive_byte_size():
    calls = []
    bus = SessionBus("s1", on_publish=lambda topic, size: calls.append((topic, size)))
    bus.register_topic("counter", lambda: {"count": 0})

    asyncio.run(bus.publish("counter"))

    assert len(calls) == 1
    topic, size = calls[0]
    assert topic == "counter"
    assert size > 0


def test_set_publish_recorder_applies_to_the_next_publish():
    calls = []
    bus, _ = make_session()
    bus.set_publish_recorder(lambda topic, size: calls.append((topic, size)))

    asyncio.run(bus.publish("counter"))

    assert len(calls) == 1
    assert calls[0][0] == "counter"


def test_set_publish_recorder_of_none_disables_recording():
    calls = []
    bus = SessionBus("s1", on_publish=lambda topic, size: calls.append((topic, size)))
    bus.register_topic("counter", lambda: {"count": 0})
    bus.set_publish_recorder(None)

    asyncio.run(bus.publish("counter"))

    assert calls == []


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


# -- ADR-003 stage 3.2: args_schema validation --------------------------------


@dataclass
class _GreetArgs:
    name: str
    greeting: str | None = None


def _make_greet_session():
    """A session with ONE args_schema-validated intent, plus a call-count
    side channel so a test can prove the handler was or wasn't actually
    invoked - the exit criterion is "unreachable without prior validation",
    not just "eventually errors"."""
    bus = SessionBus("greet-session")
    calls = []

    def greet(name, greeting=None):
        calls.append((name, greeting))
        return f"{greeting or 'Hello'}, {name}!"

    bus.register_intent("app-test", "greet", greet, args_schema=_GreetArgs)
    return bus, calls


def test_args_schema_none_leaves_dispatch_unvalidated_matching_pre_3_2_behavior():
    # No args_schema passed - the exact call shape every intent had before
    # this stage; a wrong-arity call still raises, but as the handler's own
    # bare TypeError (caught later by app.py's generic handler), never
    # IntentValidationError - dispatch_intent didn't even look at args.
    bus, _ = make_session()
    with pytest.raises(TypeError):
        asyncio.run(bus.dispatch_intent("counter", "bump", []))


def test_args_schema_valid_args_call_the_handler_and_return_its_result():
    bus, calls = _make_greet_session()
    result = asyncio.run(bus.dispatch_intent("app-test", "greet", ["Ada"]))
    assert result == "Hello, Ada!"
    assert calls == [("Ada", None)]


def test_args_schema_optional_field_can_also_be_supplied_explicitly():
    # Review-fix: this test used to be byte-identical to the one above (both
    # only ever omitted `greeting`), so its name promised dedicated coverage
    # of the optional field's two valid shapes but only ever exercised one.
    # The complementary case - `greeting` supplied explicitly, not omitted -
    # is what actually makes this a distinct test: `X | None` optional in
    # validate_payload means "not required if absent", not "must be absent".
    bus, calls = _make_greet_session()
    result = asyncio.run(bus.dispatch_intent("app-test", "greet", ["Ada", "Hey"]))
    assert result == "Hey, Ada!"
    assert calls == [("Ada", "Hey")]


def test_args_schema_rejects_missing_required_field_before_calling_the_handler():
    bus, calls = _make_greet_session()
    with pytest.raises(IntentValidationError) as exc_info:
        asyncio.run(bus.dispatch_intent("app-test", "greet", []))
    assert calls == [], "the handler must never run when validation fails"
    assert any("missing required field" in e for e in exc_info.value.errors)


def test_args_schema_rejects_too_many_args_before_calling_the_handler():
    bus, calls = _make_greet_session()
    with pytest.raises(IntentValidationError) as exc_info:
        asyncio.run(bus.dispatch_intent("app-test", "greet", ["Ada", "Hi", "extra"]))
    assert calls == [], "the handler must never run when validation fails"
    assert "expected at most 2 argument(s), got 3" in exc_info.value.errors[0]


def test_args_schema_rejects_wrong_type_before_calling_the_handler():
    bus, calls = _make_greet_session()
    with pytest.raises(IntentValidationError) as exc_info:
        asyncio.run(bus.dispatch_intent("app-test", "greet", [12345]))
    assert calls == [], "the handler must never run when validation fails"
    assert any("expected string" in e for e in exc_info.value.errors)


def test_args_schema_rejects_null_for_a_required_field():
    bus, calls = _make_greet_session()
    with pytest.raises(IntentValidationError):
        asyncio.run(bus.dispatch_intent("app-test", "greet", [None]))
    assert calls == []


def test_intent_validation_error_str_joins_the_errors_readably():
    bus, _ = _make_greet_session()
    with pytest.raises(IntentValidationError) as exc_info:
        asyncio.run(bus.dispatch_intent("app-test", "greet", []))
    assert str(exc_info.value) == "; ".join(exc_info.value.errors)


def test_args_schema_rejects_a_dict_shaped_args_instead_of_silently_corrupting_the_call():
    # Review-fix (HIGH): _handle_message (backend/app.py) builds args from
    # `message.get("args") or []` with no shape check - a client sending a
    # JSON OBJECT for "args" reached zip(fields, a_dict) unchanged, which
    # pairs each schema field with the dict's KEY STRINGS, never its values.
    # Validation could then report zero errors while the caller went on to
    # do handler(*args) with that SAME dict - which unpacks its keys, not
    # its values, as positional args. Empirically: dispatch_intent("app-
    # test", "greet", {"name": "ATTACKER-CONTROLLED-VALUE"}) used to call
    # greet("name") - the literal field name, not the real or even a
    # rejected value - silently corrupting the call instead of failing it.
    bus, calls = _make_greet_session()
    with pytest.raises(IntentValidationError) as exc_info:
        asyncio.run(bus.dispatch_intent("app-test", "greet", {"name": "ATTACKER-CONTROLLED-VALUE"}))
    assert calls == [], "the handler must never run on a malformed (non-list) args shape"
    assert any("expected a list" in e for e in exc_info.value.errors)
