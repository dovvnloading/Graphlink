"""Offline end-to-end workspace-agent journeys using a scripted test model.

These run in CI's ordinary backend pytest job. The model is the only fake:
the harness loop, tool registry, approvals, bounded workspace, subprocess,
PDF reader, graph commands, chart publication, and persisted transcript are real.
No configured provider, credentials, network or LLM judge is required.
"""

from __future__ import annotations

import asyncio
import io
import json
import shlex
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import api_provider
import graphlink_task_config as config
import pytest
from backend import agents as agents_module
from backend.agent_dispatch.core import DispatcherCoreOps
from backend.agent_dispatch.harness import HarnessDispatchOps
from backend.domain.graph import SceneDocument
from backend.harness import skills as skills_module
from backend.harness import tools_web
from backend.harness import workspace as workspace_module
from backend.harness.loop import run_harness
from backend.harness.tools_fs import register_harness_fs_tools
from backend.harness.tools_publish import register_workspace_tools
from backend.harness.tools_shell import register_harness_shell_tool
from backend.harness.tools_skills import register_workspace_tools as register_skill_tools
from backend.harness.tools_transcript import register_workspace_tools as register_transcript_tools
from backend.harness.tools_web import register_workspace_tools as register_web_tools
from backend.harness.transcript import load_messages
from backend.harness.workspace import ensure_workspace
from backend.providers.base import ProviderCapabilities, ProviderEvent, ToolCall
from backend.run_lifecycle import RunRegistry
from backend.tools import ToolRegistry
from backend.tools_graph import register_graph_tools, register_run_node_tool
from graphlink_model_catalog import ModelRef
from graphlink_plugins.web_research import service as research_service_module
from graphlink_plugins.web_research.domain import FetchedDocument, FetchedPayload, SearchResult


