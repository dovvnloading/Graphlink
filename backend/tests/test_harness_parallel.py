"""Bounded, ordered concurrency for the workspace agent's read-only calls."""

from __future__ import annotations

import asyncio
import threading

import api_provider
import pytest

from backend.domain.graph import SceneDocument
from backend.harness import workspace as workspace_module
from backend.harness.loop import _MAX_CONCURRENT_READS, _execute_tool_calls, run_harness
from backend.harness.transcript import load_messages
from backend.harness.workspace import ensure_workspace
from backend.providers.base import ToolCall, ToolSpec
from backend.run_lifecycle import RunRegistry
from backend.tools import (
    FS_READ, FS_WRITE, GRAPH_READ, GRAPH_MUTATE,
    RunContext, ToolRegistry, ToolResult,
)


def _call(call_id: str, name: str) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments={})


def _register(registry: ToolRegistry, name: str, handler, *, approval="auto", parallel_safe=False):
    registry.register(
        ToolSpec(name=name, description=name, input_schema={"type": "object"}),
        handler,
        scopes={FS_READ if approval == "auto" else FS_WRITE},
        approval=approval,
        parallel_safe=parallel_safe,
    )


def _ctx(approve=None):
    async def allow(call):
        return await approve(call) if approve is not None else True

    return RunContext(
        granted_scopes=frozenset({FS_READ, FS_WRITE, GRAPH_READ, GRAPH_MUTATE}),
        request_approval=allow,
    )


async def _run_calls(registry, calls, ctx):
    recorded = []

    async def record(call, result, elapsed_ms):
        recorded.append((call.id, result.content, result.is_error))
        assert elapsed_ms >= 0

    await _execute_tool_calls(calls, registry, ctx, record)
    return recorded


def test_read_only_calls_overlap_but_never_exceed_the_limit():
    registry = ToolRegistry()
    in_flight = 0
    max_in_flight = 0
    completed = []

    async def reader(call, ctx):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            # Later requests finish first: recording must not follow completion.
            await asyncio.sleep(0.004 * (9 - int(call.id[1:])))
            completed.append(call.id)
            return ToolResult(content=call.id)
        finally:
            in_flight -= 1

    _register(registry, "probe.read", reader, parallel_safe=True)
    calls = [_call(f"r{i}", "probe.read") for i in range(8)]
    results = asyncio.run(_run_calls(registry, calls, _ctx()))
    ids = [call.id for call in calls]
    assert max_in_flight == _MAX_CONCURRENT_READS
    assert completed != ids
    assert results == [(identifier, identifier, False) for identifier in ids]


def test_writes_are_barriers_and_each_gets_its_own_approval():
    registry = ToolRegistry()
    state = 0
    active_reads = 0
    approved = []
    events = []

    async def read(call, ctx):
        nonlocal active_reads
        version = state
        active_reads += 1
        events.append(("start", call.id))
        try:
            await asyncio.sleep(0.005)
            return ToolResult(content=f"version {version}")
        finally:
            active_reads -= 1
            events.append(("end", call.id))

    async def write(call, ctx):
        nonlocal state
        assert active_reads == 0
        events.append(("write", call.id))
        state += 1
        return ToolResult(content=f"version {state}")

    async def approve(call):
        assert active_reads == 0
        approved.append(call.id)
        return True

    _register(registry, "probe.read", read, parallel_safe=True)
    _register(registry, "probe.write", write, approval="once")
    calls = [
        _call("r1", "probe.read"), _call("r2", "probe.read"),
        _call("w1", "probe.write"),
        _call("r3", "probe.read"), _call("r4", "probe.read"),
        _call("w2", "probe.write"), _call("r5", "probe.read"),
    ]
    results = asyncio.run(_run_calls(registry, calls, _ctx(approve)))
    assert approved == ["w1", "w2"]
    assert results == [
        ("r1", "version 0", False), ("r2", "version 0", False),
        ("w1", "version 1", False),
        ("r3", "version 1", False), ("r4", "version 1", False),
        ("w2", "version 2", False), ("r5", "version 2", False),
    ]
    assert events.index(("start", "r2")) < events.index(("end", "r1"))
    assert events.index(("end", "r2")) < events.index(("write", "w1"))
    assert events.index(("write", "w1")) < events.index(("start", "r3"))
    assert events.index(("end", "r4")) < events.index(("write", "w2"))
    assert events.index(("write", "w2")) < events.index(("start", "r5"))


