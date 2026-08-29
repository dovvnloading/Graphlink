"""H4: harness subagents. Lean by directive - the load-bearing behaviors
only: the read-only preset, the depth-1 + no-mutation scope filter, and
the end-to-end spawn through the parent loop."""

from __future__ import annotations

import asyncio
import time

import pytest

import api_provider
from backend.domain.graph import SceneDocument
from backend.harness import workspace as workspace_module
from backend.harness.loop import HarnessRunContext, run_harness
from backend.harness.subagents import (
    SUBAGENT_SCOPES,
    register_subagent_tool,
    run_subagent,
)
from backend.harness.tools_fs import register_harness_fs_tools
from backend.harness.tools_shell import register_harness_shell_tool
from backend.harness.workspace import ensure_workspace
from backend.providers.base import ToolCall
from backend.run_lifecycle import RunRegistry
from backend.tools import ToolRegistry


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "harness_root"
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", root)
    return root


def call(cid, name, **arguments):
    return ToolCall(id=cid, name=name, arguments=arguments)


def scripted(monkeypatch, turns):
    """A single sequential script: the parent and any subagent share it,
    because the subagent runs synchronously inside the parent's tool
    dispatch, so chat_turn_with_tools calls fire in a fixed order."""
    remaining = list(turns)
    seen_specs = []

    def fake_turn(task, messages, tools=(), **kwargs):
        seen_specs.append([t.name for t in tools])
        entry = remaining.pop(0)
        return {
            "message": {"content": entry.get("content", ""), "role": "assistant"},
            "tool_calls": entry.get("tool_calls", []),
            "usage": None,
        }

    monkeypatch.setattr(api_provider, "chat_turn_with_tools", fake_turn)
    return seen_specs


def full_registry():
    registry = ToolRegistry()
    register_harness_fs_tools(registry)
    register_harness_shell_tool(registry)
    register_subagent_tool(registry)
    return registry


def test_subagent_is_offered_only_read_tools_no_write_shell_or_spawn(workspace_root, monkeypatch):
    """The one filter that enforces both invariants: read-only (no
    write/shell) AND depth-1 (no nested spawn)."""
    ensure_workspace("ws1")
    (ensure_workspace("ws1") / "a.txt").write_text("findable content", encoding="utf-8")
    registry = full_registry()
    seen = scripted(monkeypatch, [
        {"content": "", "tool_calls": [call("s1", "fs.read", path="a.txt")]},
        {"content": "a.txt contains findable content"},
    ])
    answer = asyncio.run(run_subagent(registry=registry, workspace_dir=ensure_workspace("ws1"), task="what is in a.txt?"))
    assert answer == "a.txt contains findable content"
    offered = set(seen[0])
    assert {"fs.read", "fs.list", "fs.grep"} <= offered
    assert offered.isdisjoint({"fs.write", "fs.edit", "shell.exec", "subagent.spawn"})
    assert SUBAGENT_SCOPES == frozenset({"fs.read", "knowledge.read"})


def test_a_write_attempt_by_the_subagent_is_denied_by_scope(workspace_root, monkeypatch):
    ensure_workspace("ws1")
    registry = full_registry()
    # The subagent tries to write despite not being offered the tool; the
    # scope gate refuses it, and the child sees the denial as feedback.
    scripted(monkeypatch, [
        {"content": "", "tool_calls": [call("s1", "fs.write", path="x.txt", content="nope")]},
        {"content": "I could not write; I can only read."},
    ])
    answer = asyncio.run(run_subagent(registry=registry, workspace_dir=ensure_workspace("ws1"), task="try to write"))
    assert "only read" in answer
    assert not (ensure_workspace("ws1") / "x.txt").exists()


def test_turn_limit_returns_the_last_text_flagged(workspace_root, monkeypatch):
    ensure_workspace("ws1")
    registry = full_registry()
    scripted(monkeypatch, [
        {"content": "still looking", "tool_calls": [call("s1", "fs.list")]},
        {"content": "still looking", "tool_calls": [call("s2", "fs.list")]},
    ])
    answer = asyncio.run(
        run_subagent(registry=registry, workspace_dir=ensure_workspace("ws1"), task="loop", max_turns=2)
    )
    assert "turn limit" in answer