def _call(call_id, tool_name, **arguments):
    return ToolCall(id=call_id, name=tool_name, arguments=arguments)


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

    def __init__(self, turns, *, json_tools=False, vision=False):
        self.turns = list(turns)
        self.json_tools = json_tools
        self.requests = []
        self.model_id = "scripted-workspace-test"
        self.capabilities = ProviderCapabilities(
            streaming=True, tools=not json_tools, structured_output=json_tools,
            vision=vision,
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
    _resolve_approval = DispatcherCoreOps._resolve_approval
    approve_code_execution = DispatcherCoreOps.approve_code_execution
    approve_harness_tool_for_session = HarnessDispatchOps.approve_harness_tool_for_session
    approve_harness_network = HarnessDispatchOps.approve_harness_network

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


def _drive(
    scenario, monkeypatch, turns, *, approvals=(), json_tools=False,
    vision=False, settings_manager=None, timeout_seconds=30,
    replay_first_approval=False, session_offers=None, before_approval=None,
):
    """Run a real harness task, resolving precisely the scripted human decisions."""
    model = _ScriptedTestModel(turns, json_tools=json_tools, vision=vision)
    monkeypatch.setattr(api_provider, "_provider_for_model_ref", lambda _ref, _state: model)
    provider = config.LOCAL_PROVIDER_LLAMACPP if json_tools else "DeepSeek"
    expected_approvals = list(approvals)
    spent_before = scenario.agent.state.harness_spent_turns

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
            settings_manager=settings_manager,
            permission_mode=scenario.agent.state.harness_permission_mode,
            execution_profile=scenario.agent.state.harness_execution_profile,
        ))
        seen_approvals = []
        first_token = None
        cursor = 0
        deadline = asyncio.get_running_loop().time() + timeout_seconds
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
                    current_token = scenario.agent.state.harness_approval_id
                    seen_approvals.append((actual_tool, scenario.agent.state.harness_approval_summary))
                    if replay_first_approval:
                        assert current_token, "each prompt needs a nonempty identity"
                        if first_token is None:
                            first_token = current_token
                        else:
                            assert current_token != first_token
                            assert not scenario.dispatcher.approve_code_execution(handle.request_id, first_token)
                            assert not scenario.dispatcher.approve_code_execution(handle.request_id, "")
                            assert not future.done(), "a stale response resolved the later approval"
                    if session_offers is not None:
                        assert scenario.agent.state.harness_approval_session_offered is session_offers[cursor]
                        if not session_offers[cursor]:
                            # An intent forged despite the missing UI option
                            # must not persist an unusable grant or resolve consent.
                            assert not scenario.dispatcher.approve_harness_tool_for_session(
                                handle.request_id, current_token,
                            )
                            assert not future.done()
                    if before_approval is not None:
                        before_approval(cursor, actual_tool, scenario.agent.state.harness_approval_summary)
                    if decision == "shell_rule":
                        assert scenario.agent.state.harness_approval_session_offered
                        assert scenario.dispatcher.approve_harness_tool_for_session(
                            handle.request_id, scenario.agent.state.harness_approval_id,
                        )
                    elif decision == "network_once":
                        assert scenario.agent.state.harness_approval_network_offered
                        assert scenario.dispatcher.approve_harness_network(
                            handle.request_id, scenario.agent.state.harness_approval_id,
                        )
                    elif replay_first_approval and decision is True:
                        assert scenario.dispatcher.approve_code_execution(handle.request_id, current_token)
                    else:
                        future.set_result(decision)
                    cursor += 1
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError(f"workspace-agent scenario exceeded {timeout_seconds} seconds")
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
    assert scenario.agent.state.harness_spent_turns - spent_before == len(model.requests)
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
    # CPython's timestamp-based .pyc cache checks whole-second mtime and size.
    # These two unittest subprocesses can run within the same second, so an
    # equal-length edit (+ 1 -> * 2) could rerun the buggy cached bytecode.
    # Change the file size as part of the repair to force recompilation.
    broken_source = b"def double(value):\n    return value + 1  # bug\n"
    fixed_source = b"def double(value):\n    return value * 2\n"
    assert len(broken_source) != len(fixed_source)
    source.write_bytes(broken_source)
    (project / "test_calculator.py").write_bytes(
        b"import unittest\nfrom calculator import double\n\n"
        b"class CalculatorTests(unittest.TestCase):\n"
        b"    def test_double(self):\n"
        b"        self.assertEqual(double(3), 6)\n"
    )
    # Run unittest with the current interpreter, never a PATH alias.
    # Windows cmd forwards Python's exit status without an extra PowerShell
    # -Command/exit wrapper, while still exercising the real shell.exec tool.
    if sys.platform == "win32":
        scenario.agent.state.harness_shell = "cmd"
        test_command = f'"{sys.executable}" -m unittest discover -s sample_project'
    else:
        test_command = f"{shlex.quote(sys.executable)} -m unittest discover -s sample_project"

    # Check the recorded tool results outside the provider callback, so a
    # failed expectation reports the actual shell output rather than being
    # obscured as an "Unrecoverable provider error" by the model adapter.
    observed = {}

    def fix_after_failure(request):
        observed["inspect"] = _result(request, "inspect")
        observed["inspect_test"] = _result(request, "inspect_test")
        observed["failed"] = _result(request, "failing_test")
        return {"calls": [_call(
            "fix", "fs.edit", path="sample_project/calculator.py",
            old_string="return value + 1  # bug", new_string="return value * 2",
        )]}

    def verify(request):
        observed["fix"] = _result(request, "fix")
        return {"calls": [_call("passing_test", "shell.exec", command=test_command)]}

    def finish(request):
        observed["passed"] = _result(request, "passing_test")
        return {"answer": "Fixed double() and verified the sample test passes."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [
            _call("inspect", "fs.read", path="sample_project/calculator.py"),
            _call("inspect_test", "fs.read", path="sample_project/test_calculator.py"),
            _call("failing_test", "shell.exec", command=test_command),
        ]},
        fix_after_failure, verify, finish,
    ], approvals=[("shell.exec", True), ("fs.edit", True), ("shell.exec", True)])

    assert "return value + 1" in observed["inspect"]
    assert "self.assertEqual(double(3), 6)" in observed["inspect_test"]
    assert observed["failed"].startswith("exit code 1"), observed["failed"]
    assert "edit" in observed["fix"].lower() or "replac" in observed["fix"].lower(), observed["fix"]
    assert observed["passed"].startswith("exit code 0"), observed["passed"]
    assert source.read_bytes() == fixed_source
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


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell host scenario runs on Windows")
def test_powershell_shell_exec_from_scripted_agent(scenario, monkeypatch):
    scenario.agent.state.harness_shell = "powershell"
    observed = {}

    def finish(request):
        observed["output"] = _result(request, "powershell_output")
        return {"answer": "PowerShell command completed."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("powershell_output", "shell.exec", command="Write-Output POWERSHELL_READY")]},
        finish,
    ], approvals=[("shell.exec", True)])

    assert observed["output"].startswith("exit code 0\n")
    assert "POWERSHELL_READY" in observed["output"]
    assert _activity(scenario) == [("shell.exec", "ok")]
    assert [tool for tool, _ in run.approvals] == ["shell.exec"]
    assert run.transcript[-1]["content"] == "PowerShell command completed."


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell host scenario runs on Windows")
def test_powershell_shell_exec_reports_native_exit_code(scenario, monkeypatch):
    scenario.agent.state.harness_shell = "powershell"
    observed = {}

    def finish(request):
        observed["output"] = _result(request, "native_exit")
        return {"answer": "The native command failed with exit code 42."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("native_exit", "shell.exec", command="cmd /c exit 42")]},
        finish,
    ], approvals=[("shell.exec", True)])

    assert observed["output"] == "exit code 42 (no output)"
    assert _activity(scenario) == [("shell.exec", "error")]
    assert [tool for tool, _ in run.approvals] == ["shell.exec"]


