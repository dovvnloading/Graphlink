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
    # A handle carrying neither mechanism cannot be cancelled. ADR-006
    # stage 6.2 note: no production kind claims this shape any more (even
    # chart/note now get a cancel_event from run_single_shot) - this pins
    # the registry-level contract for a mechanism-less handle itself.
    registry = RunRegistry()
    handle = registry.claim("chart")
    assert registry.cancel(handle.request_id) is False


def test_cancel_all_trips_every_cancel_event_and_skips_handles_without_one():
    registry = RunRegistry()
    chat_cancel = threading.Event()
    chat_handle = registry.claim("chat", cancel_event=chat_cancel)
    chart_handle = registry.claim("chart")  # no cancel_event

    registry.cancel_all()  # must not raise on the cancel_event-less handle

    assert chat_cancel.is_set()
    # ADR-006 stage 6.2 (release-on-cancel): a fired handle is popped
    # IMMEDIATELY - the pre-6.2 "cancel_all() must not release anything"
    # assertion here is now the opposite of the contract for any handle
    # that carries a mechanism. Only a mechanism-less handle survives.
    assert registry.get(chat_handle.request_id) is None, (
        "cancel_all() must release a fired handle immediately (release-on-cancel)"
    )
    assert registry.get(chart_handle.request_id) is not None, (
        "cancel_all() must not release a handle it could not fire"
    )


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
    chat_handle = registry.claim("chat", cancel_event=chat_cancel)
    web_fired = []
    web_handle = registry.claim("web_research", on_cancel=lambda: web_fired.append(True))
    chart_handle = registry.claim("chart")  # neither mechanism

    registry.cancel_all()

    assert chat_cancel.is_set()
    assert web_fired == [True]
    # ADR-006 stage 6.2 (release-on-cancel): both fired handles - whichever
    # mechanism they carry - are popped immediately; the stale "must not
    # release anything" assertion only ever passed vacuously here because
    # the surviving handle carried no mechanism at all.
    assert registry.get(chat_handle.request_id) is None
    assert registry.get(web_handle.request_id) is None
    assert registry.get(chart_handle.request_id) is not None, (
        "cancel_all() must not release a handle it could not fire"
    )


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


# -- ADR-006 stage 6.2: release-on-cancel / finalize / orphan tracking --------


def test_cancel_pops_the_handle_immediately_and_clears_is_busy():
    """ADR-006 stage 6.2 review fix: cancel() now releases the slot the
    moment it fires (release-on-cancel), instead of leaving release() to
    the worker's own finally - while a handle with NO mechanism must stay
    claimed (cancel returns False and pops nothing)."""
    registry = RunRegistry()
    cancel_event = threading.Event()
    handle = registry.claim("chat", cancel_event=cancel_event)
    assert registry.cancel(handle.request_id) is True
    assert cancel_event.is_set()
    assert registry.get(handle.request_id) is None, "cancel must pop the handle immediately"
    assert registry.is_busy("chat") is False

    bare = registry.claim("chart")  # no mechanism - cannot be cancelled
    assert registry.cancel(bare.request_id) is False
    assert registry.get(bare.request_id) is bare, "an unfired handle must not be popped"
    assert registry.is_busy("chart") is True


def test_cancel_auto_denies_an_unresolved_approval_future():
    """ADR-006 stage 6.2 review fix: a popped handle is unreachable to
    cancel_all_pending_approvals (it walks _handles), so _pop itself must
    auto-deny (False) an unresolved approval future or the run's parked
    `await approval_future` waits forever."""
    async def run():
        registry = RunRegistry()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        handle = registry.claim(
            "pycoder", cancel_event=threading.Event(), approval_future=future
        )
        assert registry.cancel(handle.request_id) is True
        assert future.done() and future.result() is False, "cancel must auto-deny, never approve"

    asyncio.run(run())


def test_cancel_never_clobbers_an_already_resolved_approval_future():
    """ADR-006 stage 6.2 review fix: an approval resolved a moment before
    the cancel lands must survive untouched - set_result on a done future
    would raise InvalidStateError."""
    async def run():
        registry = RunRegistry()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        future.set_result(True)  # a human approved it just before the cancel
        handle = registry.claim(
            "pycoder", cancel_event=threading.Event(), approval_future=future
        )
        assert registry.cancel(handle.request_id) is True  # must not raise InvalidStateError
        assert future.result() is True, "an already-resolved future must never be clobbered"

    asyncio.run(run())


