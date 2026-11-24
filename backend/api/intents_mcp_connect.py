"""MCP Connect's explicit discover, configure, preview, run and approval API."""

from __future__ import annotations

import copy
import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.api.intents_graph_runner import _digest, _make_graph_checkpoint, _public_run
from backend.domain.graph_run_history import RUN_HISTORY_LIMIT, RUN_ID, can_prune_run
from backend.domain.model import SceneError
from backend.events import IntentValidationError
from backend.mcp_connect_runtime import McpConnectRuntime, _redact, require_plugin, require_server
from graphlink_plugins.mcp_connect import core


@dataclass
class McpServerArgs:
    serverId: str = ""


@dataclass
class McpNodeArgs:
    nodeId: str


@dataclass
class ConfigureMcpArgs(McpNodeArgs):
    configuration: dict


@dataclass
class RunMcpArgs(McpNodeArgs):
    previewToken: str
    operationId: str


@dataclass
class ApproveMcpArgs:
    approvalId: str
    approved: bool


@dataclass
class ReconcileMcpArgs(McpNodeArgs):
    requestId: str


@dataclass
class NoMcpArgs:
    pass


def _node(document: Any, node_id: str) -> Any:
    node = document.nodes.get(node_id)
    if node is None or node.kind != "mcp_connect.tool":
        raise SceneError("Choose an MCP Connect tool node on the open graph.")
    core.state_data(node)
    return node


def _preview(runtime: McpConnectRuntime, node_id: str) -> tuple[str, dict, Any]:
    node = _node(runtime.document, node_id)
    if node.pending_request_id:
        raise SceneError("This MCP tool is already running. Wait for its result or cancel it.")
    prepared = core.prepare(runtime.document, node)
    sources = list(core.dependencies(runtime.document, node))
    step = {
        "nodeId": node_id, "sourceId": sources[0] if sources else node_id, "sourceIds": sources,
        "kind": "tool", "operation": "tool", "label": node.title,
        "status": "ready", "detail": "Reviewed MCP tool request.", "inputHash": prepared.input_hash,
        "configHash": core.config_hash(node), "targetHash": core.output_hash(node),
    }
    runtime.pin_step(node, step)
    token = _digest([str(runtime.document.graph_run_epoch), step])
    return token, step, prepared


def _source_rows(document: Any, node_id: str) -> list[dict]:
    rows = []
    ids = dict.fromkeys(edge.source for edge in document.edges.values() if edge.target == node_id)
    for source_id in ids:
        source = document.nodes.get(source_id)
        if source is None or source.id == node_id:
            continue
        outputs: list[str] = []
        if source.kind == "mcp_connect.tool":
            # A configured upstream tool can have no output *yet*. Allow its
            # typed port to be bound; Runner resolves it after completion.
            outputs = ["text", "json", "table"]
        else:
            try:
                document._text_output(source_id)
                outputs.extend(("text", "json"))
            except SceneError:
                pass
            try:
                document._read_table(source_id)
                outputs.append("table")
            except SceneError:
                pass
        if outputs:
            rows.append({"id": source.id, "title": source.title, "kind": source.kind, "outputs": outputs})
    return rows