@pytest.mark.skipif(sys.platform == "win32", reason="Bash host scenario runs on POSIX")
def test_bash_shell_exec_from_scripted_agent(scenario, monkeypatch):
    scenario.agent.state.harness_shell = "bash"
    observed = {}

    def finish(request):
        observed["output"] = _result(request, "bash_output")
        return {"answer": "Bash command completed."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("bash_output", "shell.exec", command="printf BASH_READY")]},
        finish,
    ], approvals=[("shell.exec", True)])

    assert observed["output"].startswith("exit code 0\n")
    assert "BASH_READY" in observed["output"]
    assert _activity(scenario) == [("shell.exec", "ok")]
    assert [tool for tool, _ in run.approvals] == ["shell.exec"]
    assert run.transcript[-1]["content"] == "Bash command completed."


@pytest.mark.parametrize(
    ("tool_name", "first_args", "second_args"),
    [
        ("fs.write", {"path": "first.txt", "content": "reviewed first"},
         {"path": "second.txt", "content": "reviewed second"}),
        ("shell.session", {"action": "write", "name": "unopened", "input": "first stdin"},
         {"action": "write", "name": "unopened", "input": "second stdin"}),
    ],
)
def test_issue_730_replayed_approval_never_authorizes_the_next_prompt(
    scenario, monkeypatch, tool_name, first_args, second_args,
):
    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("one", tool_name, **first_args)]},
        {"calls": [_call("two", tool_name, **second_args)]},
        {"answer": "Both decisions were independent."},
    ], approvals=[(tool_name, True), (tool_name, True)], replay_first_approval=True)
    assert len(run.approvals) == 2
    if tool_name == "fs.write":
        assert (scenario.workspace / "first.txt").read_text() == "reviewed first"
        assert (scenario.workspace / "second.txt").read_text() == "reviewed second"