def test_finalize_runs_exactly_once_on_cancel_and_the_stale_release_returns_false():
    """ADR-006 stage 6.2 review fix: on cancel, the handle's finalize (the
    surface's end transition) is scheduled exactly once by cancel() itself;
    the worker's late release() then returns False, so the gated tail
    pattern (`if registry.release(...): on_end()`) skips - never a second
    end transition."""
    async def run():
        registry = RunRegistry()
        counter = []

        async def finalize():
            counter.append(1)

        handle = registry.claim(
            "chat", cancel_event=threading.Event(), finalize=finalize
        )
        assert registry.cancel(handle.request_id) is True

        # Drain the finalize coroutine cancel() scheduled onto this loop.
        deadline = asyncio.get_running_loop().time() + 2
        while not counter and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert counter == [1], "finalize must run exactly once on cancel"

        # The worker's own finally now runs release() - it must report False
        # (cancel already popped), which is exactly what gates the tail skip.
        assert registry.release(handle.request_id) is False

        # And nothing schedules finalize a second time.
        await asyncio.sleep(0.05)
        assert counter == [1]

    asyncio.run(run())


def test_finalize_is_not_run_on_a_normal_release():
    """ADR-006 stage 6.2 review fix: on a NORMAL completion the surface's
    own finally owns the end transition (gated on release() -> True);
    the registry must never fire finalize itself then."""
    async def run():
        registry = RunRegistry()
        counter = []

        async def finalize():
            counter.append(1)

        handle = registry.claim(
            "chat", cancel_event=threading.Event(), finalize=finalize
        )
        assert registry.release(handle.request_id) is True
        await asyncio.sleep(0.05)  # give any (wrongly) scheduled finalize a chance to run
        assert counter == [], "finalize must never fire on a normal release"

    asyncio.run(run())


def test_cancel_from_a_worker_thread_sets_the_event_inline_and_marshals_the_pop_to_the_loop():
    """ADR-006 stage 6.2 review fix (thread affinity - pins _finish_cancel):
    cancelChatRequest's sync handler runs via asyncio.to_thread, so cancel()
    can arrive on a worker thread. The cancel EVENT must be set inline on
    that thread (zero added latency for the worker to observe it); the pop +
    finalize bookkeeping is marshalled onto the handle's loop and lands
    shortly after, exactly once."""
    async def run():
        registry = RunRegistry()
        cancel_event = threading.Event()
        counter = []

        async def finalize():
            counter.append(1)

        handle = registry.claim("chat", cancel_event=cancel_event, finalize=finalize)

        observed = {}

        def worker():
            observed["returned"] = registry.cancel(handle.request_id)
            # Checked ON the worker thread, before the loop had any chance
            # to run the marshalled bookkeeping.
            observed["event_set_inline"] = cancel_event.is_set()

        thread = threading.Thread(target=worker)
        thread.start()
        await asyncio.to_thread(thread.join, 5)
        assert observed["returned"] is True
        assert observed["event_set_inline"] is True

        # The pop lands on the loop shortly after (call_soon_threadsafe).
        deadline = asyncio.get_running_loop().time() + 2
        while registry.get(handle.request_id) is not None and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert registry.get(handle.request_id) is None, "the marshalled pop never landed on the loop"
        assert registry.is_busy("chat") is False

        deadline = asyncio.get_running_loop().time() + 2
        while not counter and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)  # settle: prove no second finalize follows
        assert counter == [1], "finalize must run exactly once for a worker-thread cancel"

    asyncio.run(run())


def test_has_any_live_work_counts_a_popped_but_unfinished_orphan_task():
    """ADR-006 stage 6.2 review fix: release-on-cancel empties _handles
    instantly, but the cancelled worker is still real in-flight work -
    has_any_live_work() must stay True until the orphaned task actually
    completes (session eviction consults exactly this)."""
    async def run():
        registry = RunRegistry()
        gate = asyncio.Event()

        async def held_worker():
            await gate.wait()

        handle = registry.claim("chat", cancel_event=threading.Event())
        registry.attach_task(handle, asyncio.create_task(held_worker()))
        await asyncio.sleep(0)  # let the task start

        assert registry.cancel(handle.request_id) is True
        assert registry.is_busy("chat") is False, "the slot itself frees immediately"
        assert registry.has_any_live_work() is True, (
            "a popped-but-unfinished task is still live work"
        )

        gate.set()
        deadline = asyncio.get_running_loop().time() + 2
        while registry.has_any_live_work() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert registry.has_any_live_work() is False

    asyncio.run(run())


# -- ADR-016 stage 16.1: run-lifecycle logging --------------------------
#
# Instrumented at the registry (claim/cancel/_pop) rather than at each of
# the app's 12 dispatch surfaces - see run_lifecycle.py's own module-level
# comment on _logger for why. These tests prove every kind gets a traced
# lifecycle "for free" through the shared primitive, without needing a
# real chat/chart/etc. dispatch surface in the loop.


def test_claim_logs_run_claimed_with_run_id_kind_and_node_id(caplog):
    registry = RunRegistry()
    with caplog.at_level("INFO", logger="graphlink.run"):
        handle = registry.claim("chart", node_id="n-1")

    records = [r for r in caplog.records if r.name == "graphlink.run"]
    assert len(records) == 1
    assert records[0].message == "run claimed"
    assert records[0].run_id == handle.request_id
    assert records[0].kind == "chart"
    assert records[0].node_id == "n-1"


