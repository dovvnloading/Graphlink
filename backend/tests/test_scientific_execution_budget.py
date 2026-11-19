"""Killable Scientific Workbench worker integration and receipt safety.

The held operation signals entry and waits on an unset event. These tests do
not rely on an arbitrary sleep to prove concurrency or cancellation.
"""

import asyncio
import multiprocessing
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.tests.test_scientific_workbench import (
    dispatch, envelope, grant, session as _shared_session,
)
from graphlink_plugins.scientific import execution, workbench
from graphlink_plugins.scientific.core import Field, Operation
from graphlink_plugins.scientific.execution import (
    ExecutionFailure, TIMEOUT_ENV, execute_in_process, execution_timeout,
)

# Re-export the shared pytest fixture under its injection name without
# shadowing an imported binding in the test's parameter list.
session = _shared_session


def _quick(data):
    return {"summary": "Stable answer.", "values": {"quantity (m)": data["quantity"]}}


def _held(data):
    # Write then rename so the marker never exists without its PID: a reader
    # polling for existence under parallel load could otherwise see "".
    marker = Path(data["marker"])
    partial = marker.with_name(marker.name + ".partial")
    partial.write_text(str(os.getpid()), encoding="ascii")
    os.replace(partial, marker)
    # This never returns: only process termination can stop the worker.
    threading.Event().wait()
    return {"summary": "Never committed", "values": {"quantity (m)": -999}}


FAST = Operation("quick", "Quick", "Quick independent calculation", (
    Field("quantity", "Quantity", "number", unit="m"),
), {"quantity": 3}, _quick)
HELD = Operation("held", "Held", "Controlled test-only blocked calculation", (
    Field("marker", "Started marker", "text", unit="path"),
), {"marker": "placeholder"}, _held)


def _no_science_workers():
    return not [p for p in multiprocessing.active_children() if p.name.startswith("graphlink-science-")]


async def _wait_for_marker(marker: Path):
    async def check():
        while not marker.exists():
            await asyncio.sleep(0.01)  # Observe a child signal, not elapsed-time behavior.
    await asyncio.wait_for(check(), timeout=20)


def test_budget_configuration_and_pack_override(monkeypatch):
    monkeypatch.setenv(TIMEOUT_ENV, "2.5")
    assert execution_timeout(FAST) == 2.5
    assert execution_timeout(Operation(**{**FAST.__dict__, "timeout_s": 8})) == 8
    for invalid in ("0", "nan", "inf", "-1", "not-a-number", "601"):
        monkeypatch.setenv(TIMEOUT_ENV, invalid)
        with pytest.raises(ValueError, match="timeout|TIMEOUT|seconds"):
            execution_timeout(FAST)


def test_success_preserves_receipt_and_reaps_process():
    async def scenario():
        result = await execute_in_process("core", FAST, {"quantity": 5}, None,
                                          timeout_s=20, cancel_event=asyncio.Event())
        assert result["values"] == {"quantity (m)": 5}
        assert result["provenance"]["packId"] == "core"
        assert result["provenance"]["operationId"] == FAST.id
        assert _no_science_workers()
    asyncio.run(scenario())


def test_registered_lambda_operation_is_resolved_in_spawn_worker():
    # The Numerical Methods pack has lambda-backed operation registrations;
    # these cannot be pickled into Windows spawn children.
    from graphlink_plugins.scientific.catalog import operations

    op = next(op for op in operations("numerics") if op.id == "solve_ivp_rk45")

    async def scenario():
        result = await execute_in_process(
            "numerics", op, op.example, None, timeout_s=20, cancel_event=asyncio.Event(),
        )
        assert result["operationId"] == op.id
        assert result["provenance"]["packId"] == "numerics"
        assert result["values"]
        assert _no_science_workers()

    asyncio.run(scenario())


