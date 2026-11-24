"""Session-owned MCP connections, admission and durable tool dispatch.

Discovery never calls a tool. Tool calls have one durable request fence and
are never retried, including when cancellation races with a remote response.
Credentials remain in Settings; neither the node nor the run journal receives
the connection configuration.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from backend.domain.graph_run_external import (
    ExternalCall, ExternalResult, dispatch_external_attempt, drain_cancelled_task,
)
from backend.domain.model import SceneError
from backend.mcp_client import McpServerConfig, create_mcp_client
from graphlink_plugins.mcp_connect import core


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def connection_hash(config: McpServerConfig) -> str:
    """A private comparison hash, never a serialized credential dictionary."""
    return _digest(config.to_dict())


def require_server(settings: Any, server_id: str, tool_name: str = "") -> McpServerConfig:
    if settings is None:
        raise SceneError("Configure an MCP server in Settings before connecting.")
    raw = next((entry for entry in settings.get_mcp_servers() if entry.get("id") == server_id), None)
    if raw is None:
        raise SceneError("This MCP server was removed. Choose a configured server.")
    config = McpServerConfig.from_dict(raw)
    if not config.enabled:
        raise SceneError("This MCP server is disabled. Enable it in Settings before connecting.")
    if config.approval not in {"auto", "once", "always"}:
        raise SceneError("Choose a valid MCP approval policy in Settings.")
    if tool_name and config.enabled_tools and tool_name not in config.enabled_tools:
        raise SceneError("This tool is not enabled for its MCP server. Review the tool allowlist in Settings.")
    return config


def require_plugin(settings: Any) -> None:
    if settings is None or not settings.get_plugin_grants().get("mcp_connect", False):
        raise SceneError("Enable MCP Connect in Settings > Plugins before configuring or running a tool.")


def _redact(value: Any, config: McpServerConfig) -> Any:
    """Do not let an echoing server publish the saved connection's secrets."""
    secrets = set(config.env.values()) | set(config.headers.values())
    secrets |= {value[7:] for value in config.headers.values() if value.lower().startswith("bearer ")}
    secrets.discard("")

    def visit(item: Any) -> Any:
        if isinstance(item, str):
            for secret in sorted(secrets, key=len, reverse=True):
                item = item.replace(secret, "[redacted]")
            return item
        if isinstance(item, dict):
            return {visit(key): visit(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [visit(child) for child in item]
        return item

    return visit(value)


def _tool_row(spec: Any, config: McpServerConfig) -> dict[str, Any]:
    return _redact({
        "serverId": config.id, "serverName": config.name,
        "name": spec.name, "toolName": spec.name, "title": getattr(spec, "title", ""),
        "description": spec.description, "inputSchema": spec.input_schema,
        "outputSchema": getattr(spec, "output_schema", None),
        "annotations": getattr(spec, "annotations", {}),
        "allowed": not config.enabled_tools or spec.name in config.enabled_tools,
    }, config)


async def _thread_call(client: Any, function: Callable, *args: Any) -> Any:
    """Drain workers on cancellation; a detached thread must not dispatch later."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Closing stdio terminates the child; HTTP close releases its socket.
        # A connect racing with close may create a resource afterwards, so the
        # caller closes again after this worker has actually stopped.
        await drain_cancelled_task(asyncio.create_task(asyncio.to_thread(client.close)))
        await drain_cancelled_task(worker)
        raise


async def _close_client(client: Any) -> None:
    task = asyncio.create_task(asyncio.to_thread(client.close))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await drain_cancelled_task(task)
        raise
    except Exception:
        # The result/fence is already authoritative; close errors are never
        # grounds for another tools/call or for exposing server stderr.
        pass


class McpConnectRuntime:
    def __init__(self, bus: Any, document: Any, settings: Any):
        self.bus, self.document, self.settings = bus, document, settings
        self.catalogs: dict[str, tuple[str, list[dict[str, Any]]]] = {}
        self.approvals: dict[str, tuple[dict[str, Any], asyncio.Future[bool]]] = {}
        self.approved_tools: set[tuple[str, str, str]] = set()
        self.active: dict[str, asyncio.Task] = {}
        self.discovery_guard = asyncio.Semaphore(3)

    def server_rows(self) -> list[dict[str, Any]]:
        if self.settings is None:
            return []
        return [{"id": entry["id"], "name": entry["name"], "enabled": entry.get("enabled", True),
                 "transport": entry.get("transport", "stdio")}
                for entry in self.settings.get_mcp_servers()]

    async def discover(self, server_id: str) -> list[dict[str, Any]]:
        config = require_server(self.settings, server_id)
        async with self.discovery_guard:
            client = None
            try:
                client = create_mcp_client(config)
                await _thread_call(client, client.connect)
                specs = await _thread_call(client, client.list_tools)
                rows = [_tool_row(spec, config) for spec in specs]
                if connection_hash(require_server(self.settings, server_id)) != connection_hash(config):
                    raise SceneError("The MCP connection changed during discovery. Discover its tools again.")
                self.catalogs[server_id] = (connection_hash(config), rows)
                return copy.deepcopy(rows)
            except asyncio.CancelledError:
                raise
            except SceneError:
                raise
            except Exception as exc:
                raise SceneError("Could not discover MCP tools. Check the server command or URL, credentials and timeout in Settings.") from exc
            finally:
                if client is not None:
                    await _close_client(client)

    async def tool_metadata(self, server_id: str, tool_name: str) -> dict[str, Any]:
        config = require_server(self.settings, server_id, tool_name)
        cached = self.catalogs.get(server_id)
        rows = cached[1] if cached and cached[0] == connection_hash(config) else await self.discover(server_id)
        tool = next((row for row in rows if row["name"] == tool_name and row["allowed"]), None)
        if tool is None:
            raise SceneError("This MCP tool is unavailable. Refresh the tool browser and choose an enabled tool.")
        return copy.deepcopy(tool)

    def pin_step(self, node: Any, step: dict[str, Any]) -> None:
        require_plugin(self.settings)
        saved = core.state_data(node)["config"]
        config = require_server(self.settings, saved["serverId"], saved["toolName"])
        step.update(endpointHash=connection_hash(config), provider="mcp:" + config.id,
                    tool=saved["toolName"])

    async def _approve(self, node: Any, config: McpServerConfig, arguments: dict[str, Any], *,
                       reviewed: bool, step: dict[str, Any], checkpoint: Callable[[], Awaitable[bool]]) -> None:
        saved = core.state_data(node)["config"]
        capability_hash = _digest([saved.get(field) for field in ("inputSchema", "outputSchema", "annotations")])
        key = (connection_hash(config), saved["toolName"], capability_hash)
        if reviewed or config.approval == "auto" or (config.approval == "once" and key in self.approved_tools):
            step["approval"] = "reviewed" if reviewed else "auto" if config.approval == "auto" else "cached"
            step.pop("approvalId", None)
            if reviewed and config.approval == "once":
                self.approved_tools.add(key)
            return
        approval_id = "mcp-" + uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        row = {"id": approval_id, "nodeId": node.id, "serverName": config.name, "toolName": key[1],
               "arguments": _redact(copy.deepcopy(arguments), config), "scopes": sorted(config.scopes)}
        self.approvals[approval_id] = (row, future)
        core.set_status(node, "awaiting-approval")
        step.update(approval="pending", approvalId=approval_id)
        try:
            # Journal the approval identity, never its arguments. A crash
            # here is pre-dispatch; a resumed run must obtain fresh approval.
            if not await checkpoint():
                raise SceneError("Could not save the MCP approval checkpoint.")
            await self.bus.publish("scene")
            approved = await asyncio.wait_for(future, timeout=600)
            if not approved:
                step["approval"] = "denied"
                raise SceneError("The MCP tool request was denied. No tool call was sent.")
            step["approval"] = "approved"
            if config.approval == "once":
                self.approved_tools.add(key)
        except asyncio.CancelledError:
            step["approval"] = "cancelled"
            raise
        except TimeoutError as exc:
            step["approval"] = "expired"
            raise SceneError("MCP approval expired. Preview the tool again when you are ready.") from exc
        finally:
            self.approvals.pop(approval_id, None)

    def cancel(self, node_id: str) -> bool:
        task = self.active.get(node_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def disconnect(self) -> None:
        self.approved_tools.clear()
        for _row, future in tuple(self.approvals.values()):
            if not future.done():
                future.set_result(False)
        for task in tuple(self.active.values()):
            if not task.done():
                task.cancel()

    async def execute(
        self, step: dict[str, Any], checkpoint: Callable[[], Awaitable[bool]],
        apply_result: Callable[[dict[str, Any], str, Any, str], str], *, reviewed: bool = False,
    ) -> None:
        """Invoke a saved, freshly validated tool and apply exactly one receipt."""
        node = self.document.nodes.get(step["nodeId"])
        if node is None or not core.configured(node):
            raise SceneError("The MCP node was removed or reconfigured. Preview it again.")
        require_plugin(self.settings)
        if node.id in self.active:
            raise SceneError("This MCP tool is already running.")
        if core.state_data(node).get("status") == "reconciliation-required" or any(
            prior.get("nodeId") == node.id and prior.get("status") == "reconciliation-required"
            for run in self.document.graph_runs for prior in run.get("steps", [])
        ):
            raise SceneError("This tool has an unresolved request. Check its saved request ID before another attempt.")
        saved = core.state_data(node)["config"]
        config = require_server(self.settings, saved["serverId"], saved["toolName"])
        endpoint_hash = connection_hash(config)
        if step.get("endpointHash") != endpoint_hash:
            raise SceneError("The MCP server or permissions changed since preview. Preview the tool again.")

        def validate_live() -> Any:
            current = self.document.nodes.get(node.id)
            if current is not node or core.config_hash(node) != step["configHash"] or core.output_hash(node) != step["targetHash"]:
                raise SceneError("The MCP configuration or output changed. Preview the tool again.")
            require_plugin(self.settings)
            if connection_hash(require_server(self.settings, config.id, saved["toolName"])) != endpoint_hash:
                raise SceneError("MCP permissions or connection settings changed. Preview the tool again.")
            prepared = core.prepare(self.document, node, active_request_id=step.get("externalAttempt", {}).get("requestId", ""))
            if step["inputHash"] and prepared.input_hash != step["inputHash"]:
                raise SceneError("The MCP input changed since preview. Preview the tool again.")
            return prepared

        prepared = validate_live()
        step["inputHash"] = prepared.input_hash
        client = None
        task = asyncio.current_task()
        if task is None:
            raise SceneError("MCP execution requires an active task.")
        self.active[node.id] = task
        node.pending_request_id = "mcp-" + uuid.uuid4().hex
        core.set_status(node, "connecting")
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            await self.bus.publish("scene")
            client = create_mcp_client(config)
            await _thread_call(client, client.connect)
            specs = await _thread_call(client, client.list_tools)
            spec = next((candidate for candidate in specs if candidate.name == saved["toolName"]), None)
            if spec is None:
                raise SceneError("The configured MCP tool is no longer available. Refresh and configure the node again.")
            live_tool = _tool_row(spec, config)
            live_tool["annotations"] = {key: value for key, value in live_tool["annotations"].items()
                                        if key in {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
                                        and type(value) is bool}
            if any(live_tool.get(key) != saved.get(key) for key in ("inputSchema", "outputSchema", "annotations")):
                raise SceneError("The MCP tool schema or capabilities changed. Refresh and configure the node again.")
            prepared = validate_live()
            await self._approve(node, config, prepared.arguments, reviewed=reviewed, step=step, checkpoint=checkpoint)
            prepared = validate_live()
            core.set_status(node, "running")
            await self.bus.publish("scene")

            async def invoke(_request_id: str) -> dict[str, Any]:
                # Permission and input edits can arrive while SQLite commits
                # the fence. Recheck at the actual dispatch boundary too.
                validate_live()
                result = await _thread_call(client, client.call_tool, saved["toolName"], prepared.arguments)
                if getattr(result, "truncated", False):
                    raise SceneError("The MCP response exceeded the result limit; inspect the remote request before retrying.")
                return _redact({
                    "text": result.content, "content": list(getattr(result, "content_blocks", ())),
                    "structuredContent": getattr(result, "structured_content", None), "isError": result.is_error,
                }, config)

            async def apply(payload: dict[str, Any]) -> ExternalResult:
                # A response to obsolete inputs remains an uncertain external
                # attempt, never a replacement for the user's newer work.
                validate_live()
                digest = apply_result(payload, step["externalAttempt"]["requestId"], prepared, started_at)
                return ExternalResult(digest, payload.get("isError") is True)

            await dispatch_external_attempt(
                step, ExternalCall(kind="tool", provider_name="mcp:" + config.id, tool=saved["toolName"],
                                   input_ref="mcp-input:" + prepared.input_hash, input_hash=prepared.input_hash),
                checkpoint=checkpoint, invoke=invoke, apply_result=apply,
            )
        except asyncio.CancelledError:
            core.set_status(node, "reconciliation-required" if "externalAttempt" in step else "error",
                            "The request may have run. Inspect its saved request ID." if "externalAttempt" in step else "MCP tool cancelled before dispatch.")
            raise
        except Exception as exc:
            uncertain = "externalAttempt" in step
            message = ("The external request may have run. Inspect its saved request ID before trying again." if uncertain
                       else str(exc) if isinstance(exc, SceneError)
                       else "Could not connect to the MCP server. Check its settings and discover its tools again.")
            core.set_status(node, "reconciliation-required" if uncertain else "error", message)
            if isinstance(exc, SceneError):
                raise
            raise SceneError(message) from exc
        finally:
            try:
                if client is not None:
                    await _close_client(client)
            finally:
                self.active.pop(node.id, None)
                node.pending_request_id = None
                await self.bus.publish("scene")
