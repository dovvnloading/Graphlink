"""Offline end-to-end workspace-agent journeys using a scripted test model.

These run in CI's ordinary backend pytest job. The model is the only fake:
the harness loop, tool registry, approvals, bounded workspace, subprocess,
PDF reader, graph commands, chart publication, and persisted transcript are real.
No configured provider, credentials, network or LLM judge is required.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import api_provider
import graphlink_task_config as config
import pytest
from backend import agents as agents_module
from backend.domain.graph import SceneDocument
from backend.harness import workspace as workspace_module
from backend.harness.loop import run_harness
from backend.harness.tools_fs import register_harness_fs_tools
from backend.harness.tools_publish import register_workspace_tools
from backend.harness.tools_shell import register_harness_shell_tool
from backend.harness.transcript import load_messages
from backend.harness.workspace import ensure_workspace
from backend.providers.base import ProviderCapabilities, ProviderEvent, ToolCall
from backend.run_lifecycle import RunRegistry
from backend.tools import ToolRegistry
from backend.tools_graph import register_graph_tools, register_run_node_tool
from graphlink_model_catalog import ModelRef


def _call(call_id, name, **arguments):
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _result(request, call_id):
    """Read an actual earlier tool result fed back to the scripted model."""
    matches = [
        message for message in request.messages
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id
    ]
    assert len(matches) == 1, (call_id, request.messages)
    return matches[0]["content"]


class _ScriptedTestModel:
    """One deterministic response per turn; also checks native/JSON transport."""

    def __init__(self, turns, *, json_tools=False):
        self.turns = list(turns)
        self.json_tools = json_tools
        self.requests = []
        self.model_id = "scripted-workspace-test"
        self.capabilities = ProviderCapabilities(
            streaming=True, tools=not json_tools, structured_output=json_tools,
        )

    def stream(self, request, cancel):
        self.requests.append(request)
        assert self.turns, "the agent requested more model turns than scripted"
        step = self.turns.pop(0)
        if callable(step):
            step = step(request)
        calls = step.get("calls", ())
        if self.json_tools:
            assert not request.tools, "JSON fallback must not send native tool declarations"
            answer = json.dumps({
                "tool_calls": [
                    {"name": call.name, "arguments_json": json.dumps(call.arguments)}
                    for call in calls
                ],
                "final_answer": step.get("answer", ""),
            })
            yield ProviderEvent("done", answer, usage={"prompt_tokens": 12, "completion_tokens": 8})
            return

        available = {spec.name for spec in request.tools}
        for call in calls:
            # Real cloud adapters translate dotted names at the provider boundary.
            wire_name = call.name.replace(".", "_")
            assert wire_name in available, (wire_name, available)
            yield ProviderEvent(
                "tool_call",
                tool_call=ToolCall(id=call.id, name=wire_name, arguments=call.arguments),
            )
        yield ProviderEvent(
            "done", step.get("answer", ""), usage={"prompt_tokens": 12, "completion_tokens": 8},
        )


class _Bus:
    def __init__(self):
        self.topics = []

    async def publish(self, topic):
        self.topics.append(topic)


class _Dispatcher:
    def __init__(self):
        self._runs = RunRegistry()


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    # Never bind a scenario to the developer's live workspace or settings.
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path / "agent-workspaces")
    document = SceneDocument()
    agent = document.add_harness_node(20, 40, "scripted scenario")
    agent.state.harness_max_turns = 12
    workspace = ensure_workspace(agent.state.harness_workspace_id)
    dispatcher = _Dispatcher()
    registry = ToolRegistry()
    register_harness_fs_tools(registry)
    register_harness_shell_tool(registry)
    register_graph_tools(registry, document)
    register_run_node_tool(registry, document, dispatcher)
    register_workspace_tools(registry, dispatcher, document)
    return SimpleNamespace(
        document=document, agent=agent, workspace=workspace,
        dispatcher=dispatcher, registry=registry, bus=_Bus(),
    )


def _drive(scenario, monkeypatch, turns, *, approvals=(), json_tools=False):
    """Run a real harness task, resolving precisely the scripted human decisions."""
    model = _ScriptedTestModel(turns, json_tools=json_tools)
    monkeypatch.setattr(api_provider, "_provider_for_model_ref", lambda _ref, _state: model)
    provider = config.LOCAL_PROVIDER_LLAMACPP if json_tools else "DeepSeek"
    expected_approvals = list(approvals)

    async def main():
        cancel_event = threading.Event()
        handle = scenario.dispatcher._runs.claim(
            "harness", node_id=scenario.agent.id, cancel_event=cancel_event,
        )
        task = asyncio.create_task(run_harness(
            document=scenario.document, dispatcher=scenario.dispatcher,
            registry=scenario.registry, bus=scenario.bus, notifications=None,
            harness_node_id=scenario.agent.id, user_text="Complete the scenario.",
            request_id=handle.request_id, handle=handle, cancel_event=cancel_event,
            model_ref=ModelRef(provider, model.model_id),
        ))
        seen_approvals = []
        cursor = 0
        deadline = asyncio.get_running_loop().time() + 30
        try:
            while not task.done():
                future = handle.approval_future
                if scenario.agent.state.harness_awaiting_approval and future is not None and not future.done():
                    if cursor >= len(expected_approvals):
                        future.set_result(False)
                        raise AssertionError(
                            f"unexpected approval request: {scenario.agent.state.harness_approval_tool_name}"
                        )
                    expected_tool, decision = expected_approvals[cursor]
                    actual_tool = scenario.agent.state.harness_approval_tool_name
                    assert actual_tool == expected_tool, (actual_tool, expected_tool)
                    seen_approvals.append((actual_tool, scenario.agent.state.harness_approval_summary))
                    future.set_result(decision)
                    cursor += 1
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("workspace-agent scenario exceeded 30 seconds")
                await asyncio.sleep(0.002)
            await task
            assert cursor == len(expected_approvals), (seen_approvals, expected_approvals)
            return handle.request_id, seen_approvals
        finally:
            if not task.done():
                cancel_event.set()
                if handle.approval_future is not None and not handle.approval_future.done():
                    handle.approval_future.set_result(False)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            scenario.dispatcher._runs.release(handle.request_id)

    run_id, seen_approvals = asyncio.run(main())
    assert scenario.agent.state.harness_status == "done", scenario.agent.state.harness_status_detail
    assert scenario.agent.state.harness_awaiting_approval is False
    assert scenario.agent.pending_request_id is None
    assert not model.turns, "unused scripted model turns"
    assert scenario.agent.state.harness_spent_turns == len(model.requests)
    return SimpleNamespace(
        id=run_id, approvals=seen_approvals, scripted_model=model,
        transcript=load_messages(scenario.workspace),
    )


def _activity(scenario):
    return [(entry["tool"], entry["outcome"]) for entry in scenario.agent.state.harness_activity]


def test_analyse_csv_and_publish_a_native_chart(scenario, monkeypatch):
    csv_text = "Month,Revenue\nJan,7\nFeb,13\nMar,10\n"
    (scenario.workspace / "revenue.csv").write_bytes(csv_text.encode("utf-8"))
    chart_inputs = []

    def chart_model(source, chart_type, cancel_event=None):
        chart_inputs.append((source, chart_type))
        assert source == csv_text
        assert chart_type == "bar"
        return {
            "type": "bar", "title": "Monthly revenue",
            "labels": ["Jan", "Feb", "Mar"], "values": [7, 13, 10],
            "xAxis": "Month", "yAxis": "Revenue",
        }

    monkeypatch.setattr(agents_module, "_call_chart_agent", chart_model)

    def create_source(request):
        assert csv_text.rstrip("\n") in _result(request, "read_csv")
        return {"calls": [_call("source", "graph.create_node", kind="note", content=csv_text)]}

    def publish_chart(request):
        source_id = json.loads(_result(request, "source"))["node_id"]
        assert source_id in scenario.document.nodes
        return {"calls": [
            _call("chart", "run_node", node_id=source_id, action="chart", chart_type="bar"),
        ]}

    def finish(request):
        chart_id = json.loads(_result(request, "chart"))["chart_node_id"]
        assert scenario.document.nodes[chart_id].kind == "chart"
        return {"answer": "Published a bar chart of monthly revenue."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("read_csv", "fs.read", path="revenue.csv")]},
        create_source, publish_chart, finish,
    ], approvals=[("graph.create_node", True), ("run_node", True)])

    sources = [n for n in scenario.document.nodes.values() if n.kind == "note"]
    charts = [n for n in scenario.document.nodes.values() if n.kind == "chart"]
    assert len(sources) == len(charts) == 1
    source, chart = sources[0], charts[0]
    assert source.content == csv_text
    assert chart.state.chart_data["type"] == "bar"
    assert chart.state.chart_data["labels"] == ["Jan", "Feb", "Mar"]
    assert chart.state.chart_data["values"] == [7, 13, 10]
    assert any(edge.source == source.id and edge.target == chart.id for edge in scenario.document.edges.values())
    assert chart_inputs == [(csv_text, "bar")]
    assert [(command.run_id, command.provenance) for command in scenario.document.command_log] == [
        (run.id, "agent"), (run.id, "agent"),
    ]
    assert _activity(scenario) == [
        ("fs.read", "ok"), ("graph.create_node", "ok"), ("run_node", "ok"),
    ]
    assert scenario.agent.state.harness_reply == "Published a bar chart of monthly revenue."


def test_fix_failing_sample_project_test_and_re_run_it(scenario, monkeypatch):
    project = scenario.workspace / "sample_project"
    project.mkdir()
    source = project / "calculator.py"
    source.write_bytes(b"def double(value):\n    return value + 1\n")
    (project / "test_calculator.py").write_bytes(
        b"import unittest\nfrom calculator import double\n\n"
        b"class CalculatorTests(unittest.TestCase):\n"
        b"    def test_double(self):\n"
        b"        self.assertEqual(double(3), 6)\n"
    )
    test_command = "python -m unittest discover -s sample_project"

    def fix_after_failure(request):
        assert "return value + 1" in _result(request, "inspect")
        assert "self.assertEqual(double(3), 6)" in _result(request, "inspect_test")
        failed = _result(request, "failing_test")
        assert "exit code 1" in failed and "FAILED" in failed, failed
        return {"calls": [_call(
            "fix", "fs.edit", path="sample_project/calculator.py",
            old_string="return value + 1", new_string="return value * 2",
        )]}

    def verify(request):
        result = _result(request, "fix")
        assert "edit" in result.lower() or "replac" in result.lower(), result
        return {"calls": [_call("passing_test", "shell.exec", command=test_command)]}

    def finish(request):
        passed = _result(request, "passing_test")
        assert "exit code 0" in passed and "OK" in passed, passed
        return {"answer": "Fixed double() and verified the sample test passes."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [
            _call("inspect", "fs.read", path="sample_project/calculator.py"),
            _call("inspect_test", "fs.read", path="sample_project/test_calculator.py"),
            _call("failing_test", "shell.exec", command=test_command),
        ]},
        fix_after_failure, verify, finish,
    ], approvals=[("shell.exec", True), ("fs.edit", True), ("shell.exec", True)])

    assert source.read_bytes() == b"def double(value):\n    return value * 2\n"
    assert [tool for tool, _summary in run.approvals] == ["shell.exec", "fs.edit", "shell.exec"]
    assert _activity(scenario) == [
        ("fs.read", "ok"), ("fs.read", "ok"),
        ("shell.exec", "error"), ("fs.edit", "ok"), ("shell.exec", "ok"),
    ]
    assert [m["name"] for m in run.transcript if m["role"] == "tool"] == [
        "fs.read", "fs.read", "shell.exec", "fs.edit", "shell.exec",
    ]
    assert scenario.agent.state.harness_reply.startswith("Fixed double()")


def _write_pdf(path: Path):
    """Generate a tiny real two-page PDF, without binary fixture drift."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for sentence in ("Revenue increased nine percent.", "Operating costs remained flat."):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        contents = DecodedStreamObject()
        contents.set_data(f"BT /F1 12 Tf 40 720 Td ({sentence}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(contents)
    writer.write(path)


def test_read_pdf_and_summarise_into_a_canvas_note(scenario, monkeypatch):
    _write_pdf(scenario.workspace / "report.pdf")
    summary = "Report summary: revenue increased 9%; operating costs were flat."

    def create_summary(request):
        extracted = _result(request, "read_pdf")
        assert "[Page 1]" in extracted and "[Page 2]" in extracted
        assert "Revenue increased nine percent." in extracted
        assert "Operating costs remained flat." in extracted
        return {"calls": [_call("note", "graph.create_node", kind="note", content=summary)]}

    def finish(request):
        note_id = json.loads(_result(request, "note"))["node_id"]
        assert scenario.document.nodes[note_id].content == summary
        return {"answer": "Summarised report.pdf into a note."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("read_pdf", "fs.read", path="report.pdf")]},
        create_summary, finish,
    ], approvals=[("graph.create_node", True)])

    notes = [n for n in scenario.document.nodes.values() if n.kind == "note"]
    assert len(notes) == 1 and notes[0].content == summary
    assert _activity(scenario) == [("fs.read", "ok"), ("graph.create_node", "ok")]
    assert scenario.document.command_log[-1].run_id == run.id
    assert scenario.document.command_log[-1].provenance == "agent"
    assert "Revenue increased nine percent." in next(
        m["content"] for m in run.transcript if m.get("role") == "tool"
    )


