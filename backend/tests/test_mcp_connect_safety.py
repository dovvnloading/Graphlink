"""Adversarial lifecycle regressions for MCP graph execution."""

from __future__ import annotations

import asyncio
import threading

import pytest

from backend.tests.test_mcp_connect_intents import MCP_HANDOFF_TIMEOUT, setup, open_session, add_tool, run_tool
from graphlink_plugins.mcp_connect import core


async def _cancel_after_dispatch(tmp_path, monkeypatch):
    settings, server = setup(tmp_path, monkeypatch, approval="auto")
    doc, bus, scene = open_session(tmp_path / "safety-graphs.db", settings)
    node_id = await add_tool(bus, scene)
    entered, release = threading.Event(), threading.Event()

    def hold(_name, _arguments):
        entered.set()
        assert release.wait(10)

    server.on_call = hold
    preview = await scene("previewGraphRun", [])
    task = asyncio.create_task(scene("executeGraphRun", [], preview["previewToken"], "uncertain-cancel-run"))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        await scene("cancelGraphRun", "uncertain-cancel-run")
    finally:
        release.set()
    result = await asyncio.wait_for(task, 10)
    server.on_call = None
    assert result["steps"][0]["status"] == "reconciliation-required"
    assert len(server.calls) == 1
    return doc, bus, scene, server, node_id, result


def test_cancelled_graph_preserves_uncertain_run_status(tmp_path, monkeypatch):
    async def scenario():
        doc, _bus, _scene, _server, node_id, result = await _cancel_after_dispatch(tmp_path, monkeypatch)
        assert result["status"] == "reconciliation-required"
        assert doc.nodes[node_id].state.data["status"] == "reconciliation-required"

    asyncio.run(scenario())


def test_history_pressure_cannot_erase_an_uncertain_request_after_undo(tmp_path, monkeypatch):
    async def scenario():
        doc, bus, scene, server, node_id, result = await _cancel_after_dispatch(tmp_path, monkeypatch)
        request_id = result["steps"][0]["externalAttempt"]["requestId"]
        # The latest recorded graph command configured this tool before its
        # dispatch. Undo/redo changes canvas state, never remote effects or
        # the journal that must still fence the cancelled request.
        doc.undo()
        doc.redo()
        assert core.configured(doc.nodes[node_id])
        other = await add_tool(bus, scene, "Independent tool")
        for index in range(13):
            completed, _ = await run_tool(scene, other, "independent-run-" + str(index))
            assert completed["status"] == "completed"
        attempts = await scene("getMcpToolAttempts", node_id)
        assert any(attempt["requestId"] == request_id for attempt in attempts), "Uncertain receipt was evicted"
        calls_before = len(server.calls)
        try:
            outcome, _ = await run_tool(scene, node_id, "must-not-redispatch")
            assert outcome["status"] != "completed"
        except Exception as exc:
            # Some surfaces reject at preview; others return a blocked run.
            assert "unresolved" in str(exc).lower() or "reconcile" in str(exc).lower()
        assert len(server.calls) == calls_before

    asyncio.run(scenario())


def test_tool_error_is_never_checkpointed_as_a_successful_step(tmp_path, monkeypatch):
    import copy
    from backend import chat_library
    from backend.domain.graph_run_history import restore_graph_runs

    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "error-graphs.db", settings)
        node_id = await add_tool(bus, scene)
        server.is_error = True
        original = chat_library.save_chat_atomically_row
        snapshots = []

        def record(*args, **kwargs):
            outcome = original(*args, **kwargs)
            snapshots.append((copy.deepcopy(doc.graph_runs[-1]), copy.deepcopy(doc.nodes[node_id].state.data)))
            return outcome

        monkeypatch.setattr(chat_library, "save_chat_atomically_row", record)
        result, _ = await run_tool(scene, node_id, "known-tool-error")
        assert result["status"] == "partial"
        completed_receipts = [run for run, state in snapshots if state.get("latest") is not None
                              and run["steps"][0].get("externalAttempt", {}).get("phase") == "completed"]
        assert completed_receipts
        for saved in completed_receipts:
            assert saved["steps"][0]["status"] == "failed", "Error result was durably advertised as success"
            assert saved["steps"][0]["errorType"] == "tool_error"
            # A crash after *any* completed-receipt checkpoint must restore a
            # known failure, never a completed step or an uncertain retry.
            restored, = restore_graph_runs([saved], {node_id: node_id})
            assert restored["steps"][0]["status"] == "failed"
            assert restored["steps"][0]["externalAttempt"]["phase"] == "completed"

    asyncio.run(scenario())