def test_issue_732_inert_git_rules_are_never_offered_or_saved(scenario, monkeypatch):
    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("git-one", "shell.exec", command="git status")]},
        {"calls": [_call("git-two", "shell.exec", command="git status")]},
        {"answer": "Both git commands required approval."},
    ], approvals=[("shell.exec", True), ("shell.exec", True)], session_offers=[False, False])
    assert len(run.approvals) == 2
    assert scenario.agent.state.harness_shell_allow_rules == []


@pytest.mark.parametrize(
    ("tool_name", "before", "external", "arguments"),
    [
        ("fs.write", None, "valuable external file",
         {"path": "report.txt", "content": "agent version"}),
        ("fs.write", "reviewed original", "external replacement",
         {"path": "report.txt", "content": "agent version"}),
        ("fs.edit", "old value\nkeep=yes\n", "old value\nkeep=external\n",
         {"path": "report.txt", "old_string": "old", "new_string": "new"}),
    ],
)
def test_issue_734_stale_file_preview_never_overwrites_external_content(
    scenario, monkeypatch, tool_name, before, external, arguments,
):
    target = scenario.workspace / "report.txt"
    if before is not None:
        target.write_text(before, encoding="utf-8")
    result_text = {}

    def change_file_while_prompt_is_pending(index, actual_tool, summary):
        assert index == 0 and actual_tool == tool_name
        if before is None:
            assert "--- new file: report.txt" in summary
        else:
            assert ("--- a/report.txt" in summary)
        target.write_text(external, encoding="utf-8")

    def finish(request):
        result_text["tool"] = _result(request, "file-mutation")
        return {"answer": "External changes were preserved."}

    _drive(scenario, monkeypatch, [
        {"calls": [_call("file-mutation", tool_name, **arguments)]},
        finish,
    ], approvals=[(tool_name, True)], before_approval=change_file_while_prompt_is_pending)
    assert "changed since approval preview" in result_text["tool"]
    assert target.read_text(encoding="utf-8") == external


def test_saved_shell_rule_survives_a_task_but_not_a_changed_command(scenario, monkeypatch):
    first = _drive(scenario, monkeypatch, [
        {"calls": [_call("remember", "shell.exec", command="echo remembered")]},
        {"answer": "Remembered the safe command."},
    ], approvals=[("shell.exec", "shell_rule")])
    assert "exit code 0" in next(m["content"] for m in first.transcript if m.get("tool_call_id") == "remember")
    assert len(scenario.agent.state.harness_shell_allow_rules) == 1
    assert scenario.agent.state.harness_shell_allow_rules[0].startswith("v2\t")

    observed = {}

    def change_command(request):
        observed["reused"] = _result(request, "reused")
        return {"calls": [_call("changed", "shell.exec", command="echo different")]}

    def finish(request):
        observed["changed"] = _result(request, "changed")
        return {"answer": "Reused only the exact saved command."}

    second = _drive(scenario, monkeypatch, [
        {"calls": [_call("reused", "shell.exec", command="echo remembered")]},
        change_command, finish,
    ], approvals=[("shell.exec", True)])
    assert "remembered" in observed["reused"]
    assert "different" in observed["changed"]
    assert len(second.approvals) == 1
    assert "echo different" in second.approvals[0][1]
    assert len(scenario.agent.state.harness_shell_allow_rules) == 1


