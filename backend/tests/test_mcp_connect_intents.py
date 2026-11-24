"""MCP graph execution through real intents, plugin consent and SQLite saves."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import sys
import threading

import pytest

from backend import chat_library, mcp_connect_runtime
from backend.canvas import SceneDocument
from backend.events import IntentValidationError, SessionBus
from backend.mcp_client import McpError, McpToolResult, McpToolSpec
from backend.notifications import NotificationState
from backend.plugin_sdk import discover_plugins
from backend.plugins import register_plugins
from graphlink_plugins.mcp_connect import core
from graphlink_settings_store import SettingsManager


class Server:
    def __init__(self):
        self.specs = (McpToolSpec(
            name="echo", description="Return the supplied message.",
            input_schema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
            output_schema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
            annotations={"readOnlyHint": True, "title": "Display metadata is not an authorization grant"},
        ),)
        self.calls = []
        self.connections = 0
        self.closed = 0
        self.on_call = None
        self.on_connect = None
        self.is_error = False
        self.discover_error = False

    def client(self, _config):
        server = self

        class Client:
            def connect(self):
                server.connections += 1
                if server.on_connect:
                    server.on_connect()

            def list_tools(self):
                if server.discover_error:
                    raise McpError("stderr contains private-server-credential")
                return server.specs

            def call_tool(self, name, arguments):
                server.calls.append((name, copy.deepcopy(arguments)))
                if server.on_call:
                    server.on_call(name, arguments)
                text = arguments["message"]
                return McpToolResult(
                    content=text, is_error=server.is_error,
                    content_blocks=({"type": "text", "text": text},),
                    structured_content={"message": text},
                )

            def close(self):
                server.closed += 1

        return Client()


def setup(tmp_path, monkeypatch, *, approval="always"):
    settings = SettingsManager(tmp_path / "settings.dat")
    settings.set_plugin_grant("mcp_connect", True)
    settings.set_mcp_servers([{"id": "test-server", "name": "Test server", "command": "unused-test-command",
                              "approval": approval, "env": {"API_KEY": "private-server-credential"}}])
    server = Server()
    monkeypatch.setattr(mcp_connect_runtime, "create_mcp_client", server.client)
    return settings, server


def open_session(path, settings):
    document = SceneDocument()
    bus = SessionBus("mcp-connect-tests")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", document.scene_payload)
    register_plugins(bus, notifications, document, settings, plugin_registry=discover_plugins())
    chat_library.register_chat_library(bus, path, document, notifications, autosave_interval_seconds=None, settings_manager=settings)

    async def scene(intent, *args):
        return await bus.dispatch_intent("scene", intent, list(args))

    return document, bus, scene


async def add_tool(bus, scene, message="Hello", parent=None, bindings=None):
    node_id = await bus.dispatch_intent("app-plugins", "executePlugin", ["MCP Connect", parent, 100, 100])
    assert isinstance(node_id, str)
    assert await scene("configureMcpTool", node_id, {
        "serverId": "test-server", "toolName": "echo", "arguments": {"message": message}, "bindings": bindings or {},
    }) == {"ok": True}
    return node_id


async def run_tool(scene, node_id, operation_id="mcp-operation-one"):
    preview = await scene("previewMcpTool", node_id)
    result = await scene("runMcpTool", node_id, preview["previewToken"], operation_id)
    return result, preview


def test_real_stdio_tool_runs_through_plugin_bus_and_database(tmp_path):
    async def scenario():
        settings = SettingsManager(tmp_path / "settings.dat")
        settings.set_plugin_grant("mcp_connect", True)
        fixture = Path(__file__).resolve().parents[2] / "tests_e2e" / "fixture_mcp_server.py"
        settings.set_mcp_servers([{
            "id": "real-stdio", "name": "Project records", "command": sys.executable,
            "args": [str(fixture)], "approval": "always", "timeout": 10,
        }])
        path = tmp_path / "graphs.db"
        document, bus, scene = open_session(path, settings)
        catalog = await scene("listMcpTools", "real-stdio")
        assert not catalog["errors"] and catalog["tools"][0]["name"] == "lookup_records"
        node_id = await bus.dispatch_intent("app-plugins", "executePlugin", ["MCP Connect", None, 100, 100])
        await scene("configureMcpTool", node_id, {
            "serverId": "real-stdio", "toolName": "lookup_records",
            "arguments": {"query": "Graphlink", "limit": 2}, "bindings": {},
        })
        result, preview = await run_tool(scene, node_id, "real-stdio-operation")
        assert result["status"] == "completed", result
        assert preview["requiresApproval"]
        node = document.nodes[node_id]
        assert node.state.data["latest"]["structuredContent"]["rows"] == [
            {"Project": "Graphlink 1", "Status": "Open", "Count": 7},
            {"Project": "Graphlink 2", "Status": "Open", "Count": 14},
        ]
        assert core.prepare(document, node).current
        saved = chat_library.load_chat_row(path, document.current_chat_id)
        assert saved["data"]["graph_runs"][-1]["steps"][0]["externalAttempt"]["phase"] == "completed"

    asyncio.run(scenario())


def test_discovery_preview_run_idempotency_provenance_undo_and_reload(tmp_path, monkeypatch):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        path = tmp_path / "graphs.db"
        doc, bus, scene = open_session(path, settings)
        # Leave holes so restored references actually need remapping.
        for _ in range(4):
            doc.remove_nodes([doc.add_note(0, 0).id])
        catalog = await scene("listMcpTools", "")
        assert catalog["tools"] == [] and server.connections == 0
        node_id = await add_tool(bus, scene)
        preview = await scene("previewMcpTool", node_id)
        assert preview["arguments"] == {"message": "Hello"} and preview["requiresApproval"]
        assert not server.calls and not path.exists()

        def verify_fence(_name, _arguments):
            saved = chat_library.load_chat_row(path, doc.current_chat_id)
            step = saved["data"]["graph_runs"][-1]["steps"][0]
            assert step["externalAttempt"]["phase"] == "dispatched"
            assert step["inputHash"] == preview["inputHash"]

        server.on_call = verify_fence
        result = await scene("runMcpTool", node_id, preview["previewToken"], "mcp-operation-one")
        assert result["status"] == "completed", result
        assert len(server.calls) == 1
        latest = doc.nodes[node_id].state.data["latest"]
        assert latest["structuredContent"] == {"message": "Hello"} and latest["version"] == 1
        assert core.valid_result(latest)
        assert latest["provenance"]["requestId"] == result["steps"][0]["externalAttempt"]["requestId"]
        assert "private-server-credential" not in json.dumps(doc.scene_payload())
        assert "private-server-credential" not in json.dumps(doc.graph_runs)
        assert (await scene("runMcpTool", node_id, preview["previewToken"], "mcp-operation-one"))["status"] == "completed"
        assert len(server.calls) == 1
        assert (await scene("previewGraphRun", []))["steps"][0]["status"] == "current"
        assert doc.undo_run("mcp-operation-one") == 1
        assert doc.nodes[node_id].state.data["latest"] is None and len(server.calls) == 1
        doc.redo()
        fresh, bus2, scene2 = open_session(path, settings)
        await bus2.dispatch_intent("app-chat-library", "loadChat", [doc.current_chat_id])
        restored = next(n for n in fresh.nodes.values() if n.kind == core.KIND)
        assert restored.id != node_id
        assert core.prepare(fresh, restored).current
        assert (await scene2("getMcpToolAttempts", restored.id))[0]["phase"] == "completed"
        assert len(server.calls) == 1

    asyncio.run(scenario())


def test_source_or_permission_drift_invalidates_preview_without_call(tmp_path, monkeypatch):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "graphs.db", settings)
        source = doc.add_note(0, 0)
        doc.set_note_content(source.id, "first")
        node_id = await add_tool(bus, scene, parent=source.id, bindings={
            "message": {"sourceId": source.id, "output": "text", "pointer": ""},
        })
        assert (await scene("getMcpToolSources", node_id))[0]["id"] == source.id
        preview = await scene("previewMcpTool", node_id)
        doc.set_note_content(source.id, "changed")
        with pytest.raises(IntentValidationError, match="changed"):
            await scene("runMcpTool", node_id, preview["previewToken"], "stale-input-test")
        preview = await scene("previewMcpTool", node_id)
        config = settings.get_mcp_servers()[0]
        settings.set_mcp_servers([{**config, "enabled_tools": ["different"]}])
        with pytest.raises(IntentValidationError, match="not enabled"):
            await scene("runMcpTool", node_id, preview["previewToken"], "stale-policy-test")
        assert not server.calls and not doc.graph_runs

    asyncio.run(scenario())


def test_json_text_binding_and_authoritative_currentness(tmp_path, monkeypatch):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "graphs.db", settings)
        source = doc.add_note(0, 0)
        doc.set_note_content(source.id, '{"message":"from JSON"}')
        binding = {"message": {"sourceId": source.id, "output": "json", "pointer": "/message"}}
        node_id = await add_tool(bus, scene, parent=source.id, bindings=binding)
        sources = await scene("getMcpToolSources", node_id)
        assert "json" in sources[0]["outputs"]
        assert not (await scene("getMcpToolStatus", node_id))["current"]
        await run_tool(scene, node_id)
        assert server.calls == [("echo", {"message": "from JSON"})]
        assert (await scene("getMcpToolStatus", node_id))["current"]
        await scene("configureMcpTool", node_id, {
            "serverId": "test-server", "toolName": "echo", "arguments": {"message": "Hello"}, "bindings": binding,
        })
        assert (await scene("getMcpToolStatus", node_id))["current"]
        doc.set_note_content(source.id, '{"message":"edited JSON"}')
        assert not (await scene("getMcpToolStatus", node_id))["current"]
        assert (await scene("previewMcpTool", node_id))["arguments"] == {"message": "edited JSON"}
        assert len(server.calls) == 1

    asyncio.run(scenario())


def test_runner_approves_actual_deferred_arguments_and_propagates_output(tmp_path, monkeypatch):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        path = tmp_path / "graphs.db"
        doc, bus, scene = open_session(path, settings)
        first = await add_tool(bus, scene, "from upstream")
        second = await add_tool(bus, scene, "unused", parent=first, bindings={
            "message": {"sourceId": first, "output": "json", "pointer": "/message"},
        })
        note = doc.add_note(0, 0)
        doc.bind_note_text_input(second, note.id)
        preview = await scene("previewGraphRun", [])
        assert [step["status"] for step in preview["steps"]] == ["ready", "waiting", "waiting"]
        appeared = asyncio.Queue()
        seen_approvals = set()
        publish = bus.publish

        async def observed(topic):
            result = await publish(topic)
            # The durable approval checkpoint also publishes library updates.
            # Observe each live approval once, not every publication while it
            # remains in the runtime's map (including resolved futures).
            if topic == "scene":
                for approval_id, (_row, future) in bus.mcp_connect_runtime.approvals.items():
                    if not future.done() and approval_id not in seen_approvals:
                        seen_approvals.add(approval_id)
                        appeared.put_nowait(approval_id)
            return result

        monkeypatch.setattr(bus, "publish", observed)
        task = asyncio.create_task(scene("executeGraphRun", [], preview["previewToken"], "mcp-chain-run"))
        for index, node_id in enumerate((first, second)):
            approval_id = await asyncio.wait_for(appeared.get(), 10)
            pending, = await scene("getMcpToolApprovals")
            assert pending["id"] == approval_id
            assert pending["nodeId"] == node_id
            assert pending["arguments"] == {"message": "from upstream"}
            assert server.calls == [("echo", {"message": "from upstream"})] * index
            receipt = chat_library.load_workflow_run(path, doc.current_chat_id, "mcp-chain-run")
            assert receipt["steps"][index]["approval"] == "pending"
            assert receipt["steps"][index]["approval_id"] == approval_id
            assert receipt["steps"][index]["request_id"] is None
            assert await scene("approveMcpTool", "stale-approval", True) == {"ok": False}
            assert await scene("approveMcpTool", pending["id"], True) == {"ok": True}
        result = await asyncio.wait_for(task, 10)
        assert result["status"] == "completed", result
        assert [step["status"] for step in result["steps"]] == ["completed"] * 3
        assert note.content == "from upstream" and len(server.calls) == 2
        assert (await scene("previewGraphRun", []))["steps"][0]["status"] == "current"

    asyncio.run(scenario())


@pytest.mark.parametrize("deny", [True, False])
def test_runner_denial_or_disconnect_sends_nothing(tmp_path, monkeypatch, deny):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "graphs.db", settings)
        node_id = await add_tool(bus, scene)
        preview = await scene("previewGraphRun", [])
        appeared = asyncio.Event()
        publish = bus.publish

        async def observed(topic):
            result = await publish(topic)
            if bus.mcp_connect_runtime.approvals:
                appeared.set()
            return result

        monkeypatch.setattr(bus, "publish", observed)
        task = asyncio.create_task(scene("executeGraphRun", [], preview["previewToken"], "mcp-denied-run"))
        await asyncio.wait_for(appeared.wait(), 10)
        if deny:
            pending, = await scene("getMcpToolApprovals")
            await scene("approveMcpTool", pending["id"], False)
        else:
            bus.mcp_connect_runtime.disconnect()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not server.calls
        assert not bus.mcp_connect_runtime.approvals
        assert doc.nodes[node_id].pending_request_id is None
        assert "externalAttempt" not in doc.graph_runs[-1]["steps"][0]

    asyncio.run(scenario())


def test_remote_failure_is_uncertain_never_replayed_and_explicitly_reconciled(tmp_path, monkeypatch):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        path = tmp_path / "graphs.db"
        doc, bus, scene = open_session(path, settings)
        node_id = await add_tool(bus, scene)

        def uncertain(_name, _arguments):
            raise McpError("private-server-credential from stderr")

        server.on_call = uncertain
        result, preview = await run_tool(scene, node_id)
        assert result["status"] == "reconciliation-required", result
        assert doc.nodes[node_id].state.data["status"] == "reconciliation-required"
        assert "private-server-credential" not in json.dumps(doc.scene_payload())
        assert (await scene("resumeGraphRun", result["runId"]))["status"] == "reconciliation-required"
        assert len(server.calls) == 1
        with pytest.raises(IntentValidationError, match="Reconcile"):
            await scene("previewMcpTool", node_id)
        fresh, bus2, scene2 = open_session(path, settings)
        await bus2.dispatch_intent("app-chat-library", "loadChat", [doc.current_chat_id])
        restored = next(n for n in fresh.nodes.values() if n.kind == core.KIND)
        attempt, = await scene2("getMcpToolAttempts", restored.id)
        assert attempt["phase"] == "reconciliation-required"
        assert await scene2("reconcileMcpTool", restored.id, attempt["requestId"]) == {"ok": True}
        assert (await scene2("getMcpToolAttempts", restored.id))[0]["phase"] == "reconciled"
        assert len(server.calls) == 1
        server.on_call = None
        result, _ = await run_tool(scene2, restored.id, "explicit-new-attempt")
        assert result["status"] == "completed" and len(server.calls) == 2

    asyncio.run(scenario())


def test_tool_error_is_saved_as_known_failure_after_reload(tmp_path, monkeypatch):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        path = tmp_path / "graphs.db"
        doc, bus, scene = open_session(path, settings)
        node_id = await add_tool(bus, scene)
        server.is_error = True
        result, _ = await run_tool(scene, node_id)
        assert result["status"] == "partial", result
        assert result["steps"][0]["status"] == "failed"
        assert doc.nodes[node_id].state.data["latest"]["isError"]
        _, fresh_bus, fresh_scene = open_session(path, settings)
        await fresh_bus.dispatch_intent("app-chat-library", "loadChat", [doc.current_chat_id])
        saved = await fresh_scene("getGraphRun")
        assert saved["status"] == "partial" and saved["steps"][0]["status"] == "failed"
        assert saved["steps"][0]["externalAttempt"]["phase"] == "completed"
        assert len(server.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_write", [1, 2, 3, 4])
def test_failed_checkpoints_never_dispatch_early_or_replay(tmp_path, monkeypatch, failure_write):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "graphs.db", settings)
        node_id = await add_tool(bus, scene)
        original = chat_library.save_chat_atomically_row
        writes = 0

        def fail_once(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == failure_write:
                raise OSError("injected disk failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(chat_library, "save_chat_atomically_row", fail_once)
        try:
            result, _ = await run_tool(scene, node_id)
        except IntentValidationError:
            result = await scene("getGraphRun")
        assert len(server.calls) == (1 if failure_write >= 3 else 0)
        assert not bus.chat_mutation_guard["active"]
        if failure_write >= 3:
            assert result["status"] == ("reconciliation-required" if failure_write == 3 else "interrupted")
            await scene("resumeGraphRun", result["runId"])
            assert len(server.calls) == 1

    asyncio.run(scenario())


def test_terminal_receipt_stays_running_until_its_save_commits(tmp_path, monkeypatch):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        document, bus, scene = open_session(tmp_path / "graphs.db", settings)
        node_id = await add_tool(bus, scene)
        original = chat_library.save_chat_atomically_row
        entered, release = threading.Event(), threading.Event()

        def hold_terminal(*args, **kwargs):
            if document.graph_runs[-1]["status"] == "completed":
                entered.set()
                assert release.wait(10)
            return original(*args, **kwargs)

        monkeypatch.setattr(chat_library, "save_chat_atomically_row", hold_terminal)
        task = asyncio.create_task(run_tool(scene, node_id))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            visible = await scene("getGraphRun")
            assert visible["status"] == "running" and visible["trace"]["status"] == "running"
            assert bus.chat_mutation_guard["active"] and len(server.calls) == 1
        finally:
            release.set()
        result, _ = await asyncio.wait_for(task, 10)
        assert result["status"] == "completed"
        assert (await scene("getGraphRun"))["status"] == "completed"

    asyncio.run(scenario())


def test_inflight_input_change_does_not_commit_response(tmp_path, monkeypatch):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "graphs.db", settings)
        source = doc.add_note(0, 0)
        doc.set_note_content(source.id, "first")
        node_id = await add_tool(bus, scene, parent=source.id, bindings={
            "message": {"sourceId": source.id, "output": "text", "pointer": ""},
        })
        entered, release = threading.Event(), threading.Event()

        def hold(_name, _arguments):
            entered.set()
            assert release.wait(10)

        server.on_call = hold
        task = asyncio.create_task(run_tool(scene, node_id))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            doc.set_note_content(source.id, "edited while tool was running")
        finally:
            release.set()
        result, _ = await task
        assert result["status"] == "reconciliation-required"
        assert doc.nodes[node_id].state.data["latest"] is None
        assert len(server.calls) == 1

    asyncio.run(scenario())


def test_discovery_errors_and_echoed_secrets_are_not_published(tmp_path, monkeypatch):
    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "graphs.db", settings)
        server.discover_error = True
        catalog = await scene("listMcpTools", "test-server")
        assert catalog["errors"] and "private-server-credential" not in json.dumps(catalog)
        server.discover_error = False
        node_id = await add_tool(bus, scene, "private-server-credential")
        result, preview = await run_tool(scene, node_id)
        assert preview["arguments"] == {"message": "[redacted]"}
        assert result["status"] == "completed"
        latest = doc.nodes[node_id].state.data["latest"]
        assert "private-server-credential" not in json.dumps(latest)

    asyncio.run(scenario())