def test_reconciliation_cannot_revive_an_older_result_as_current(tmp_path, monkeypatch):
    from backend.mcp_client import McpError

    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "freshness-graphs.db", settings)
        node_id = await add_tool(bus, scene)
        first, _ = await run_tool(scene, node_id, "first-known-result")
        assert first["status"] == "completed"
        assert core.prepare(doc, doc.nodes[node_id]).current

        def fail(_name, _arguments):
            raise McpError("Disconnected after remote execution")

        server.on_call = fail
        uncertain, _ = await run_tool(scene, node_id, "second-unknown-result")
        assert uncertain["status"] == "reconciliation-required"
        request_id = uncertain["steps"][0]["externalAttempt"]["requestId"]
        await scene("reconcileMcpTool", node_id, request_id)
        assert doc.nodes[node_id].state.data["latest"]["version"] == 1
        assert not core.prepare(doc, doc.nodes[node_id]).current, "An old output became current after an uncertain refresh"
        assert (await scene("previewGraphRun", []))["steps"][0]["status"] == "ready"
        server.on_call = None
        refreshed, _ = await run_tool(scene, node_id, "third-known-result")
        assert refreshed["status"] == "completed"
        assert core.prepare(doc, doc.nodes[node_id]).current
        assert doc.nodes[node_id].state.data["latest"]["version"] == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("revoke", ["server", "plugin"])
def test_revocation_during_predispatch_checkpoint_prevents_remote_call(tmp_path, monkeypatch, revoke):
    from backend import chat_library

    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "revocation-graphs.db", settings)
        node_id = await add_tool(bus, scene)
        original = chat_library.save_chat_atomically_row
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        held = False

        def hold_fence(*args, **kwargs):
            nonlocal held
            step = args[3]["graph_runs"][-1]["steps"][0]
            if not held and step.get("externalAttempt", {}).get("phase") == "dispatched":
                held = True
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(MCP_HANDOFF_TIMEOUT)
            return original(*args, **kwargs)

        monkeypatch.setattr(chat_library, "save_chat_atomically_row", hold_fence)
        task = asyncio.create_task(run_tool(scene, node_id, "revoked-before-send"))
        try:
            await asyncio.wait_for(entered.wait(), MCP_HANDOFF_TIMEOUT)
            assert not server.calls, "The tool was sent before its durable fence"
            if revoke == "server":
                settings.set_mcp_servers([{**settings.get_mcp_servers()[0], "enabled": False}])
            else:
                settings.set_plugin_grant("mcp_connect", False)
        finally:
            release.set()
        result, _ = await asyncio.wait_for(task, MCP_HANDOFF_TIMEOUT)
        assert not server.calls, "Revoked MCP authority was used after the durable checkpoint resumed"
        assert result["status"] == "reconciliation-required"
        assert result["steps"][0]["externalAttempt"]["phase"] == "reconciliation-required"
        assert doc.nodes[node_id].state.data["latest"] is None

    asyncio.run(scenario())