def test_timeout_terminates_worker_without_partial_result(tmp_path):
    async def scenario():
        with pytest.raises(ExecutionFailure) as exception:
            await execute_in_process("core", HELD, {"marker": str(tmp_path / "timeout")}, None,
                                     timeout_s=0.5, cancel_event=asyncio.Event())
        result = exception.value.as_result()
        assert result["ok"] is False and result["errorType"] == "timeout"
        assert result["elapsedMs"] >= 0
        assert "latest" not in result and "provenance" not in result
        assert _no_science_workers()
    asyncio.run(scenario())


def test_cancel_terminates_a_running_worker_and_reaps_it(tmp_path):
    async def scenario():
        marker = tmp_path / "entered"
        cancel_event = asyncio.Event()
        pending = asyncio.create_task(execute_in_process(
            "core", HELD, {"marker": str(marker)}, None, timeout_s=25, cancel_event=cancel_event))
        try:
            await _wait_for_marker(marker)
            assert marker.read_text(encoding="ascii").isdigit()
            cancel_event.set()
            with pytest.raises(ExecutionFailure) as exception:
                await asyncio.wait_for(pending, timeout=10)
            assert exception.value.error_type == "cancelled"
            assert exception.value.elapsed_ms >= 0
        finally:
            cancel_event.set()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        assert _no_science_workers()
    asyncio.run(scenario())


def test_task_cancellation_also_reaps_the_worker(tmp_path):
    async def scenario():
        marker = tmp_path / "entered"
        pending = asyncio.create_task(execute_in_process(
            "core", HELD, {"marker": str(marker)}, None, timeout_s=25, cancel_event=asyncio.Event()))
        try:
            await _wait_for_marker(marker)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        assert _no_science_workers()
    asyncio.run(scenario())


def test_node_timeout_and_cancel_preserve_previous_result_and_allow_retry(session, monkeypatch, tmp_path):
    document, bus, _, _ = session
    monkeypatch.setattr(workbench, "operations", lambda pack: (FAST, HELD) if pack == "core" else ())
    monkeypatch.setenv(TIMEOUT_ENV, "20")
    grant(bus, "core")
    node_id = dispatch(bus, "executePlugin", "Scientific Core")
    assert dispatch(bus, "invokePluginIntent", "science_core", "run", {
        "node_id": node_id, "operation_id": "quick", "inputs": {"quantity": 5},
    }) == {"ok": True}
    saved = envelope(document, node_id)["latest"]
    body = document.nodes[node_id].content
    command_count = len(document.command_log)

    async def execute(name, operation, inputs):
        return await bus.dispatch_intent("app-plugins", "invokePluginIntent", [
            "science_core", name, {"node_id": node_id, "operation_id": operation, **inputs}])

    async def scenario():
        monkeypatch.setenv(TIMEOUT_ENV, "0.5")
        timed_out = await execute("run", "held", {"inputs": {"marker": str(tmp_path / "timeout")}})
        assert timed_out["ok"] is False and timed_out["errorType"] == "timeout"
        assert isinstance(timed_out["elapsedMs"], int)
        assert "budget" in timed_out["error"]
        assert envelope(document, node_id)["latest"] == saved
        assert document.nodes[node_id].content == body
        assert len(document.command_log) == command_count
        assert _no_science_workers()

        monkeypatch.setenv(TIMEOUT_ENV, "25")
        marker = tmp_path / "cancel"
        task = asyncio.create_task(execute("run", "held", {"inputs": {"marker": str(marker)}}))
        try:
            await _wait_for_marker(marker)
            busy = await execute("run", "quick", {"inputs": {"quantity": 9}})
            assert busy["errorType"] == "busy"
            stopped = await execute("cancel", "held", {})
            assert stopped == {"ok": True}
            cancelled = await asyncio.wait_for(task, timeout=10)
            assert cancelled["errorType"] == "cancelled"
            assert cancelled["elapsedMs"] >= 0
            assert cancelled["ok"] is False
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert envelope(document, node_id)["latest"] == saved
        assert document.nodes[node_id].content == body
        assert len(document.command_log) == command_count
        assert _no_science_workers()
        assert (await execute("cancel", "held", {}))["errorType"] == "not_running"
        assert await execute("run", "quick", {"inputs": {"quantity": 11}}) == {"ok": True}
        assert envelope(document, node_id)["latest"]["values"]["quantity (m)"] == 11

    asyncio.run(scenario())


