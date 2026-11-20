"""Eight offline, scripted-model journeys through real workspace-agent services.

Each test uses the production harness loop/registry and checks its side effects,
approval disclosures, tool results and persisted transcript. Only model turns
are scripted; wheels, documents and images are generated in disposable roots.
"""

from __future__ import annotations

import io
import json
import shlex
import sys
import venv
import zipfile
from types import SimpleNamespace

import pytest
from docx import Document
from PIL import Image

from backend.harness import loop as loop_module
from backend.harness import python_install
from backend.harness.python_env import venv_python
from backend.harness.tools_python import PythonReplRegistry, register_harness_python_tool
from backend.harness.tools_python_install import register_harness_python_install_tool
from backend.harness.tools_scientific import register_workspace_tools as register_science_tools
from backend.harness.workspace import WorkspaceError
from backend.harness.workspace_files import list_workspace_files, read_workspace_file
from backend.plugin_sdk import HostContext
from backend.tests.test_harness_e2e_scenarios import (
    _activity, _call, _drive, _result, scenario as scenario,
)
from graphlink_plugins.scientific.workbench import register_pack


def _seed_offline_wheel(folder):
    """A valid pure-Python wheel, with no index or network dependency."""
    folder.mkdir(parents=True, exist_ok=True)
    wheel_path = folder / "graphlink_offline_fixture-1.0.0-py3-none-any.whl"
    info = "graphlink_offline_fixture-1.0.0.dist-info"
    with zipfile.ZipFile(wheel_path, "w") as wheel:
        wheel.writestr("graphlink_offline_fixture.py", "VALUE = 173\n")
        wheel.writestr(
            f"{info}/METADATA",
            "Metadata-Version: 2.1\nName: graphlink-offline-fixture\nVersion: 1.0.0\n",
        )
        wheel.writestr(
            f"{info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: graphlink-test\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr(f"{info}/RECORD", "")
    return wheel_path


def test_python_install_and_exec_share_an_offline_workspace_environment(scenario, monkeypatch, tmp_path):
    environment = scenario.workspace / ".venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    cache = tmp_path / "wheel-cache"
    wheel_path = _seed_offline_wheel(cache / "wheels")
    monkeypatch.setattr(python_install, "PYTHON_WHEEL_CACHE_ROOT", cache)

    actual_pip = python_install._run_pip
    pip_calls = []

    def offline_pip(argv, cwd, cancel_event):
        pip_calls.append(list(argv))
        assert "download" not in argv, "cached installation must never contact PyPI"
        assert "--no-index" in argv
        return actual_pip(argv, cwd, cancel_event)

    monkeypatch.setattr(python_install, "_run_pip", offline_pip)
    repls = PythonReplRegistry()
    register_harness_python_tool(scenario.registry, repls)
    register_harness_python_install_tool(scenario.registry)
    observed = {}

    def import_from_environment(request):
        observed["install"] = _result(request, "install")
        return {"calls": [_call(
            "exec", "python.exec",
            code=(
                "import graphlink_offline_fixture, sys\n"
                "print(graphlink_offline_fixture.VALUE)\n"
                "print(sys.prefix != sys.base_prefix)"
            ),
        )]}

    def finish(request):
        observed["execution"] = _result(request, "exec")
        return {"answer": "Installed a cached wheel and imported it from the workspace environment."}

    try:
        run = _drive(
            scenario, monkeypatch,
            [
                {"calls": [_call("install", "python.install", packages=["graphlink-offline-fixture==1.0.0"])]},
                import_from_environment, finish,
            ],
            approvals=[("python.install", True), ("python.exec", True)],
            timeout_seconds=90,
        )
    finally:
        repls.stop_all()

    assert "shared cached wheels" in observed["install"]
    assert observed["execution"].splitlines() == ["173", "True"]
    assert wheel_path.is_file() and pip_calls
    assert all("download" not in argv and "--no-index" in argv for argv in pip_calls)
    assert "graphlink-offline-fixture==1.0.0" in run.approvals[0][1]
    assert str(venv_python(environment)) in run.approvals[0][1]
    assert _activity(scenario) == [("python.install", "ok"), ("python.exec", "ok")]
    assert [m["name"] for m in run.transcript if m["role"] == "tool"] == [
        "python.install", "python.exec",
    ]


def test_read_a_word_document_and_image_with_a_bounded_vision_preview(scenario, monkeypatch):
    document = Document()
    document.add_heading("Specimen review", level=1)
    document.add_paragraph("Microscope image shows intact cells.")
    document.save(scenario.workspace / "specimen.docx")
    raw_image = io.BytesIO()
    Image.new("RGB", (96, 48), "navy").save(raw_image, format="PNG")
    (scenario.workspace / "specimen.png").write_bytes(raw_image.getvalue())
    observed = {}

    def summarize(request):
        observed["document"] = _result(request, "doc")
        observed["image"] = _result(request, "image")
        visual_messages = [
            message for message in request.messages
            if message["role"] == "user" and isinstance(message["content"], list)
        ]
        assert len(visual_messages) == 1
        previews = [part["data"] for part in visual_messages[0]["content"]
                    if part["type"] == "image_bytes"]
        assert len(previews) == 1 and previews[0] != raw_image.getvalue()
        with Image.open(io.BytesIO(previews[0])) as preview:
            assert preview.format == "JPEG" and preview.size == (96, 48)
        return {"answer": "Read the specimen document and the bounded image preview."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [
            _call("doc", "fs.read", path="specimen.docx"),
            _call("image", "fs.read", path="specimen.png"),
        ]},
        summarize,
    ], vision=True)

    assert "Microscope image shows intact cells." in observed["document"]
    assert "Image: PNG, 96 x 48 pixels" in observed["image"]
    assert _activity(scenario) == [("fs.read", "ok"), ("fs.read", "ok")]
    assert [m["name"] for m in run.transcript if m["role"] == "tool"] == ["fs.read", "fs.read"]
    assert all(isinstance(m.get("content"), str) for m in run.transcript)
    assert not any(isinstance(m.get("content"), list) for m in run.transcript)