def test_web_research_search_fetch_and_redirect_consent(scenario, monkeypatch):
    monkeypatch.setattr(tools_web, "_plugin_enabled", lambda: True)
    register_web_tools(scenario.registry, None, None)
    queries, requested = [], []
    redirected = "https://mirror.example/reference"

    class SearchProvider:
        name = "DuckDuckGo"

        def search(self, query, *, limits, token):
            token.raise_if_cancelled()
            queries.append(query)
            return [SearchResult(
                "source-1", "Research result", "https://docs.example/first",
                "https://docs.example/first", "Useful reference snippet",
            )]

    class Fetcher:
        def fetch(self, result, *, limits, token, on_request=None):
            for url in (result.canonical_url, redirected):
                token.raise_if_cancelled()
                on_request(url)
                requested.append(url)
            return FetchedPayload(
                source_id=result.source_id, requested_url=result.url,
                final_url=redirected, content_type="text/plain", body=b"fixture content",
            )

    class Extractor:
        def extract(self, payload, *, limits, token):
            token.raise_if_cancelled()
            return FetchedDocument(
                source_id=payload.source_id, title="Research reference",
                final_url=payload.final_url, content_type=payload.content_type,
                text="The verified fixture marker is BLUE_ORCHID.", truncated=False,
            )

    service = SimpleNamespace(search_provider=SearchProvider(), fetcher=Fetcher(), extractor=Extractor())
    monkeypatch.setattr(research_service_module, "WebResearchService", lambda: service)
    observed = {}

    def research_more(request):
        observed["search_one"] = _result(request, "search_one")
        return {"calls": [
            _call("search_two", "web.search", query="second research query"),
            _call("fetch_one", "web.fetch", url="https://docs.example/first"),
        ]}

    def fetch_again(request):
        observed["search_two"] = _result(request, "search_two")
        observed["fetch_one"] = _result(request, "fetch_one")
        return {"calls": [_call("fetch_two", "web.fetch", url="https://docs.example/second")]}

    def finish(request):
        observed["fetch_two"] = _result(request, "fetch_two")
        return {"answer": "The reference states BLUE_ORCHID."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("search_one", "web.search", query="first research query")]},
        research_more, fetch_again, finish,
    ], approvals=[("web.search", True), ("web.fetch", True), ("web.fetch", True)])

    assert queries == ["first research query", "second research query"]
    assert requested == [
        "https://docs.example/first", redirected,
        "https://docs.example/second", redirected,
    ]
    assert "UNTRUSTED WEB SEARCH RESULTS" in observed["search_one"]
    assert "UNTRUSTED WEB SEARCH RESULTS" in observed["search_two"]
    assert all("BLUE_ORCHID" in observed[key] for key in ("fetch_one", "fetch_two"))
    assert [tool for tool, _ in run.approvals] == ["web.search", "web.fetch", "web.fetch"]
    assert "duckduckgo.com" in run.approvals[0][1]
    assert "docs.example" in run.approvals[1][1]
    assert "mirror.example" in run.approvals[2][1]
    assert _activity(scenario) == [
        ("web.search", "ok"), ("web.search", "ok"),
        ("web.fetch", "ok"), ("web.fetch", "ok"),
    ]


def test_agent_discovers_and_loads_a_workspace_skill_before_writing(scenario, monkeypatch):
    monkeypatch.setattr(skills_module, "USER_SKILLS_DIR", scenario.workspace / "no-user-skills")
    skill_file = scenario.workspace / ".graphlink" / "skills" / "review" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: review\ndescription: Prepare a concise review file.\n---\n"
        "# Procedure\nUse the SKILL_BODY_MARKER for a review.\n", encoding="utf-8",
    )
    register_skill_tools(scenario.registry, None, None)
    observed = {}

    def load_skill(request):
        prompt = request.messages[0]["content"]
        observed["catalog"] = prompt
        return {"calls": [_call("load_review", "skills.load", source="workspace", name="review")]}

    def write_review(request):
        observed["skill"] = _result(request, "load_review")
        return {"calls": [_call("review_file", "fs.write", path="review.txt", content="SKILL_BODY_MARKER\n")]}

    def finish(request):
        observed["write"] = _result(request, "review_file")
        return {"answer": "Created review.txt after reading the skill."}

    run = _drive(scenario, monkeypatch, [load_skill, write_review, finish],
                 approvals=[("fs.write", True)])
    assert "workspace / review: Prepare a concise review file." in observed["catalog"]
    assert "SKILL_BODY_MARKER" not in observed["catalog"]
    assert "SKILL_BODY_MARKER" in observed["skill"]
    assert (scenario.workspace / "review.txt").read_text(encoding="utf-8") == "SKILL_BODY_MARKER\n"
    assert [tool for tool, _ in run.approvals] == ["fs.write"]
    assert _activity(scenario) == [("skills.load", "ok"), ("fs.write", "ok")]


