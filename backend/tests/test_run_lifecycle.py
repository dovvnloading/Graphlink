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


# -- ADR-002 stage 2.4b: on_cancel / approval_future / kind-filtered cancel --


def test_claim_captures_on_cancel():
    registry = RunRegistry()
    on_cancel = lambda: None  # noqa: E731 - identity check only, never called here
    handle = registry.claim("web_research", on_cancel=on_cancel)
    assert handle.on_cancel is on_cancel
    assert handle.cancel_event is None


def test_claim_captures_approval_future():
    async def run():
        registry = RunRegistry()
        future = asyncio.get_running_loop().create_future()
        handle = registry.claim("pycoder", approval_future=future)
        assert handle.approval_future is future
        assert handle.cancel_event is None
        assert handle.on_cancel is None

    asyncio.run(run())


def test_cancel_fires_on_cancel_when_present_instead_of_cancel_event():
    registry = RunRegistry()
    fired = []
    handle = registry.claim("web_research", on_cancel=lambda: fired.append(True))
    assert registry.cancel(handle.request_id) is True
    assert fired == [True]


def test_cancel_returns_false_for_a_handle_with_neither_cancel_event_nor_on_cancel():
    registry = RunRegistry()
    handle = registry.claim("chart")
    assert registry.cancel(handle.request_id) is False


def test_cancel_with_matching_kind_succeeds():
    registry = RunRegistry()
    cancel_event = threading.Event()
    handle = registry.claim("artifact", cancel_event=cancel_event)
    assert registry.cancel(handle.request_id, kind="artifact") is True
    assert cancel_event.is_set()


def test_cancel_with_mismatched_kind_is_rejected_and_does_not_trip_the_event():
    """The load-bearing hardening this revision adds: a request_id that
    resolves to a DIFFERENT kind than the caller expected must never trip
    that unrelated run's cancellation - see RunRegistry.cancel's own
    docstring."""
    registry = RunRegistry()
    cancel_event = threading.Event()
    handle = registry.claim("artifact", cancel_event=cancel_event)
    assert registry.cancel(handle.request_id, kind="chat") is False
    assert not cancel_event.is_set(), "a mismatched kind must not trip the wrong run's cancellation"


def test_cancel_all_fires_on_cancel_for_every_handle_that_has_one():
    registry = RunRegistry()
    chat_cancel = threading.Event()
    registry.claim("chat", cancel_event=chat_cancel)
    web_fired = []
    registry.claim("web_research", on_cancel=lambda: web_fired.append(True))
    chart_handle = registry.claim("chart")  # neither mechanism

    registry.cancel_all()

    assert chat_cancel.is_set()
    assert web_fired == [True]
    assert registry.get(chart_handle.request_id) is not None, "cancel_all() must not release anything"


def test_cancel_all_pending_approvals_resolves_only_listed_kinds_with_false():
    async def run():
        registry = RunRegistry()
        loop = asyncio.get_running_loop()
        pycoder_future = loop.create_future()
        sandbox_future = loop.create_future()
        chat_future_stray = loop.create_future()  # a kind NOT in the listed set

        registry.claim("pycoder", approval_future=pycoder_future)
        registry.claim("code_sandbox", approval_future=sandbox_future)
        registry.claim("chat", approval_future=chat_future_stray)

        registry.cancel_all_pending_approvals(("pycoder", "code_sandbox"))

        assert pycoder_future.done() and pycoder_future.result() is False
        assert sandbox_future.done() and sandbox_future.result() is False
        assert not chat_future_stray.done(), "a kind outside the listed set must not be touched"

    asyncio.run(run())


def test_cancel_all_pending_approvals_never_clobbers_an_already_resolved_future():
    async def run():
        registry = RunRegistry()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        future.set_result(True)  # a human approved it a moment before disconnect

        registry.claim("pycoder", approval_future=future)
        registry.cancel_all_pending_approvals(("pycoder", "code_sandbox"))

        assert future.result() is True, "an already-resolved future must never be clobbered"

    asyncio.run(run())


def test_cancel_all_pending_approvals_on_a_handle_with_no_approval_future_is_a_safe_noop():
    registry = RunRegistry()
    registry.claim("pycoder")  # no approval_future passed
    registry.cancel_all_pending_approvals(("pycoder", "code_sandbox"))  # must not raise


def test_approval_future_can_be_replaced_in_place_on_the_same_handle():
    """Mirrors the real mid-run repair-loop pattern (a fresh Future
    replaces the old one on the same handle) - proves RunHandle is a
    plain, mutable dataclass, not frozen, so this reassignment works."""
    async def run():
        registry = RunRegistry()
        loop = asyncio.get_running_loop()
        first = loop.create_future()
        handle = registry.claim("pycoder", approval_future=first)

        second = loop.create_future()
        handle.approval_future = second

        assert registry.get(handle.request_id).approval_future is second

    asyncio.run(run())