def test_release_logs_run_released(caplog):
    registry = RunRegistry()
    handle = registry.claim("note")
    with caplog.at_level("INFO", logger="graphlink.run"):
        registry.release(handle.request_id)

    records = [r for r in caplog.records if r.name == "graphlink.run"]
    assert len(records) == 1
    assert records[0].message == "run released"
    assert records[0].run_id == handle.request_id
    assert records[0].kind == "note"


def test_release_of_an_unknown_request_id_logs_nothing(caplog):
    registry = RunRegistry()
    with caplog.at_level("INFO", logger="graphlink.run"):
        registry.release("never-claimed")

    assert [r for r in caplog.records if r.name == "graphlink.run"] == []


def test_cancel_logs_run_cancelled_then_run_released(caplog):
    registry = RunRegistry()
    handle = registry.claim("chat", cancel_event=threading.Event())
    with caplog.at_level("INFO", logger="graphlink.run"):
        assert registry.cancel(handle.request_id) is True

    records = [r for r in caplog.records if r.name == "graphlink.run"]
    messages = [r.message for r in records]
    assert messages == ["run cancelled", "run released"], (
        "cancel() fires the mechanism and immediately pops the slot - both "
        "must be traced, in order"
    )
    assert all(r.run_id == handle.request_id for r in records)
    assert all(r.kind == "chat" for r in records)


def test_cancel_of_a_kind_with_no_mechanism_logs_nothing(caplog):
    # Matches cancel()'s own "kind that cannot be cancelled" contract - a
    # handle with neither cancel_event nor on_cancel never fires, so nothing
    # about it should be logged either.
    registry = RunRegistry()
    handle = registry.claim("chart")
    with caplog.at_level("INFO", logger="graphlink.run"):
        assert registry.cancel(handle.request_id) is False

    assert [r for r in caplog.records if r.name == "graphlink.run"] == []


def test_cancel_all_logs_every_fired_handle(caplog):
    registry = RunRegistry()
    chat_handle = registry.claim("chat", cancel_event=threading.Event())
    web_handle = registry.claim("web_research", on_cancel=lambda: None)
    with caplog.at_level("INFO", logger="graphlink.run"):
        registry.cancel_all()

    records = [r for r in caplog.records if r.name == "graphlink.run"]
    cancelled_run_ids = {r.run_id for r in records if r.message == "run cancelled"}
    assert cancelled_run_ids == {chat_handle.request_id, web_handle.request_id}


# -- ADR-016 stage 16.3: diagnostics on_claim/on_end callbacks ----------
#
# Explicit callbacks (not derived from the caplog-visible logging above) -
# see backend/diagnostics.py's own module docstring for why. None by
# default (every test above this point never passes them) - these tests
# prove the callbacks fire with the right arguments and in the right order
# relative to each other, independent of backend/diagnostics.py's own
# DiagnosticsState (which has its own dedicated test file).


def test_on_claim_fires_with_run_id_kind_and_node_id():
    calls = []
    registry = RunRegistry(on_claim=lambda *args: calls.append(args))
    handle = registry.claim("chart", node_id="n-1")
    assert calls == [(handle.request_id, "chart", "n-1")]


def test_on_end_fires_completed_on_release():
    calls = []
    registry = RunRegistry(on_end=lambda *args: calls.append(args))
    handle = registry.claim("note")
    registry.release(handle.request_id)
    assert calls == [(handle.request_id, "completed")]


def test_on_end_does_not_fire_on_release_of_an_unknown_request_id():
    calls = []
    registry = RunRegistry(on_end=lambda *args: calls.append(args))
    registry.release("never-claimed")
    assert calls == []


def test_on_end_fires_cancelled_not_completed_on_cancel():
    calls = []
    registry = RunRegistry(on_end=lambda *args: calls.append(args))
    handle = registry.claim("chat", cancel_event=threading.Event())
    registry.cancel(handle.request_id)
    # cancel() pops via _finish_cancel -> _pop, but release() is never
    # called for a cancelled run - the worker's own late `finally` calls
    # release() too, and _pop only returns non-None once, so on_end must
    # fire exactly once, as "cancelled", never overwritten to "completed".
    assert calls == [(handle.request_id, "cancelled")]

    # The worker's own late release() (registry.release is idempotent -
    # _pop returns None the second time) must not re-fire on_end.
    registry.release(handle.request_id)
    assert calls == [(handle.request_id, "cancelled")]


def test_on_end_fires_cancelled_for_every_fired_handle_in_cancel_all():
    calls = []
    registry = RunRegistry(on_end=lambda *args: calls.append(args))
    chat_handle = registry.claim("chat", cancel_event=threading.Event())
    chart_handle = registry.claim("chart")  # no cancel mechanism - never fires
    registry.cancel_all()
    assert calls == [(chat_handle.request_id, "cancelled")]
    assert chart_handle.request_id not in [c[0] for c in calls]