def test_repeated_cancellation_drains_worker_before_releasing_graph(tmp_path, monkeypatch):
    from backend import mcp_connect_runtime

    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "double-cancel.db", settings)
        node_id = await add_tool(bus, scene)
        call_entered, call_release, call_finished = threading.Event(), threading.Event(), threading.Event()
        close_entered, close_release, close_finished = threading.Event(), threading.Event(), threading.Event()
        original_factory = server.client

        def factory(config):
            client = original_factory(config)
            original_call, original_close = client.call_tool, client.close

            def call(name, arguments):
                call_entered.set()
                assert call_release.wait(10)
                try:
                    return original_call(name, arguments)
                finally:
                    call_finished.set()

            def close():
                close_entered.set()
                assert close_release.wait(10)
                original_close()
                close_finished.set()

            client.call_tool, client.close = call, close
            return client

        monkeypatch.setattr(mcp_connect_runtime, "create_mcp_client", factory)
        task = asyncio.create_task(run_tool(scene, node_id, "double-cancel-run"))
        try:
            assert await asyncio.to_thread(call_entered.wait, 10)
            assert await scene("cancelMcpTool", node_id) == {"ok": True}
            assert await asyncio.to_thread(close_entered.wait, 10)
            assert await scene("cancelMcpTool", node_id) == {"ok": True}
            close_release.set()
            assert await asyncio.to_thread(close_finished.wait, 10)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not task.done(), "Cancellation detached a still-running MCP worker"
            assert bus.chat_mutation_guard["active"]
            assert doc.nodes[node_id].pending_request_id is not None
        finally:
            close_release.set()
            call_release.set()
        result, _ = await asyncio.wait_for(task, 10)
        assert call_finished.is_set()
        assert result["status"] == "reconciliation-required"
        assert not bus.chat_mutation_guard["active"]
        assert not bus.mcp_connect_runtime.active
        assert doc.nodes[node_id].pending_request_id is None
        assert len(server.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["schema", "annotations"])
def test_once_approval_is_not_reused_after_capability_change(tmp_path, monkeypatch, change):
    from dataclasses import replace

    async def scenario():
        settings, server = setup(tmp_path, monkeypatch, approval="once")
        doc, bus, scene = open_session(tmp_path / "new-capability.db", settings)
        node_id = await add_tool(bus, scene)
        first, _ = await run_tool(scene, node_id, "review-original-tool")
        assert first["status"] == "completed"
        spec = server.specs[0]
        if change == "schema":
            server.specs = (replace(spec, input_schema={**spec.input_schema,
                "properties": {**spec.input_schema["properties"], "newMode": {"type": "string"}}}),)
        else:
            server.specs = (replace(spec, annotations={"readOnlyHint": False, "destructiveHint": True}),)
        await scene("listMcpTools", "test-server")
        await scene("configureMcpTool", node_id, {
            "serverId": "test-server", "toolName": "echo", "arguments": {"message": "Hello"}, "bindings": {},
        })
        appeared = asyncio.Event()
        publish = bus.publish

        async def observe(topic):
            outcome = await publish(topic)
            if topic == "scene" and any(
                not future.done() for _row, future in bus.mcp_connect_runtime.approvals.values()
            ):
                appeared.set()
            return outcome

        monkeypatch.setattr(bus, "publish", observe)
        preview = await scene("previewGraphRun", [])
        task = asyncio.create_task(scene("executeGraphRun", [], preview["previewToken"], "review-changed-tool"))
        try:
            await asyncio.wait_for(appeared.wait(), MCP_HANDOFF_TIMEOUT)
            pending, = await scene("getMcpToolApprovals")
            assert pending["nodeId"] == node_id
            assert len(server.calls) == 1
            assert await scene("approveMcpTool", pending["id"], False) == {"ok": True}
            outcome = await asyncio.wait_for(task, MCP_HANDOFF_TIMEOUT)
            assert outcome["status"] == "partial"
            assert len(server.calls) == 1
        finally:
            if not task.done():
                bus.mcp_connect_runtime.disconnect()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_undo_cannot_make_an_unresolved_tool_output_current(tmp_path, monkeypatch):
    from backend.mcp_client import McpError

    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        doc, bus, scene = open_session(tmp_path / "undo-uncertainty.db", settings)
        node_id = await add_tool(bus, scene)
        await run_tool(scene, node_id, "known-before-refresh")

        def uncertain(_name, _arguments):
            raise McpError("Unknown remote outcome")

        server.on_call = uncertain
        result, _ = await run_tool(scene, node_id, "unknown-after-refresh")
        assert result["status"] == "reconciliation-required"
        # Restore the snapshot from before the interrupted refresh. A graph
        # undo is not reconciliation of a request at the connected service.
        doc.undo()
        doc.redo()
        assert doc.nodes[node_id].state.data["latest"]["version"] == 1
        preview = await scene("previewGraphRun", [])
        step, = preview["steps"]
        assert step["status"] == "blocked", "Undo revived a result with an unresolved remote refresh"
        assert "reconcil" in step["detail"].lower() or "unresolved" in step["detail"].lower()
        assert len(server.calls) == 2

    asyncio.run(scenario())
