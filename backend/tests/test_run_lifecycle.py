"""ADR-002 stage 2.3: RunHandle/RunRegistry, the primitive replacing 3 of
AgentDispatcher's 12 independent in-flight-request dicts (chat/
conversation, chart, note - see backend/run_lifecycle.py's own docstring).

run_single_shot (the shared chart/note skeleton) is deliberately NOT
covered by a separate unit-test suite here: its only two callers,
start_chart_generation and start_note_generation, already exercise every
one of its branches (busy/success/validate-fail/timeout/exception) end to
end in backend/tests/test_agents.py, including exact message-string
assertions - a second, isolated suite would just re-test the same
branches through a fake bus/registry with no additional signal. This file
covers what only the registry itself can prove: claim/release bookkeeping,
kind-scoped busy checks, and cancel semantics.
"""

from __future__ import annotations

import asyncio
import threading

from backend.run_lifecycle import RunHandle, RunRegistry


def test_claim_returns_a_handle_with_a_fresh_uuid4_request_id():
    registry = RunRegistry()
    handle = registry.claim("chat")
    assert isinstance(handle, RunHandle)
    assert handle.kind == "chat"
    assert handle.request_id
    other = registry.claim("chat")
    assert other.request_id != handle.request_id


def test_is_busy_is_false_before_any_claim():
    registry = RunRegistry()
    assert registry.is_busy("chat") is False


def test_is_busy_is_true_after_a_claim_of_that_kind():
    registry = RunRegistry()
    registry.claim("chart")
    assert registry.is_busy("chart") is True


def test_is_busy_is_scoped_per_kind_not_global():
    registry = RunRegistry()
    registry.claim("chart")
    assert registry.is_busy("note") is False, "an in-flight chart run must not read as a busy note slot"
    assert registry.is_busy("chat") is False


def test_claim_captures_node_id_purely_for_introspection():
    registry = RunRegistry()
    handle = registry.claim("chart", node_id="n-42")
    assert handle.node_id == "n-42"
    # node_id is NEVER part of the busy-check key - every pilot's guard is
    # session-wide, not per-node (see this module's own docstring).
    assert registry.is_busy("chart") is True


def test_release_removes_the_handle_and_clears_is_busy():
    registry = RunRegistry()
    handle = registry.claim("note")
    registry.release(handle.request_id)
    assert registry.is_busy("note") is False
    assert registry.get(handle.request_id) is None


def test_release_of_an_unknown_request_id_is_a_safe_noop():
    registry = RunRegistry()
    registry.release("never-claimed")  # must not raise


def test_get_returns_the_claimed_handle():
    registry = RunRegistry()
    handle = registry.claim("chat")
    assert registry.get(handle.request_id) is handle


def test_get_returns_none_for_an_unknown_request_id():
    registry = RunRegistry()
    assert registry.get("never-claimed") is None


def test_cancel_sets_the_cancel_event_and_returns_true():
    registry = RunRegistry()
    cancel_event = threading.Event()
    handle = registry.claim("chat", cancel_event=cancel_event)
    assert registry.cancel(handle.request_id) is True
    assert cancel_event.is_set()


def test_cancel_returns_false_for_an_unknown_request_id():
    registry = RunRegistry()
    assert registry.cancel("never-claimed") is False


def test_cancel_returns_false_for_a_handle_with_no_cancel_event():
    # chart/note's own shape - no cancellation checkpoint of their own,
    # see backend/run_lifecycle.py's own docstring.
    registry = RunRegistry()
    handle = registry.claim("chart")
    assert registry.cancel(handle.request_id) is False


def test_cancel_all_trips_every_cancel_event_and_skips_handles_without_one():
    registry = RunRegistry()
    chat_cancel = threading.Event()
    registry.claim("chat", cancel_event=chat_cancel)
    chart_handle = registry.claim("chart")  # no cancel_event

    registry.cancel_all()  # must not raise on the cancel_event-less handle

    assert chat_cancel.is_set()
    assert registry.get(chart_handle.request_id) is not None, "cancel_all() must not release anything"


def test_cancel_all_on_an_empty_registry_is_a_safe_noop():
    RunRegistry().cancel_all()  # must not raise


def test_attach_task_stores_the_task_without_touching_the_registry():
    async def _noop():
        return None

    async def run():
        registry = RunRegistry()
        handle = registry.claim("chat")
        task = asyncio.create_task(_noop())
        registry.attach_task(handle, task)
        assert handle.task is task
        assert registry.get(handle.request_id) is handle
        await task

    asyncio.run(run())


def test_values_lists_every_claimed_handle_regardless_of_kind():
    registry = RunRegistry()
    a = registry.claim("chat")
    b = registry.claim("chart")
    assert {h.request_id for h in registry.values()} == {a.request_id, b.request_id}
