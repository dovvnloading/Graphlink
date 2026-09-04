"""backend/session_context.py tests (ADR-002 stage 2.1d).

The module's whole reason to exist is turning a bare AttributeError
(several frames from the real cause) into a clear, named error when a
SessionBus is read for agent_dispatcher/canvas_document without ever
having gone through backend.app._configure_session. These tests prove
both directions: the round-trip works, and the failure mode is the
intended one, not an accident of whatever getattr happens to do.
"""

import pytest

from backend.agents import AgentDispatcher
from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.session_context import (
    SessionContext,
    SessionNotConfiguredError,
    attach_session_context,
    get_session_context,
)


class _FakeSettingsManager:
    def get(self, *_a, **_k):
        return None


def _make_context() -> SessionContext:
    return SessionContext(
        # Nothing in these tests reaches settings at all, so the fake stands
        # in for a SettingsManager it cannot be declared a subclass of.
        agent_dispatcher=AgentDispatcher(_FakeSettingsManager()),  # type: ignore[arg-type]
        canvas_document=SceneDocument(),
    )


def test_get_session_context_raises_a_named_error_when_nothing_was_attached():
    bus = SessionBus("unconfigured-session")

    with pytest.raises(SessionNotConfiguredError) as exc_info:
        get_session_context(bus)

    # The whole point of this module: the error must be immediately
    # actionable (names the session, names the real fix), not a bare
    # AttributeError several frames from where the real cause is.
    assert "unconfigured-session" in str(exc_info.value)
    assert "_configure_session" in str(exc_info.value)


def test_attach_then_get_round_trips_the_exact_same_object():
    bus = SessionBus("configured-session")
    context = _make_context()

    attach_session_context(bus, context)

    assert get_session_context(bus) is context


def test_get_session_context_reflects_a_re_attach_not_the_first_one():
    # Guards against a caching bug: a second attach_session_context call on
    # the same bus (shouldn't happen in production - _configure_session
    # runs once per session - but a test fixture might reset state) must
    # not leave get_session_context returning the stale first object.
    bus = SessionBus("reattached-session")
    first = _make_context()
    second = _make_context()

    attach_session_context(bus, first)
    attach_session_context(bus, second)

    assert get_session_context(bus) is second
    assert get_session_context(bus) is not first


def test_session_context_fields_are_exactly_what_was_passed_in():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    document = SceneDocument()
    bus = SessionBus("field-identity-session")

    attach_session_context(bus, SessionContext(agent_dispatcher=dispatcher, canvas_document=document))

    context = get_session_context(bus)
    assert context.agent_dispatcher is dispatcher
    assert context.canvas_document is document


def test_two_different_sessionbus_instances_do_not_share_context():
    # Regression guard for the attribute-name-collision class of bug: the
    # attachment must be per-INSTANCE (a real bus attribute), not keyed by
    # session_id in some shared dict that two SessionBus objects with
    # different ids could cross-read.
    bus_a = SessionBus("session-a")
    bus_b = SessionBus("session-b")
    context_a = _make_context()

    attach_session_context(bus_a, context_a)

    assert get_session_context(bus_a) is context_a
    with pytest.raises(SessionNotConfiguredError):
        get_session_context(bus_b)


def test_two_sessionbus_instances_with_the_identical_session_id_do_not_share_context():
    # The case that actually distinguishes real per-instance storage from a
    # session_id-keyed shared dict, which the different-ids test above
    # cannot: a session torn down and rebuilt with the SAME session_id
    # (a real production shape - EventBus never evicts by design, but a
    # future eviction/reconnect path could construct a fresh SessionBus for
    # an id that was already in use) must get a genuinely fresh, unattached
    # context - never the previous instance's dispatcher/document bleeding
    # through because the two objects happen to share a session_id string.
    # Confirmed via mutation testing that the different-ids test alone does
    # NOT catch a session_id-keyed dict implementation - this one does.
    first_bus = SessionBus("dup-id")
    first_context = _make_context()
    attach_session_context(first_bus, first_context)

    second_bus = SessionBus("dup-id")

    with pytest.raises(SessionNotConfiguredError):
        get_session_context(second_bus)
