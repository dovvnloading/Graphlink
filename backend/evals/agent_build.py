"""Deterministic, offline evaluations against the *real* Builder executor.

Only the model's tool-call turns are scripted. The graph document, command
layer, ToolRegistry approvals, Builder state machine and RunRegistry are real.
No network/code-execution tools are registered in this deliberately narrow
eval environment. Refs like "$node:create-question" resolve to the node id
returned by an earlier graph.create_node tool call, not an assumed id.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest import mock
from typing import Any

import api_provider
from backend.builder import register_builder_control_tools, run_build
from backend.domain.graph import SceneDocument
from backend.domain.node_states import PlanState
from backend.evals.fixtures import EvalFixture
from backend.providers.base import ToolCall
from backend.run_lifecycle import RunRegistry
from backend.tools import ToolRegistry
from backend.tools_graph import register_graph_tools

# No run_node, shell, code execution, web research, provider, or MCP tools.
# An intentionally unknown name is allowed for testing error feedback.
_SAFE_TOOLS = frozenset({
    "graph.create_node", "graph.connect", "graph.set_node_content",
    "graph.list_nodes", "graph.read_subgraph",
    "builder.complete_step", "builder.replan", "builder.finish_build",
    "builder.abort", "graph.unknown",
})
_REQUIRED_EXPECTATIONS = frozenset({
    "status", "run_statuses", "steps", "nodes", "edges", "activity",
    "approvals", "spent_steps", "spent_tokens",
})


class EvalScriptError(ValueError):
    """A bad fixture or an unexpected model interaction, not a Builder bug."""


def _node_reference(value: Any, messages: list[dict]) -> Any:
    if not isinstance(value, str) or not value.startswith("$node:"):
        return value
    call_id = value[len("$node:"):]
    for message in reversed(messages):
        if message.get("role") != "tool" or message.get("tool_call_id") != call_id:
            continue
        try:
            node_id = json.loads(message["content"])["node_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise EvalScriptError(f"tool {call_id!r} did not create a node") from exc
        if not isinstance(node_id, str) or not node_id:
            raise EvalScriptError(f"tool {call_id!r} returned an invalid node id")
        return node_id
    raise EvalScriptError(f"no earlier tool result for {value!r}")


class _ScriptedTurns:
    def __init__(self, turns: Any) -> None:
        if not isinstance(turns, list):
            raise EvalScriptError("runs[].turns must be a list")
        self.turns: list[dict] = turns
        self.cursor = 0
        self.fault: str | None = None

    def respond(self, task, messages, tools=(), **kwargs) -> dict:
        try:
            if self.cursor >= len(self.turns):
                raise EvalScriptError("model script exhausted before Builder stopped")
            row = self.turns[self.cursor]
            self.cursor += 1
            if not isinstance(row, dict) or not isinstance(row.get("tool_calls"), list):
                raise EvalScriptError("each turn needs a tool_calls list")
            if not isinstance(row.get("usage"), dict):
                raise EvalScriptError("each turn needs deterministic token usage")
            calls = []
            for raw in row["tool_calls"]:
                if not isinstance(raw, dict) or raw.get("name") not in _SAFE_TOOLS:
                    raise EvalScriptError("fixture requests a tool outside the offline eval allowlist")
                if not isinstance(raw.get("arguments"), dict) or not isinstance(raw.get("id"), str):
                    raise EvalScriptError("tool calls need a string id and object arguments")
                arguments = {
                    key: _node_reference(value, messages)
                    for key, value in raw["arguments"].items()
                }
                calls.append(ToolCall(id=raw["id"], name=raw["name"], arguments=arguments))
            return {
                "message": {"role": "assistant", "content": str(row.get("content", ""))},
                "tool_calls": calls,
                "usage": row["usage"],
            }
        except Exception as exc:
            self.fault = str(exc)
            raise


async def _drive_run(document, dispatcher, registry, plan, script, decisions, approvals) -> None:
    cancel_event = threading.Event()
    handle = dispatcher._runs.claim("builder", node_id=plan.id, cancel_event=cancel_event)

    async def worker() -> None:
        await run_build(
            document=document, dispatcher=dispatcher, registry=registry,
            bus=SimpleNamespace(publish=_publish), notifications=None,
            plan_node_id=plan.id, request_id=handle.request_id,
            handle=handle, cancel_event=cancel_event,
        )

    async def _publish(_topic: str) -> None:
        return None

    task = asyncio.create_task(worker())
    cursor = 0
    unexpected_approval = False
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 15.0
    try:
        while not task.done():
            if loop.time() >= deadline:
                raise EvalScriptError("Builder did not finish within 15 seconds")
            future = handle.approval_future
            if plan.state.builder_awaiting_tool_approval and future is not None and not future.done():
                approvals.append(plan.state.builder_approval_tool_name)
                if cursor >= len(decisions):
                    unexpected_approval = True
                    future.set_result(False)
                else:
                    if not isinstance(decisions[cursor], bool):
                        raise EvalScriptError("approval decisions must be booleans")
                    future.set_result(decisions[cursor])
                    cursor += 1
            await asyncio.sleep(0.001)
        await task
        if unexpected_approval or cursor != len(decisions):
            raise EvalScriptError(
                f"approval script mismatch: used {cursor} of {len(decisions)} decisions"
            )
        if script.fault:
            raise EvalScriptError(script.fault)
        if script.cursor != len(script.turns):
            raise EvalScriptError(
                f"unused model turns: consumed {script.cursor} of {len(script.turns)}"
            )
    finally:
        if not task.done():
            cancel_event.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        dispatcher._runs.release(handle.request_id)


def _sorted(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda row: json.dumps(row, sort_keys=True))


def _snapshot(document: SceneDocument, plan, approvals: list[str], statuses: list[str]) -> dict:
    nodes = [n for n in document.nodes.values() if n.kind != "plan"]
    return {
        "status": plan.state.builder_status,
        "run_statuses": statuses,
        "steps": [
            {"title": step["title"], "status": step["status"], "detail": step["detail"]}
            for step in plan.state.plan_steps
        ],
        "nodes": _sorted([{"kind": n.kind, "content": n.content} for n in nodes]),
        "edges": _sorted([
            {"from": document.nodes[e.source].content, "to": document.nodes[e.target].content}
            for e in document.edges.values()
        ]),
        "activity": [
            {"tool": entry["tool"], "outcome": entry["outcome"]}
            for entry in plan.state.builder_activity
        ],
        "approvals": approvals,
        "spent_steps": plan.state.builder_spent_steps,
        "spent_tokens": plan.state.builder_spent_tokens,
        "status_detail": plan.state.builder_status_detail,
    }


async def _execute(fixture: EvalFixture) -> dict:
    data = fixture.input
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list) or not data["steps"]:
        raise EvalScriptError("input needs a nonempty steps list")
    if not isinstance(data.get("runs"), list) or not data["runs"]:
        raise EvalScriptError("input needs a nonempty runs list")

    document = SceneDocument()
    registry = ToolRegistry()
    register_graph_tools(registry, document)
    register_builder_control_tools(registry)
    dispatcher = SimpleNamespace(_runs=RunRegistry())
    plan = document.add_plan_node(
        0, 0, str(data.get("goal") or ""), mode=str(data.get("mode", "autopilot")),
    )
    state = plan.state
    if not isinstance(state, PlanState):
        raise EvalScriptError("add_plan_node returned a node without PlanState")
    state.plan_steps = [
        {"id": f"s{i + 1}", "title": title, "status": "pending", "detail": ""}
        for i, title in enumerate(data["steps"])
    ]
    state.builder_status = "awaiting_start"
    approvals: list[str] = []
    statuses: list[str] = []

    for run in data["runs"]:
        if not isinstance(run, dict):
            raise EvalScriptError("every run must be an object")
        if "max_steps" in run:
            state.builder_max_steps = int(run["max_steps"])
        if "max_tokens" in run:
            state.builder_max_tokens = int(run["max_tokens"])
        if "max_wall_seconds" in run:
            state.builder_max_wall_seconds = int(run["max_wall_seconds"])
        script = _ScriptedTurns(run.get("turns"))
        decisions = run.get("approvals", [])
        if not isinstance(decisions, list):
            raise EvalScriptError("runs[].approvals must be a list")
        # This patch encloses every asyncio.to_thread model call and is
        # restored before any other eval fixture or caller can use a provider.
        with mock.patch.object(api_provider, "chat_turn_with_tools", side_effect=script.respond):
            await _drive_run(document, dispatcher, registry, plan, script, decisions, approvals)
        statuses.append(state.builder_status)

    return _snapshot(document, plan, approvals, statuses)


def evaluate_builder(fixture: EvalFixture) -> tuple[bool, str]:
    """Run the real Builder and score *observed* graph/state, without I/O."""
    expected = fixture.expected
    if not isinstance(expected, dict):
        raise EvalScriptError("agent_build requires an expected result object")
    missing = _REQUIRED_EXPECTATIONS - expected.keys()
    if missing:
        raise EvalScriptError(f"missing expectations: {', '.join(sorted(missing))}")
    actual = asyncio.run(_execute(fixture))
    for key, want in expected.items():
        if key == "status_detail_contains":
            if not isinstance(want, str) or want not in actual["status_detail"]:
                return False, f"status detail does not contain {want!r}: {actual['status_detail']!r}"
            continue
        got = actual.get(key)
        if key in ("nodes", "edges") and isinstance(want, list):
            want = _sorted(want)
        if got != want:
            return False, (
                f"{key} did not match the golden fixture. "
                f"actual={json.dumps(got, sort_keys=True)}; "
                f"expected={json.dumps(want, sort_keys=True, default=str)}"
            )
    return True, (
        f"real Builder matched graph, steps, approvals, activity and budgets "
        f"across {len(actual['run_statuses'])} run(s)"
    )