def test_auto_approved_stateful_tools_remain_serial():
    registry = ToolRegistry()
    active = 0
    maximum = 0

    async def plan_update(call, ctx):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        await asyncio.sleep(0.005)
        active -= 1
        return ToolResult(content=call.id)

    _register(registry, "plan.update", plan_update)
    assert not registry.parallel_safe_for("plan.update")
    assert not registry.parallel_safe_for("unknown.tool")
    calls = [_call(f"p{i}", "plan.update") for i in range(3)]
    assert [result[0] for result in asyncio.run(_run_calls(registry, calls, _ctx()))] == [
        "p0", "p1", "p2",
    ]
    assert maximum == 1


def test_read_failure_drains_siblings_without_running_a_later_mutation():
    registry = ToolRegistry()
    sibling_started = asyncio.Event()
    cancelled = []
    writes = []

    async def read(call, ctx):
        if call.id == "fail":
            await sibling_started.wait()
            raise api_provider.RequestCancelledError("stopped")
        try:
            sibling_started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(call.id)
            raise
        return ToolResult(content="unreachable")

    async def write(call, ctx):
        writes.append(call.id)
        return ToolResult(content="bad")

    _register(registry, "probe.read", read, parallel_safe=True)
    _register(registry, "probe.write", write, approval="once")

    with pytest.raises(api_provider.RequestCancelledError):
        asyncio.run(_run_calls(registry, [
            _call("fail", "probe.read"),
            _call("slow", "probe.read"),
            _call("write", "probe.write"),
        ], _ctx()))
    assert cancelled == ["slow"]
    assert writes == []


@pytest.mark.parametrize("scopes, approval", [
    ({FS_WRITE}, "auto"),
    ({GRAPH_MUTATE}, "auto"),
    ({FS_READ}, "once"),
])
def test_parallel_safe_opt_in_rejects_mutating_scopes_or_approval(scopes, approval):
    registry = ToolRegistry()

    async def handler(call, ctx):
        return ToolResult(content="ok")

    with pytest.raises(ValueError, match="Parallel-safe"):
        registry.register(
            ToolSpec(name="bad", description="bad", input_schema={"type": "object"}),
            handler, scopes=scopes, approval=approval, parallel_safe=True,
        )


def test_harness_transcript_and_next_model_turn_preserve_requested_order(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path / "workspaces")
    document = SceneDocument()
    node = document.add_harness_node(0, 0, "read multiple facts")
    registry = ToolRegistry()

    async def read(call, ctx):
        await asyncio.sleep(0.02 if call.id == "first" else 0.001)
        return ToolResult(content=call.id)

    _register(registry, "probe.read", read, parallel_safe=True)
    turns_seen = []

    def fake_turn(_task, messages, tools=(), **kwargs):
        turns_seen.append(list(messages))
        if len(turns_seen) == 1:
            return {
                "message": {"role": "assistant", "content": "reading"},
                "tool_calls": [
                    _call("first", "probe.read"),
                    _call("second", "probe.read"),
                    _call("third", "probe.read"),
                ],
                "usage": None,
            }
        return {
            "message": {"role": "assistant", "content": "done"},
            "tool_calls": [], "usage": None,
        }

    monkeypatch.setattr(api_provider, "chat_turn_with_tools", fake_turn)

    class Dispatcher:
        def __init__(self):
            self._runs = RunRegistry()

    class Bus:
        async def publish(self, topic):
            pass

    dispatcher = Dispatcher()
    cancel_event = threading.Event()
    handle = dispatcher._runs.claim("harness", node_id=node.id, cancel_event=cancel_event)

    async def drive():
        try:
            await run_harness(
                document=document, dispatcher=dispatcher, registry=registry, bus=Bus(),
                notifications=None, harness_node_id=node.id, user_text="read all",
                request_id=handle.request_id, handle=handle, cancel_event=cancel_event,
            )
        finally:
            dispatcher._runs.release(handle.request_id)

    asyncio.run(drive())
    assert node.state.harness_status == "done"
    assert node.state.harness_reply == "done"
    ids = ["first", "second", "third"]
    expected_tools = [
        {"role": "tool", "tool_call_id": identifier, "name": "probe.read", "content": identifier}
        for identifier in ids
    ]
    assert [m for m in turns_seen[1] if m["role"] == "tool"] == expected_tools
    transcript = load_messages(ensure_workspace(node.state.harness_workspace_id))
    assert [m for m in transcript if m["role"] == "tool"] == expected_tools
    assert [m["tool"] for m in node.state.harness_activity] == ["probe.read"] * 3