# Independent budget/IPC audit regressions. All spawn targets are top-level,
# so these scenarios execute on Windows as well as POSIX.


def _incomplete_reply(sender, *_):
    sender.send(("ok", {"summary": "Partial but apparently valid", "values": {"quantity (m)": 1}}))
    sender.close()


def test_worker_ok_reply_requires_a_complete_verified_receipt(monkeypatch):
    # Previously any ("ok", dict) bypassed result/receipt validation.
    monkeypatch.setattr(execution, "_calculate_in_worker", _incomplete_reply)

    async def scenario():
        with pytest.raises(ExecutionFailure) as error:
            await execute_in_process("core", FAST, {"quantity": 3}, None,
                                     timeout_s=20, cancel_event=asyncio.Event())
        assert error.value.error_type == "worker_error"
        assert _no_science_workers()

    asyncio.run(scenario())


def test_worker_process_constructor_failure_closes_both_pipe_ends(monkeypatch):
    real_context = multiprocessing.get_context("spawn")

    class FailedContext:
        def __init__(self):
            self.reader, self.writer = real_context.Pipe(duplex=False)

        def Pipe(self, *, duplex):
            assert duplex is False
            return self.reader, self.writer

        def Process(self, **kwargs):
            raise RuntimeError("simulated process-construction failure")

    failed = FailedContext()
    monkeypatch.setattr(execution.multiprocessing, "get_context", lambda name: failed)

    async def scenario():
        with pytest.raises(ExecutionFailure) as error:
            await execute_in_process("core", FAST, {"quantity": 3}, None,
                                     timeout_s=20, cancel_event=asyncio.Event())
        assert error.value.error_type == "worker_error"

    try:
        asyncio.run(scenario())
        assert failed.reader.closed and failed.writer.closed
    finally:
        failed.reader.close()
        failed.writer.close()


def test_pipe_receive_never_blocks_the_event_loop(monkeypatch):
    real_context = multiprocessing.get_context("spawn")

    class ThreadOnlyReader:
        def __init__(self, inner):
            self.inner = inner

        def poll(self, *args):
            return self.inner.poll(*args)

        def recv(self):
            # Reader.poll() may be true with only the message header written.
            # On the old implementation, recv() ran on the event-loop thread.
            assert threading.current_thread() is not threading.main_thread()
            return self.inner.recv()

        def close(self):
            self.inner.close()

    class ProxyContext:
        def Pipe(self, *, duplex):
            reader, writer = real_context.Pipe(duplex=duplex)
            return ThreadOnlyReader(reader), writer

        def Process(self, **kwargs):
            return real_context.Process(**kwargs)

    monkeypatch.setattr(execution.multiprocessing, "get_context", lambda name: ProxyContext())

    async def scenario():
        result = await execute_in_process("core", FAST, {"quantity": 7}, None,
                                          timeout_s=20, cancel_event=asyncio.Event())
        assert result["values"]["quantity (m)"] == 7
        assert _no_science_workers()

    asyncio.run(scenario())