def test_agent_searches_a_prior_task_transcript(scenario, monkeypatch):
    register_transcript_tools(scenario.registry, None, None)
    _drive(scenario, monkeypatch, [
        {"answer": "Previous session recorded the orchidindex decision."},
    ])
    observed = {}

    def finish(request):
        observed["result"] = _result(request, "historical_search")
        return {"answer": "Recovered the prior orchidindex decision."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("historical_search", "session.search", query="orchidindex")]},
        finish,
    ])
    assert "transcript.jsonl:" in observed["result"]
    assert "orchidindex" in observed["result"]
    assert "assistant" in observed["result"]
    assert run.approvals == []
    assert _activity(scenario)[-1] == ("session.search", "ok")


@pytest.mark.parametrize("mode,approval_names", [
    ("ask", ("fs.write", "fs.edit", "shell.exec")),
    ("auto_edit", ("shell.exec",)),
])
def test_ask_and_auto_edit_mode_journey(scenario, monkeypatch, mode, approval_names):
    scenario.agent.state.harness_permission_mode = mode
    observed = {}

    def edit(request):
        observed["written"] = _result(request, "mode_write")
        return {"calls": [_call(
            "mode_edit", "fs.edit", path="notes.txt",
            old_string="draft", new_string="reviewed",
        )]}

    def run_command(request):
        observed["edited"] = _result(request, "mode_edit")
        return {"calls": [_call("mode_shell", "shell.exec", command="echo mode_ready")]}

    def finish(request):
        observed["shell"] = _result(request, "mode_shell")
        return {"answer": "File edited and shell command checked."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("mode_write", "fs.write", path="notes.txt", content="draft")]},
        edit, run_command, finish,
    ], approvals=[(name, True) for name in approval_names])
    assert (scenario.workspace / "notes.txt").read_text(encoding="utf-8") == "reviewed"
    assert observed["shell"].startswith("exit code 0\n")
    assert "mode_ready" in observed["shell"]
    assert [name for name, _ in run.approvals] == list(approval_names)
    assert _activity(scenario) == [
        ("fs.write", "ok"), ("fs.edit", "ok"), ("shell.exec", "ok"),
    ]


def _fake_container_engine(monkeypatch):
    """Capture Docker argv without starting a daemon or contacting a network."""
    from backend.harness import shell_isolation, tools_shell

    launched = []
    real_which = shell_isolation.shutil.which
    monkeypatch.setattr(shell_isolation, "available_engine", lambda _env: "docker")
    monkeypatch.setattr(
        shell_isolation.shutil, "which",
        lambda name, **kwargs: "/fake/docker" if name == "docker" else real_which(name, **kwargs),
    )
    monkeypatch.setattr(
        tools_shell, "create_execution_guard",
        lambda: SimpleNamespace(popen_kwargs=lambda: {}, close=lambda: None),
    )

    class CompletedProcess:
        def __init__(self):
            self.stdout = io.StringIO("fake container output\n")
            self.returncode = 0

        def poll(self):
            return self.returncode

    def spawn(_guard, argv, **kwargs):
        assert kwargs["shell"] is False
        launched.append(list(argv))
        return CompletedProcess()

    monkeypatch.setattr(tools_shell, "spawn_guarded_subprocess", spawn)
    monkeypatch.setattr(tools_shell, "assign_guarded_subprocess", lambda *_args: None)
    monkeypatch.setattr(tools_shell, "cleanup_container", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "backend.harness.context.describe_machine",
        lambda *_args, **_kwargs: "Isolated fake-container test machine",
    )
    return launched