def register_mcp_connect(bus, document, settings, save_checkpoint, serialize_mutation) -> None:
    runtime = McpConnectRuntime(bus, document, settings)
    bus.mcp_connect_runtime = runtime
    checkpoint_write = _make_graph_checkpoint(save_checkpoint)

    async def checkpoint() -> bool:
        await checkpoint_write()
        return True

    async def list_mcp_tools(server_id: str = ""):
        result = {"servers": runtime.server_rows(), "tools": [], "errors": []}
        if server_id:
            try:
                result["tools"] = await runtime.discover(server_id)
            except SceneError as exc:
                result["errors"] = [{"serverId": server_id, "message": str(exc)}]
        return result

    @serialize_mutation
    async def configure_node(node_id: str, config: dict):
        require_plugin(settings)
        node = _node(document, node_id)
        if node.pending_request_id:
            raise SceneError("Wait for this MCP request to finish before changing its configuration.")
        if set(config) - {"serverId", "toolName", "arguments", "bindings"}:
            raise SceneError("MCP configuration contains unsupported fields. Refresh the tool browser.")
        if not isinstance(config.get("serverId"), str) or not isinstance(config.get("toolName"), str):
            raise SceneError("Choose a configured MCP server and tool.")
        metadata = await runtime.tool_metadata(config["serverId"], config["toolName"])
        if document.nodes.get(node_id) is not node:
            raise SceneError("The MCP node changed while tools were loading. Select it again.")
        document.record_command(
            "configureMcpTool", "user", lambda: core.configure(
                document, node, metadata, config.get("arguments", {}), config.get("bindings", {}),
            ), node_ids=(node_id,),
        )
        await bus.publish("scene")
        return {"ok": True}

    async def configure_mcp_tool(node_id: str, config: dict):
        try:
            result = await configure_node(node_id, config)
            if result is None:
                raise SceneError("Another graph operation is in progress. Try again after it finishes.")
            return result
        except SceneError as exc:
            raise IntentValidationError([str(exc)]) from exc

    async def get_mcp_tool_sources(node_id: str):
        _node(document, node_id)
        return _source_rows(document, node_id)

    async def get_mcp_tool_status(node_id: str):
        node = _node(document, node_id)
        data = core.state_data(node)
        current, error = False, data.get("error", "")
        if core.configured(node):
            try:
                current = core.prepare(document, node).current
            except SceneError as exc:
                error = str(exc)
        return {"configured": core.configured(node), "current": current,
                "status": data.get("status", "idle"), "error": error}

    async def preview_mcp_tool(node_id: str):
        try:
            token, step, prepared = _preview(runtime, node_id)
            saved = core.state_data(document.nodes[node_id])["config"]
            config = require_server(settings, saved["serverId"], saved["toolName"])
            return {
                "previewToken": token, "serverName": config.name, "toolName": saved["toolName"],
                "arguments": _redact(prepared.arguments, config), "inputHash": step["inputHash"],
                "requiresApproval": config.approval != "auto", "scopes": sorted(config.scopes),
                "warnings": ["Running this tool can change the connected service. Undo restores canvas data only."],
            }
        except SceneError as exc:
            raise IntentValidationError([str(exc)]) from exc

    @serialize_mutation
    async def start_tool(node_id: str, preview_token: str, run_id: str):
        token, step, _prepared = _preview(runtime, node_id)
        if token != preview_token:
            raise SceneError("The MCP inputs, settings or output changed. Preview the tool again.")
        if len(document.graph_runs) >= RUN_HISTORY_LIMIT:
            oldest = next((run for run in document.graph_runs if can_prune_run(run, document.graph_runs)), None)
            if oldest is None:
                raise SceneError("Resolve an unfinished graph run before starting another MCP request.")
            document.graph_runs.remove(oldest)
        step.update(status="pending", detail="Waiting for a durable request checkpoint.")
        run = {"runId": run_id, "previewHash": token, "selectionHash": _digest([node_id]),
               "startedAt": datetime.now(timezone.utc).isoformat(), "status": "running", "steps": [step], "maxConcurrency": 1}
        document.graph_runs.append(run)
        document.graph_run_epoch = uuid.uuid4()
        bus.mcp_active_run_id = run_id

        def apply_result(payload: dict, request_id: str, prepared: Any, started_at: str) -> str:
            node = _node(document, node_id)
            document.record_command(
                "runMcpTool", "user", lambda: core.commit_result(
                    node, payload, request_id=request_id, input_hash=prepared.input_hash,
                    sources=prepared.sources, started_at=started_at, finished_at=datetime.now(timezone.utc).isoformat(),
                ), node_ids=(node_id,), run_id=run_id,
            )
            latest = core.state_data(node)["latest"]
            step["outputVersion"] = latest["version"]
            return core.output_hash(node)

        try:
            try:
                await checkpoint()
                await runtime.execute(step, checkpoint, apply_result, reviewed=True)
                run["status"] = "partial" if step["status"] == "failed" else "completed"
            except asyncio.CancelledError:
                if "externalAttempt" not in step:
                    step.update(status="cancelled", detail="MCP request cancelled before dispatch.")
                run["status"] = "reconciliation-required" if step["status"] == "reconciliation-required" else "cancelled"
            except SceneError as exc:
                if "externalAttempt" not in step:
                    step.update(status="blocked", detail=str(exc))
                run["status"] = "reconciliation-required" if step["status"] == "reconciliation-required" else "partial"
            run["finishedAt"] = datetime.now(timezone.utc).isoformat()
            await checkpoint()
            return _public_run(run)
        except BaseException:
            run["status"] = "reconciliation-required" if step["status"] == "reconciliation-required" else "interrupted"
            raise
        finally:
            bus.mcp_active_run_id = None
            await bus.publish("scene")

    async def run_mcp_tool(node_id: str, preview_token: str, operation_id: str):
        if not RUN_ID.fullmatch(operation_id):
            raise IntentValidationError(["Use an operation ID of 8–100 letters, digits, hyphens or underscores."])
        existing = next((run for run in document.graph_runs if run["runId"] == operation_id), None)
        if existing:
            if existing["previewHash"] != preview_token or existing["selectionHash"] != _digest([node_id]):
                raise IntentValidationError(["This operation ID belongs to a different MCP request."])
            return _public_run(existing)
        try:
            result = await start_tool(node_id, preview_token, operation_id)
            if result is None:
                raise SceneError("Another graph operation is in progress. Try again after it finishes.")
            return result
        except SceneError as exc:
            raise IntentValidationError([str(exc)]) from exc

    async def cancel_mcp_tool(node_id: str):
        return {"ok": runtime.cancel(node_id)}

    async def get_mcp_tool_approvals():
        return [copy.deepcopy(row) for row, future in runtime.approvals.values() if not future.done()]

    async def approve_mcp_tool(approval_id: str, approved: bool):
        pending = runtime.approvals.get(approval_id)
        if pending is None or pending[1].done():
            return {"ok": False}
        pending[1].set_result(approved)
        return {"ok": True}

    async def get_mcp_tool_attempts(node_id: str):
        _node(document, node_id)
        return [{"requestId": step["externalAttempt"]["requestId"], "runId": run["runId"],
                 "status": step["status"], "phase": step["externalAttempt"]["phase"],
                 "toolName": step["externalAttempt"]["tool"], "detail": step.get("detail", "")}
                for run in reversed(document.graph_runs) for step in run["steps"]
                if step.get("nodeId") == node_id and "externalAttempt" in step]

    @serialize_mutation
    async def reconcile_attempt(node_id: str, request_id: str):
        node = _node(document, node_id)
        if node.pending_request_id:
            raise SceneError("Wait for the active MCP request to finish before reconciling it.")
        matches = [(run, step) for run in document.graph_runs for step in run["steps"]
                   if step.get("nodeId") == node_id and step.get("externalAttempt", {}).get("requestId") == request_id]
        if len(matches) != 1:
            raise SceneError("Choose the saved request ID for this MCP node.")
        run, step = matches[0]
        if step["externalAttempt"]["phase"] == "reconciled":
            return {"ok": True}
        if step["status"] != "reconciliation-required":
            raise SceneError("This request does not need reconciliation.")
        step["externalAttempt"]["phase"] = "reconciled"
        step.update(status="failed", detail="The user checked the remote outcome. No request was repeated.")
        if not any(item["status"] == "reconciliation-required" for item in run["steps"]):
            run["status"] = "partial"
        if not any(item.get("nodeId") == node_id and item["status"] == "reconciliation-required"
                   for record in document.graph_runs for item in record["steps"]):
            core.set_status(node, "idle")
        document.graph_run_epoch = uuid.uuid4()
        await checkpoint()
        await bus.publish("scene")
        return {"ok": True}

    async def reconcile_mcp_tool(node_id: str, request_id: str):
        try:
            result = await reconcile_attempt(node_id, request_id)
            if result is None:
                raise SceneError("Another graph operation is in progress. Try again after it finishes.")
            return result
        except SceneError as exc:
            raise IntentValidationError([str(exc)]) from exc

    bus.register_intent("scene", "listMcpTools", list_mcp_tools, args_schema=McpServerArgs)
    bus.register_intent("scene", "configureMcpTool", configure_mcp_tool, args_schema=ConfigureMcpArgs)
    bus.register_intent("scene", "getMcpToolSources", get_mcp_tool_sources, args_schema=McpNodeArgs)
    bus.register_intent("scene", "getMcpToolStatus", get_mcp_tool_status, args_schema=McpNodeArgs)
    bus.register_intent("scene", "previewMcpTool", preview_mcp_tool, args_schema=McpNodeArgs)
    bus.register_intent("scene", "runMcpTool", run_mcp_tool, args_schema=RunMcpArgs)
    bus.register_intent("scene", "cancelMcpTool", cancel_mcp_tool, args_schema=McpNodeArgs)
    bus.register_intent("scene", "getMcpToolApprovals", get_mcp_tool_approvals, args_schema=NoMcpArgs)
    bus.register_intent("scene", "approveMcpTool", approve_mcp_tool, args_schema=ApproveMcpArgs)
    bus.register_intent("scene", "getMcpToolAttempts", get_mcp_tool_attempts, args_schema=McpNodeArgs)
    bus.register_intent("scene", "reconcileMcpTool", reconcile_mcp_tool, args_schema=ReconcileMcpArgs)