def test_concurrent_nodes_and_immediate_cancel_then_rerun_are_isolated(session, monkeypatch, tmp_path):
    document, bus, _, _ = session
    monkeypatch.setattr(workbench, "operations", lambda pack: (FAST, HELD) if pack == "core" else ())
    monkeypatch.setenv(TIMEOUT_ENV, "25")
    grant(bus, "core")
    cancelled_node = dispatch(bus, "executePlugin", "Scientific Core")
    other_node = dispatch(bus, "executePlugin", "Scientific Core")
    assert cancelled_node != other_node

    async def invoke(node_id, intent, operation, inputs=None):
        return await bus.dispatch_intent("app-plugins", "invokePluginIntent", [
            "science_core", intent, {"node_id": node_id, "operation_id": operation,
                                     **({"inputs": inputs} if inputs is not None else {})},
        ])

    async def scenario():
        assert await invoke(cancelled_node, "run", "quick", {"quantity": 3}) == {"ok": True}
        before = envelope(document, cancelled_node)["latest"]
        marker = tmp_path / "overlap"
        pending = asyncio.create_task(invoke(cancelled_node, "run", "held", {"marker": str(marker)}))
        try:
            await _wait_for_marker(marker)
            assert await invoke(other_node, "run", "quick", {"quantity": 9}) == {"ok": True}
            assert envelope(document, other_node)["latest"]["values"]["quantity (m)"] == 9
            assert await invoke(cancelled_node, "cancel", "held") == {"ok": True}
            # Racing a rerun against reap can be busy, but can never commit the
            # cancelled operation or corrupt the independently completed node.
            raced = await invoke(cancelled_node, "run", "quick", {"quantity": 11})
            assert raced == {"ok": True} or raced.get("errorType") == "busy"
            stopped = await asyncio.wait_for(pending, timeout=10)
            assert stopped["ok"] is False and stopped["errorType"] == "cancelled"
            if raced.get("errorType") == "busy":
                assert envelope(document, cancelled_node)["latest"] == before
                assert await invoke(cancelled_node, "run", "quick", {"quantity": 11}) == {"ok": True}
            assert envelope(document, cancelled_node)["latest"]["values"]["quantity (m)"] == 11
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        assert _no_science_workers()

    asyncio.run(scenario())


def _run_stalled_pipe_scenario(deadline: bool):
    """Spawn-importable scenario: the outer test can kill an unresponsive loop."""
    real_context = multiprocessing.get_context("spawn")
    receiving = threading.Event()
    release_receive = threading.Event()
    pipe_ends = []

    class StalledReader:
        def __init__(self, inner):
            self.inner = inner

        def recv(self):
            receiving.set()
            # A partial frame can strand recv indefinitely on Windows.
            # Keep it deliberately stuck until AFTER the run returns.
            release_receive.wait()
            return self.inner.recv()

        def close(self):
            self.inner.close()

    class ProxyContext:
        def Pipe(self, *, duplex):
            reader, writer = real_context.Pipe(duplex=duplex)
            pipe_ends.append((reader, writer))
            return StalledReader(reader), writer

        def Process(self, **kwargs):
            return real_context.Process(**kwargs)

    async def scenario():
        cancel_event = asyncio.Event()
        pending = asyncio.create_task(execute_in_process(
            "core", FAST, {"quantity": 17}, None,
            timeout_s=6 if deadline else 15, cancel_event=cancel_event,
        ))
        try:
            # Require that the receive is actually stalled before exercising
            # either cancellation or the wall-clock deadline.
            entered = await asyncio.wait_for(asyncio.to_thread(receiving.wait, 8), timeout=9)
            assert entered, "the receiver never reached the stalled recv"
            if not deadline:
                cancel_event.set()
            with pytest.raises(ExecutionFailure) as error:
                await asyncio.wait_for(pending, timeout=9)
            assert error.value.error_type == ("timeout" if deadline else "cancelled")
            assert _no_science_workers()
            assert len(pipe_ends) == 1
            assert all(reader.closed and writer.closed for reader, writer in pipe_ends)
        finally:
            release_receive.set()
            if not pending.done():
                pending.cancel()

    with patch.object(execution.multiprocessing, "get_context", lambda name: ProxyContext()):
        asyncio.run(scenario())


@pytest.mark.parametrize("deadline", [False, True], ids=["cancel", "deadline"])
def test_cancel_interrupts_a_stalled_pipe_receive(deadline):
    # asyncio.wait_for alone cannot bound asyncio.run() shutting down its
    # executor if recv() is hung. A disposable process bounds even that case.
    context = multiprocessing.get_context("spawn")
    harness = context.Process(target=_run_stalled_pipe_scenario, args=(deadline,))
    try:
        harness.start()
        harness.join(timeout=22)
        assert not harness.is_alive(), "stalled receive blocked cancellation/deadline or interpreter exit"
        assert harness.exitcode == 0, f"stalled receive scenario exited with {harness.exitcode}"
    finally:
        if harness.is_alive():
            harness.kill()
            harness.join(timeout=3)
        harness.close()