def test_autonomous_mode_skips_safe_isolated_ops_but_prompts_for_danger(scenario, monkeypatch):
    launched = _fake_container_engine(monkeypatch)
    scenario.agent.state.harness_execution_profile = "isolated"
    scenario.agent.state.harness_permission_mode = "autonomous"
    observed = {}

    def read_safe_file(request):
        observed["write"] = _result(request, "auto_write")
        return {"calls": [_call("auto_cat", "shell.exec", command="cat notes.txt")]}

    def attempt_danger(request):
        observed["read"] = _result(request, "auto_cat")
        return {"calls": [_call("auto_danger", "shell.exec", command="rm -rf notes.txt")]}

    def finish(request):
        observed["denied"] = _result(request, "auto_danger")
        return {"answer": "Safe actions ran; destructive command was denied."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("auto_write", "fs.write", path="notes.txt", content="safe content")]},
        read_safe_file, attempt_danger, finish,
    ], approvals=[("shell.exec", False)])
    assert (scenario.workspace / "notes.txt").read_text(encoding="utf-8") == "safe content"
    assert observed["read"].startswith("exit code 0\n")
    assert "denied approval" in observed["denied"]
    assert [tool for tool, _ in run.approvals] == ["shell.exec"]
    assert [argv[-1] for argv in launched] == ["cat notes.txt"]
    assert "--network=none" in launched[0]
    assert _activity(scenario) == [
        ("fs.write", "ok"), ("shell.exec", "ok"), ("shell.exec", "error"),
    ]


def test_isolated_network_needs_one_command_approval_with_fake_engine(scenario, monkeypatch):
    launched = _fake_container_engine(monkeypatch)
    scenario.agent.state.harness_execution_profile = "isolated"
    scenario.agent.state.harness_permission_mode = "autonomous"
    observed = {}

    def request_network(request):
        observed["offline"] = _result(request, "offline")
        return {"calls": [_call(
            "network_once", "shell.exec", command="curl https://docs.example/reference",
            network=True, network_reason="Read approved reference documentation",
        )]}

    def request_other_network(request):
        observed["approved"] = _result(request, "network_once")
        return {"calls": [_call(
            "network_denied", "shell.exec", command="curl https://other.example/data",
            network=True, network_reason="Read a second site's documentation",
        )]}

    def continue_offline(request):
        observed["denied"] = _result(request, "network_denied")
        return {"calls": [_call("offline_after", "shell.exec", command="echo still_offline")]}

    def finish(request):
        observed["offline_after"] = _result(request, "offline_after")
        return {"answer": "Only the first network command was approved."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("offline", "shell.exec", command="echo offline")]},
        request_network, request_other_network, continue_offline, finish,
    ], approvals=[("shell.exec", "network_once"), ("shell.exec", False)])
    assert all(observed[key].startswith("exit code 0\n") for key in (
        "offline", "approved", "offline_after",
    ))
    assert "denied approval" in observed["denied"]
    assert [tool for tool, _ in run.approvals] == ["shell.exec", "shell.exec"]
    assert "Why network is needed: Read approved reference documentation" in run.approvals[0][1]
    assert "Why network is needed: Read a second site's documentation" in run.approvals[1][1]
    assert [argv[-1] for argv in launched] == [
        "echo offline", "curl https://docs.example/reference", "echo still_offline",
    ]
    assert [next(flag for flag in argv if flag.startswith("--network=")) for argv in launched] == [
        "--network=none", "--network=bridge", "--network=none",
    ]
    for argv in launched:
        assert argv[:2] == ["/fake/docker", "run"]
        assert "--pull=never" in argv and "--cap-drop=ALL" in argv
    assert scenario.agent.state.harness_approval_network_offered is False
    assert scenario.agent.state.harness_network_always is False
    assert _activity(scenario) == [
        ("shell.exec", "ok"), ("shell.exec", "ok"),
        ("shell.exec", "error"), ("shell.exec", "ok"),
    ]
