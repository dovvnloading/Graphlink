"""Killable Scientific Workbench worker integration and receipt safety.

The held operation signals entry and waits on an unset event. These tests do
not rely on an arbitrary sleep to prove concurrency or cancellation.
"""

import asyncio
import multiprocessing
import os
import threading
from pathlib import Path

import pytest

from backend.tests.test_scientific_workbench import dispatch, envelope, grant, session
from graphlink_plugins.scientific import workbench
from graphlink_plugins.scientific.core import Field, Operation
from graphlink_plugins.scientific.execution import (
    ExecutionFailure, TIMEOUT_ENV, execute_in_process, execution_timeout,
)


def _quick(data):
    return {"summary": "Stable answer.", "values": {"quantity (m)": data["quantity"]}}


def _held(data):
    Path(data["marker"]).write_text(str(os.getpid()), encoding="ascii")
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