def test_publish_a_workspace_report_as_a_linked_undoable_canvas_document(scenario, monkeypatch):
    contents = "# Findings\nAll checks complete.\n"
    observed = {}

    def publish(request):
        observed["write"] = _result(request, "write")
        assert (scenario.workspace / "outputs" / "summary.md").read_text(encoding="utf-8") == contents
        return {"calls": [_call("publish", "graph.publish_file", path="outputs/summary.md")]}

    def finish(request):
        observed["publication"] = json.loads(_result(request, "publish"))
        return {"answer": "Published the report on the canvas."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("write", "fs.write", path="outputs/summary.md", content=contents)]},
        publish, finish,
    ], approvals=[("fs.write", True), ("graph.publish_file", True)])

    publication = observed["publication"]
    node = scenario.document.nodes[publication["node_id"]]
    assert node.kind == publication["kind"] == "document"
    assert node.title == "summary.md" and node.content == contents
    assert node.state.mime_type == "text/markdown"
    assert node.x > scenario.agent.x
    assert any(e.source == scenario.agent.id and e.target == node.id
               for e in scenario.document.edges.values())
    assert len(scenario.document.command_log) == 1
    assert scenario.document.command_log[0].run_id == run.id
    assert scenario.document.command_log[0].provenance == "agent"
    assert _activity(scenario) == [("fs.write", "ok"), ("graph.publish_file", "ok")]

    assert scenario.document.undo_run(run.id) == 1
    assert set(scenario.document.nodes) == {scenario.agent.id}
    scenario.document.redo()
    assert publication["node_id"] in scenario.document.nodes