def test_a_hung_model_call_trips_the_watchdog_instead_of_hanging_the_parent(
    workspace_root, monkeypatch,
):
    """The parent is blocked inside registry.invoke while a subagent runs,
    so the parent's own wait_for is not covering the child's model call. A
    provider that accepts the connection and never answers would otherwise
    hang the whole run with Stop as the only way out."""
    from backend import agents as agents_module

    ensure_workspace("ws1")
    registry = full_registry()
    monkeypatch.setattr(agents_module, "WATCHDOG_TIMEOUT_SECONDS", 0.2)

    def never_answers(task, messages, tools=(), **kwargs):
        # Sleeps well past the (shrunk) watchdog. It cannot be interrupted -
        # to_thread has no cancellation - so the elapsed check below is taken
        # INSIDE the loop, where the watchdog's own effect is visible;
        # asyncio.run itself still joins this thread on the way out.
        time.sleep(3)
        raise AssertionError("the watchdog should have fired long before this")

    monkeypatch.setattr(api_provider, "chat_turn_with_tools", never_answers)

    async def go():
        ctx = HarnessRunContext(
            granted_scopes=frozenset({"provider.call"}),
            request_approval=None,
            harness_workspace_id="ws1",
        )
        started = time.monotonic()
        result = await registry.invoke(call("1", "subagent.spawn", task="investigate"), ctx)
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(go())
    assert elapsed < 2, "the spawn returns on the watchdog, not on the hung call"
    assert result.is_error and "timed out" in result.content


def test_parent_loop_spawns_a_subagent_and_gets_its_summary(workspace_root, monkeypatch):
    document = SceneDocument()
    node = document.add_harness_node(0, 0, "delegate a lookup")
    ensure_workspace(node.state.harness_workspace_id)
    registry = full_registry()
    # parent turn 1 -> spawn; subagent turn 1 -> answer; parent turn 2 -> reply
    scripted(monkeypatch, [
        {"content": "", "tool_calls": [call("p1", "subagent.spawn", task="how is X wired?")]},
        {"content": "X is wired through the registry."},
        {"content": "Based on the subagent: X is wired through the registry."},
    ])

    dispatcher = type("D", (), {"_runs": RunRegistry()})()
    import threading

    cancel = threading.Event()
    handle = dispatcher._runs.claim("harness", node_id=node.id, cancel_event=cancel)

    class Bus:
        async def publish(self, _topic):
            pass

    async def go():
        try:
            await run_harness(
                document=document, dispatcher=dispatcher, registry=registry,
                bus=Bus(), notifications=None, harness_node_id=node.id,
                user_text="do it", request_id=handle.request_id, handle=handle,
                cancel_event=cancel,
            )
        finally:
            dispatcher._runs.release(handle.request_id)

    asyncio.run(go())
    assert node.state.harness_status == "done"
    assert "wired through the registry" in node.state.harness_reply
    # The spawn shows up as one activity row - the parent's own record.
    assert any(row["tool"] == "subagent.spawn" for row in node.state.harness_activity)


def test_spawn_tool_requires_a_workspace_and_a_task(workspace_root):
    registry = full_registry()

    async def go():
        ctx = HarnessRunContext(
            granted_scopes=frozenset({"provider.call"}), request_approval=None, harness_workspace_id="ws1",
        )
        blank = await registry.invoke(call("1", "subagent.spawn", task="  "), ctx)
        ctx.harness_workspace_id = None
        unbound = await registry.invoke(call("2", "subagent.spawn", task="real"), ctx)
        return blank, unbound

    blank, unbound = asyncio.run(go())
    assert blank.is_error and "non-empty task" in blank.content
    assert unbound.is_error and "workspace" in unbound.content