def test_execute_full_task_through_constrained_json_tool_calls(scenario, monkeypatch):
    (scenario.workspace / "task.txt").write_bytes(b"Release status: ready for review.\n")

    def finish(request):
        # The real JSON protocol changes tool-role messages into a bounded
        # user message for models with no native function calling.
        assert request.tools == ()
        assert [m["role"] for m in request.messages][-2:] == ["assistant", "user"]
        results = json.loads(request.messages[-1]["content"].split("\n", 1)[1])
        assert len(results) == 1
        assert "ready for review" in results[0]["content"]
        return {"answer": "The release is ready for review."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("read_task", "fs.read", path="task.txt")]},
        finish,
    ], json_tools=True)

    assert len(run.scripted_model.requests) == 2
    first = run.scripted_model.requests[0]
    assert first.tools == ()
    assert first.extra_kwargs["response_format"]["type"] == "json_object"
    assert "WORKSPACE JSON TOOL PROTOCOL" in first.messages[0]["content"]
    assert scenario.agent.state.harness_reply == "The release is ready for review."
    assert _activity(scenario) == [("fs.read", "ok")]
    tool_messages = [m for m in run.transcript if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"].startswith("json_")
    assert "ready for review" in tool_messages[0]["content"]
    assert run.approvals == []


def test_denied_approval_is_reported_without_changing_files(scenario, monkeypatch):
    target = scenario.workspace / "locked.txt"
    target.write_bytes(b"original\n")

    def ask_to_overwrite(request):
        assert "original" in _result(request, "read_locked")
        return {"calls": [_call("overwrite", "fs.write", path="locked.txt", content="changed\n")]}

    def finish_after_denial(request):
        result = _result(request, "overwrite")
        assert "denied approval" in result, result
        assert target.read_bytes() == b"original\n"
        return {"answer": "Approval was denied, so I left locked.txt unchanged."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("read_locked", "fs.read", path="locked.txt")]},
        ask_to_overwrite, finish_after_denial,
    ], approvals=[("fs.write", False)])

    assert target.read_bytes() == b"original\n"
    assert len(run.approvals) == 1
    assert run.approvals[0][0] == "fs.write"
    assert "changed" in run.approvals[0][1], "the proposed write must be disclosed"
    assert _activity(scenario) == [("fs.read", "ok"), ("fs.write", "error")]
    assert not scenario.document.command_log
    assert set(scenario.document.nodes) == {scenario.agent.id}
    assert scenario.agent.state.harness_reply == "Approval was denied, so I left locked.txt unchanged."
    denied = [m for m in run.transcript if m["role"] == "tool" and m["name"] == "fs.write"]
    assert len(denied) == 1 and "denied approval" in denied[0]["content"]