def test_discover_and_run_an_enabled_scientific_workbench_operation(scenario, monkeypatch):
    register_science_tools(scenario.registry, scenario.dispatcher, scenario.document)
    host = HostContext("science_mathematics")
    register_pack(host, "mathematics")
    kind = "science_mathematics.workbench"
    scenario.document.plugin_node_serializers[kind] = host._node_kinds[kind].serialize
    settings = SimpleNamespace(get_plugin_grants=lambda: {"science_mathematics": True})
    observed = {}

    def calculate(request):
        catalog = json.loads(_result(request, "catalog"))
        assert catalog["total"] == 1
        assert catalog["operations"][0]["id"] == "trapezoid"
        return {"calls": [_call(
            "calculate", "science.run", pack_id="mathematics", operation_id="trapezoid",
            inputs={"x": [0, 0.5, 1, 2], "y": [1, 2, 3, 5]},
        )]}

    def finish(request):
        observed["result"] = json.loads(_result(request, "calculate"))
        return {"answer": "The verified trapezoid integral is 6."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("catalog", "science.search", pack_id="mathematics", query="trapezoid")]},
        calculate, finish,
    ], approvals=[("science.run", True)], settings_manager=settings, timeout_seconds=60)

    result = observed["result"]
    assert result["ok"] is True and result["verified"] is True
    assert result["values"]["integral"] == 6.0
    assert len(result["receipt"]["receiptHash"]) == 64
    node = scenario.document.nodes[result["node_id"]]
    assert node.kind == kind
    assert node.state.data["latest"]["provenance"] == result["receipt"]
    assert any(e.source == scenario.agent.id and e.target == node.id
               for e in scenario.document.edges.values())
    assert scenario.document.command_log[-1].run_id == run.id
    assert scenario.document.command_log[-1].provenance == "agent"
    assert _activity(scenario) == [("science.search", "ok"), ("science.run", "ok")]


def test_review_a_real_unified_diff_before_approving_an_edit(scenario, monkeypatch):
    target = scenario.workspace / "settings.txt"
    original = "mode = debug\nworkers = 2\n"
    target.write_text(original, encoding="utf-8")
    at_approval = []
    real_publish = scenario.bus.publish

    async def observe_approval(topic):
        if scenario.agent.state.harness_awaiting_approval:
            at_approval.append(target.read_text(encoding="utf-8"))
        await real_publish(topic)

    scenario.bus.publish = observe_approval

    def propose(request):
        assert "mode = debug" in _result(request, "read")
        return {"calls": [_call(
            "edit", "fs.edit", path="settings.txt",
            old_string="mode = debug", new_string="mode = release",
        )]}

    def verify(request):
        assert "edit" in _result(request, "edit").lower() or "replac" in _result(request, "edit").lower()
        return {"calls": [_call("verify", "fs.read", path="settings.txt")]}

    def finish(request):
        assert "mode = release" in _result(request, "verify")
        return {"answer": "Reviewed and applied the requested settings edit."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("read", "fs.read", path="settings.txt")]},
        propose, verify, finish,
    ], approvals=[("fs.edit", True)])

    assert at_approval and all(snapshot == original for snapshot in at_approval)
    diff = run.approvals[0][1]
    assert "--- a/settings.txt" in diff and "+++ b/settings.txt" in diff
    assert "-mode = debug" in diff and "+mode = release" in diff
    assert target.read_text(encoding="utf-8") == original.replace("debug", "release")
    assert _activity(scenario) == [("fs.read", "ok"), ("fs.edit", "ok"), ("fs.read", "ok")]
    assert [m["name"] for m in run.transcript if m["role"] == "tool"] == [
        "fs.read", "fs.edit", "fs.read",
    ]


def test_stream_live_shell_output_before_the_tool_finishes(scenario, monkeypatch):
    script = scenario.workspace / "live_output.py"
    script.write_text(
        "import time\nprint('EARLY_LINE', flush=True)\n"
        "time.sleep(0.5)\nprint('LATE_LINE', flush=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loop_module, "_LIVE_PUBLISH_INTERVAL", 0.01)
    progress = []
    real_publish = scenario.bus.publish

    async def observe_progress(topic):
        progress.extend(
            row["summary"] for row in scenario.agent.state.harness_activity
            if row["tool"] == "shell.exec" and row["outcome"] == "running"
        )
        await real_publish(topic)

    scenario.bus.publish = observe_progress
    if sys.platform == "win32":
        scenario.agent.state.harness_shell = "cmd"
        command = f'"{sys.executable}" -u live_output.py'
    else:
        command = f"{shlex.quote(sys.executable)} -u live_output.py"

    def finish(request):
        result = _result(request, "shell")
        assert result.startswith("exit code 0"), result
        assert "EARLY_LINE" in result and "LATE_LINE" in result
        return {"answer": "Observed live command output."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("shell", "shell.exec", command=command)]},
        finish,
    ], approvals=[("shell.exec", True)])

    assert any("EARLY_LINE" in snapshot for snapshot in progress)
    assert _activity(scenario) == [("shell.exec", "ok")]
    assert "EARLY_LINE" in scenario.agent.state.harness_activity[0]["summary"]
    assert run.approvals[0][0] == "shell.exec"
    assert [m["name"] for m in run.transcript if m["role"] == "tool"] == ["shell.exec"]


def test_agent_files_are_browsable_in_the_confined_files_view(scenario, monkeypatch):
    (scenario.workspace / ".git").mkdir()
    (scenario.workspace / ".git" / "config").write_text("private metadata", encoding="utf-8")
    content = "# Inspection\nCompleted by the workspace agent.\n"

    def list_files(request):
        assert "reports/inspection.md" in _result(request, "written")
        return {"calls": [_call("list", "fs.list", pattern="**/*.md")]}

    def finish(request):
        assert "reports/inspection.md" in _result(request, "list")
        return {"answer": "The inspection report is available in Files."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("written", "fs.write", path="reports/inspection.md", content=content)]},
        list_files, finish,
    ], approvals=[("fs.write", True)])

    root_listing = list_workspace_files(scenario.workspace)
    # The managed scratch root also contains the agent transcript/index.
    assert any(entry["name"] == "reports" and entry["isDir"] for entry in root_listing["entries"])
    assert all(entry["name"] != ".git" for entry in root_listing["entries"])
    sub_listing = list_workspace_files(scenario.workspace, "reports")
    assert [entry["path"] for entry in sub_listing["entries"]] == ["reports/inspection.md"]
    assert read_workspace_file(scenario.workspace, "reports/inspection.md") == {
        "path": "reports/inspection.md", "content": content, "truncated": False,
    }
    with pytest.raises(WorkspaceError, match="metadata"):
        read_workspace_file(scenario.workspace, ".git/config")
    with pytest.raises(WorkspaceError, match="outside"):
        read_workspace_file(scenario.workspace, "../outside.txt")
    assert _activity(scenario) == [("fs.write", "ok"), ("fs.list", "ok")]
    assert [m["name"] for m in run.transcript if m["role"] == "tool"] == ["fs.write", "fs.list"]


def test_json_fallback_executes_approved_edits_and_canvas_publication(scenario, monkeypatch):
    target = scenario.workspace / "release.md"
    target.write_text("# Release\nStatus: draft\n", encoding="utf-8")
    observed = {}

    def edit_then_publish(request):
        assert request.tools == ()
        records = json.loads(request.messages[-1]["content"].split("\n", 1)[1])
        assert len(records) == 1 and "Status: draft" in records[0]["content"]
        return {"calls": [
            _call("edit", "fs.edit", path="release.md",
                  old_string="Status: draft", new_string="Status: ready"),
            _call("publish", "graph.publish_file", path="release.md"),
        ]}

    def finish(request):
        assert request.tools == ()
        records = json.loads(request.messages[-1]["content"].split("\n", 1)[1])
        assert [record["name"] for record in records] == ["fs.edit", "graph.publish_file"]
        observed["publication"] = json.loads(records[1]["content"])
        return {"answer": "Published the ready release through constrained JSON tools."}

    run = _drive(scenario, monkeypatch, [
        {"calls": [_call("read", "fs.read", path="release.md")]},
        edit_then_publish, finish,
    ], json_tools=True, approvals=[("fs.edit", True), ("graph.publish_file", True)])

    assert target.read_text(encoding="utf-8") == "# Release\nStatus: ready\n"
    node = scenario.document.nodes[observed["publication"]["node_id"]]
    assert node.kind == "document" and "Status: ready" in node.content
    assert run.scripted_model.capabilities.tools is False
    assert run.scripted_model.capabilities.structured_output is True
    assert all(request.tools == () for request in run.scripted_model.requests)
    assert all(request.extra_kwargs["response_format"]["type"] == "json_object"
               for request in run.scripted_model.requests)
    assert _activity(scenario) == [
        ("fs.read", "ok"), ("fs.edit", "ok"), ("graph.publish_file", "ok"),
    ]
    tool_messages = [m for m in run.transcript if m["role"] == "tool"]
    assert [m["name"] for m in tool_messages] == ["fs.read", "fs.edit", "graph.publish_file"]
    assert all(m["tool_call_id"].startswith("json_") for m in tool_messages)
    assert scenario.document.command_log[-1].run_id == run.id
