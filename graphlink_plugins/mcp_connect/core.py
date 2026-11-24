"""Pure MCP tool nodes: explicit inputs, source freshness and verifiable receipts.

Configuration never grants permission or invokes a tool. The outer execution
adapter owns approvals, server identity checks and the durable dispatch fence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import ModelState, NoteState, PersistedPluginState, PluginStateSnapshot

from .schema import canonical, check_schema, pointer, validate

KIND = "mcp_connect.tool"
MAX_RESULT_BYTES = 512 * 1024
# Match the host's plugin state persistence cap; old results are discarded
# oldest-first before a completed call can exceed the durable store's limit.
MAX_STATE_BYTES = 1024 * 1024
HISTORY_LIMIT = 10
_DIGEST = re.compile(r"[a-f0-9]{64}")
_KEY = re.compile(r"[a-f0-9]{32}")
_STATUS = {"idle", "connecting", "awaiting-approval", "running", "completed", "error", "reconciliation-required"}


@dataclass(frozen=True)
class PreparedCall:
    arguments: dict[str, Any]
    sources: list[dict[str, Any]]
    input_hash: str
    current: bool


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value, limit=MAX_STATE_BYTES)).hexdigest()


def new_state() -> dict[str, Any]:
    return {
        "version": 1, "nodeKey": uuid4().hex, "config": None,
        "latest": None, "history": [], "status": "idle", "error": "",
    }


def state_data(node: SceneNode) -> dict[str, Any]:
    if node.kind != KIND or type(node.state) not in (PluginStateSnapshot, PersistedPluginState):
        raise SceneError("Choose an MCP Connect tool node.")
    if isinstance(node.state, PersistedPluginState) and (
        node.state.unresolved_asset_ref or node.state.invalid_asset_descriptor
    ):
        raise SceneError("The saved MCP result asset is unavailable. Restore it and reopen this graph.")
    data = node.state.data
    if (not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("nodeKey"), str)
            or not _KEY.fullmatch(data["nodeKey"])):
        raise SceneError("The saved MCP node has an unsupported version or an invalid identity.")
    if data.get("status", "idle") not in _STATUS or not isinstance(data.get("error", ""), str):
        raise SceneError("The saved MCP status is invalid.")
    return data


def configured(node: SceneNode) -> bool:
    if node.kind != KIND or type(node.state) not in (PluginStateSnapshot, PersistedPluginState):
        return False
    data = node.state.data
    return isinstance(data, dict) and isinstance(data.get("config"), dict)


def _text(value: Any, label: str, maximum: int = 256, *, optional: bool = False) -> str:
    if not isinstance(value, str) or (not optional and not value.strip()) or len(value) > maximum or "\x00" in value:
        raise SceneError(f"The MCP {label} is invalid.")
    return value


def _configuration(node: SceneNode) -> dict[str, Any]:
    config = state_data(node).get("config")
    if not isinstance(config, dict) or type(config.get("version")) is not int or config["version"] != 1:
        raise SceneError("Choose an MCP server and tool before running this node.")
    if not isinstance(config.get("bindingId"), str) or not _KEY.fullmatch(config["bindingId"]):
        raise SceneError("The saved MCP configuration identity is invalid.")
    for key in ("serverId", "serverName", "toolName"):
        _text(config.get(key), key)
    if not isinstance(config.get("arguments"), dict) or not isinstance(config.get("bindings"), dict):
        raise SceneError("The saved MCP arguments or bindings are invalid.")
    if len(config["bindings"]) > 64:
        raise SceneError("An MCP tool supports at most 64 connected input fields.")
    check_schema(config.get("inputSchema"))
    if config.get("outputSchema") is not None:
        check_schema(config["outputSchema"])
    canonical(config["arguments"])
    for field, binding in config["bindings"].items():
        _text(field, "input field", 256)
        _validate_binding(binding)
    return config


def configuration(node: SceneNode) -> dict[str, Any]:
    """A validated copy; callers cannot mutate a saved snapshot accidentally."""
    return copy.deepcopy(_configuration(node))


def _validate_binding(binding: Any) -> None:
    if not isinstance(binding, dict) or set(binding) != {"sourceId", "output", "pointer"}:
        raise SceneError("MCP input bindings require a source, output kind and JSON Pointer.")
    _text(binding.get("sourceId"), "source node", 128)
    if binding.get("output") not in {"text", "json", "table"}:
        raise SceneError("Choose Text, JSON or Table for the MCP source output.")
    path = binding.get("pointer")
    if not isinstance(path, str) or len(path) > 2048 or (path and not path.startswith("/")) or re.search(r"~(?![01])", path):
        raise SceneError("Use a valid JSON Pointer for the MCP source selection.")


def _binding_source(document: Any, node: SceneNode, binding: dict) -> SceneNode:
    source_id = binding["sourceId"]
    source = document.nodes.get(source_id)
    if source is None or source is node:
        raise SceneError("The MCP source no longer exists or points to itself.")
    if not any(edge.source == source_id and edge.target == node.id for edge in document.edges.values()):
        raise SceneError("The MCP source is disconnected. Connect it to this tool node again.")
    return source


def dependencies(document: Any, node: SceneNode) -> tuple[str, ...]:
    config = _configuration(node)
    return tuple(dict.fromkeys(_binding_source(document, node, binding).id for binding in config["bindings"].values()))


def _all_dependencies(document: Any, node: SceneNode) -> tuple[str, ...]:
    from backend.domain.table_workflows import table_output

    if configured(node):
        return dependencies(document, node)
    if isinstance(node.state, ModelState):
        return (node.state.model_binding.source_id,)
    if isinstance(node.state, NoteState):
        if node.state.model_text_input is not None:
            return (node.state.model_text_input.source_id,)
        if node.state.text_input_source_id:
            return (node.state.text_input_source_id,)
    output = table_output(node)
    return (output.source_id,) if output is not None else ()


def _assert_acyclic(document: Any, node: SceneNode) -> None:
    active: set[str] = set()
    visited: set[str] = set()

    def visit(current: SceneNode, depth: int) -> None:
        if current.id in active:
            raise SceneError("The MCP input bindings contain a dependency cycle.")
        if current.id in visited:
            return
        if depth > 128:
            raise SceneError("The MCP input chain exceeds the supported depth.")
        active.add(current.id)
        for source_id in _all_dependencies(document, current):
            source = document.nodes.get(source_id)
            if source is not None:
                visit(source, depth + 1)
        active.remove(current.id)
        visited.add(current.id)

    visit(node, 0)


def configure(document: Any, node: SceneNode, tool: dict, arguments: dict, bindings: dict) -> None:
    """Set an explicit configuration atomically; retain the last output as stale."""
    data = state_data(node)
    if data.get("status") in {"connecting", "awaiting-approval", "running", "reconciliation-required"}:
        raise SceneError("Resolve the existing MCP request before reconfiguring this node.")
    if not isinstance(tool, dict) or not isinstance(arguments, dict) or not isinstance(bindings, dict):
        raise SceneError("Choose a tool and provide JSON object arguments and bindings.")
    annotations = tool.get("annotations", {})
    if not isinstance(annotations, dict):
        annotations = {}
    config = {
        "version": 1, "bindingId": uuid4().hex,
        "serverId": tool.get("serverId"), "serverName": tool.get("serverName"),
        "toolName": tool.get("toolName", tool.get("name")),
        "description": _text(tool.get("description", ""), "description", 4000, optional=True),
        "inputSchema": copy.deepcopy(tool.get("inputSchema")),
        "outputSchema": copy.deepcopy(tool.get("outputSchema")),
        "annotations": {key: value for key, value in annotations.items()
                        if key in {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"} and type(value) is bool},
        "arguments": copy.deepcopy(arguments), "bindings": copy.deepcopy(bindings),
    }
    previous_config = data.get("config")
    if (isinstance(previous_config, dict) and isinstance(previous_config.get("bindingId"), str)
            and _KEY.fullmatch(previous_config["bindingId"]) and all(
        previous_config.get(key) == value for key, value in config.items() if key != "bindingId"
    )):
        config["bindingId"] = previous_config["bindingId"]
    canonical(config, limit=MAX_STATE_BYTES)
    proposed = copy.copy(node)
    proposed.state = PluginStateSnapshot.from_data({**data, "config": config, "status": "idle", "error": ""})
    _configuration(proposed)
    for binding in config["bindings"].values():
        _binding_source(document, node, binding)
    # Check against the tentative configuration without mutating the live graph.
    shadow = copy.copy(document)
    shadow.nodes = {**document.nodes, node.id: proposed}
    _assert_acyclic(shadow, proposed)
    canonical(proposed.state.data, limit=MAX_STATE_BYTES)
    node.state = proposed.state
    node.title = config["toolName"]


def config_hash(node: SceneNode) -> str:
    config = copy.deepcopy(_configuration(node))
    # Runtime IDs are reminted on reopen. A fresh bindingId pins each explicit
    # reconfiguration; selectors and literal values remain part of the hash.
    for binding in config["bindings"].values():
        binding.pop("sourceId")
    return fingerprint([state_data(node)["nodeKey"], config])


def _stable_sources(sources: list[dict]) -> list[dict]:
    return [{key: value for key, value in source.items() if key != "sourceId"} for source in sources]


def _result_body(result: dict) -> dict:
    return {key: result[key] for key in ("version", "text", "structuredContent", "content", "isError")}


def _receipt_body(provenance: dict) -> dict:
    return {**{key: value for key, value in provenance.items() if key != "receiptHash"},
            "sources": _stable_sources(provenance["sources"])}


def valid_result(result: Any) -> bool:
    try:
        if (not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] < 1
                or not isinstance(result.get("text"), str) or not isinstance(result.get("content"), list)
                or any(not isinstance(block, dict) for block in result["content"])
                or type(result.get("isError")) is not bool or "structuredContent" not in result):
            return False
        canonical(result, limit=MAX_RESULT_BYTES)
        provenance = result["provenance"]
        if not isinstance(provenance, dict) or not isinstance(provenance.get("sources"), list):
            return False
        for key in ("serverId", "serverName", "toolName", "requestId"):
            _text(provenance.get(key), key)
        for key in ("inputHash", "configHash", "resultHash", "receiptHash"):
            if not isinstance(provenance.get(key), str) or not _DIGEST.fullmatch(provenance[key]):
                return False
        for key in ("startedAt", "finishedAt"):
            timestamp = provenance[key]
            if not isinstance(timestamp, str) or len(timestamp) > 40 or datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo is None:
                return False
        if len(provenance["sources"]) > 64 or any(not isinstance(source, dict) for source in provenance["sources"]):
            return False
        return (fingerprint(_result_body(result)) == provenance["resultHash"]
                and fingerprint(_receipt_body(provenance)) == provenance["receiptHash"])
    except (SceneError, KeyError, TypeError, ValueError, OverflowError):
        return False


def output_hash(node: SceneNode) -> str:
    data = state_data(node)
    latest = data.get("latest")
    if latest is not None and not valid_result(latest):
        raise SceneError("The saved MCP result receipt is damaged. Restore a verified graph version.")
    identity = None if latest is None else [latest["provenance"]["resultHash"], latest["provenance"]["receiptHash"]]
    return fingerprint([identity, node.content])


def saved_text(node: SceneNode) -> str:
    data = state_data(node)
    latest = data.get("latest")
    if latest is None:
        return ""
    if not valid_result(latest):
        raise SceneError("The saved MCP result receipt is damaged.")
    return latest["text"]


def _read_source(document: Any, node: SceneNode, output: str) -> Any:
    from backend.domain.table_workflows import table_output

    if node.kind == KIND:
        if not prepare(document, node).current:
            raise SceneError("Run the upstream MCP tool first; its output is not current.")
        latest = state_data(node)["latest"]
        if output == "text":
            return latest["text"]
        if output == "json":
            if latest["structuredContent"] is not None:
                return copy.deepcopy(latest["structuredContent"])
            try:
                return json.loads(latest["text"])
            except (ValueError, TypeError) as exc:
                raise SceneError("This MCP output has no structured JSON result.") from exc
        return table_rows(node)
    if output == "table":
        table = document._read_table(node.id)
        ancestor, visited = node, set()
        while (binding := table_output(ancestor)) is not None:
            if ancestor.id in visited or len(visited) >= 128:
                raise SceneError("The table input contains a cycle or exceeds the supported depth.")
            visited.add(ancestor.id)
            if document._read_table(binding.source_id).fingerprint(binding.label_column, binding.value_column) != binding.current.source_hash:
                raise SceneError("Run the upstream table first; its output is not current.")
            ancestor = document.nodes[binding.source_id]
        return [dict(zip(table.headers, row, strict=True)) for row in table.rows]
    document.require_current_model_source(node.id, check_text_inputs=True)
    text = document._text_output(node.id)
    if output == "text":
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise SceneError("The connected text is not valid JSON. Choose Text or provide a JSON output.") from exc


def source_output(document: Any, node: SceneNode, output: str) -> Any:
    if output not in {"text", "json", "table"}:
        raise SceneError("Choose Text, JSON or Table as the output.")
    return _read_source(document, node, output)


def _journal_requires_refresh(document: Any, node: SceneNode, latest: Any, active_request_id: str) -> bool:
    """Undoable node state cannot erase the durable external-effect history."""
    from backend.domain.graph_run_external import restore_external_attempt

    receipt = latest.get("provenance") if isinstance(latest, dict) else None
    latest_request = receipt.get("requestId") if isinstance(receipt, dict) else None

    def timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str) or len(value) > 40:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else None
        except ValueError:
            return None

    latest_finished = timestamp(receipt.get("finishedAt")) if isinstance(receipt, dict) else None
    refresh = False
    for run in document.graph_runs:
        for step in run.get("steps", []):
            if not isinstance(step, dict) or step.get("nodeId") != node.id:
                continue
            attempt = restore_external_attempt(step.get("externalAttempt"))
            if (attempt is not None and active_request_id and attempt["requestId"] == active_request_id
                    and attempt["phase"] == "dispatched" and step.get("status") == "running"):
                refresh = True  # Only this caller's live pre/apply validation may proceed.
                continue
            if (step.get("status") in {"running", "reconciliation-required"}
                    or "externalAttempt" in step and (attempt is None or attempt["phase"] in {"dispatched", "reconciliation-required"})):
                raise SceneError("This MCP tool has an unresolved request. Reconcile its recorded outcome before using or refreshing the output.")
            if attempt is not None and attempt["phase"] == "reconciled":
                started = timestamp(step.get("startedAt"))
                if (attempt["requestId"] == latest_request or started is None or latest_finished is None
                        or started >= latest_finished):
                    refresh = True
    return refresh


def prepare(document: Any, node: SceneNode, *, active_request_id: str = "") -> PreparedCall:
    config = _configuration(node)
    _assert_acyclic(document, node)
    data = state_data(node)
    journal_refresh = _journal_requires_refresh(document, node, data.get("latest"), active_request_id)
    if data.get("status") == "reconciliation-required":
        raise SceneError("This MCP request has an uncertain outcome. Reconcile it before another call.")
    arguments = copy.deepcopy(config["arguments"])
    sources: list[dict] = []
    for field, binding in sorted(config["bindings"].items()):
        source = _binding_source(document, node, binding)
        selected = pointer(_read_source(document, source, binding["output"]), binding["pointer"])
        canonical(selected)
        arguments[field] = copy.deepcopy(selected)
        sources.append({
            "argument": field, "sourceId": source.id, "sourceKind": source.kind,
            "output": binding["output"], "pointer": binding["pointer"], "valueHash": fingerprint(selected),
        })
    validate(arguments, config["inputSchema"])
    digest = fingerprint([arguments, _stable_sources(sources)])
    latest = data.get("latest")
    if latest is not None and not valid_result(latest):
        raise SceneError("The saved MCP result receipt is damaged. Restore a verified graph version.")
    if latest is not None and node.content != latest["text"]:
        raise SceneError("The MCP output has manual edits. Restore the saved output before running again.")
    same = bool(latest is not None and not latest["isError"] and not data.get("requiresRefresh", False) and not journal_refresh
                and data.get("status") not in {"connecting", "awaiting-approval", "running", "error"}
                and latest["provenance"]["inputHash"] == digest
                and latest["provenance"]["configHash"] == config_hash(node))
    return PreparedCall(arguments, sources, digest, same)


def commit_result(
    node: SceneNode, payload: dict, *, request_id: str, input_hash: str,
    sources: list[dict], started_at: str, finished_at: str,
) -> str:
    """Commit a validated response after the caller has rechecked all input pins."""
    config = _configuration(node)
    data = state_data(node)
    canonical(payload, limit=MAX_RESULT_BYTES)
    content = payload.get("content", [])
    if not isinstance(content, list) or any(not isinstance(block, dict) for block in content):
        raise SceneError("The MCP response has invalid content blocks.")
    is_error = payload.get("isError", False)
    if type(is_error) is not bool:
        raise SceneError("The MCP response has an invalid error flag.")
    structured = payload.get("structuredContent")
    if not is_error and config.get("outputSchema") is not None:
        if structured is None:
            raise SceneError("The MCP tool omitted its advertised structured output.")
        validate(structured, config["outputSchema"], label="Tool output", limit=MAX_RESULT_BYTES)
    text = "\n".join(block["text"] for block in content if block.get("type") == "text" and isinstance(block.get("text"), str))
    if not text and isinstance(payload.get("text"), str):
        text = payload["text"]
    if not text and structured is not None:
        text = json.dumps(structured, ensure_ascii=False, indent=2, allow_nan=False)
    previous = data.get("latest")
    history = data.get("history", [])
    if not isinstance(history, list) or len(history) > HISTORY_LIMIT or any(not valid_result(item) for item in history):
        raise SceneError("The saved MCP result history is damaged.")
    if previous is not None and not valid_result(previous):
        raise SceneError("The saved MCP result receipt is damaged.")
    result = {
        "version": previous["version"] + 1 if previous else 1, "text": text,
        "structuredContent": copy.deepcopy(structured), "content": copy.deepcopy(content), "isError": is_error,
    }
    provenance = {
        "requestId": request_id, "startedAt": started_at, "finishedAt": finished_at,
        "serverId": config["serverId"], "serverName": config["serverName"], "toolName": config["toolName"],
        "inputHash": input_hash, "configHash": config_hash(node), "resultHash": fingerprint(result),
        "sources": copy.deepcopy(sources),
    }
    provenance["receiptHash"] = fingerprint(_receipt_body(provenance))
    result["provenance"] = provenance
    if not valid_result(result):
        raise SceneError("The MCP response cannot be saved with a valid bounded receipt.")
    history = ([*history, previous] if previous is not None else history)[-HISTORY_LIMIT:]
    updated = {**data, "latest": result, "history": history, "requiresRefresh": False,
               "status": "error" if is_error else "completed",
               "error": "The tool reported an error. Review its saved response before retrying." if is_error else ""}
    while history:
        try:
            canonical(updated, limit=MAX_STATE_BYTES)
            break
        except SceneError:
            history.pop(0)
    canonical(updated, limit=MAX_STATE_BYTES)
    node.state = PluginStateSnapshot.from_data(updated)
    node.content = text
    return output_hash(node)


def set_status(node: SceneNode, status: str, error: str = "") -> None:
    if status not in _STATUS or not isinstance(error, str) or len(error) > 2000:
        raise SceneError("The MCP request status is invalid.")
    data = {**state_data(node), "status": status, "error": error}
    if status == "reconciliation-required":
        data["requiresRefresh"] = True
    node.state = PluginStateSnapshot.from_data(data)


def table_rows(node: SceneNode) -> list[dict[str, Any]]:
    """Expose homogeneous scalar object rows, never flatten nested data silently."""
    latest = state_data(node).get("latest")
    if not valid_result(latest) or latest["isError"]:
        raise SceneError("This MCP tool has no successful table result.")
    value = latest["structuredContent"]
    if isinstance(value, dict):
        value = value.get("rows", value.get("items", value.get("data")))
    if not isinstance(value, list) or not value or len(value) > 10_000 or not isinstance(value[0], dict):
        raise SceneError("The MCP output is not an array of table rows.")
    headers = list(value[0])
    if not 1 <= len(headers) <= 128 or any(not key or len(key) > 160 for key in headers):
        raise SceneError("The MCP table has invalid column names.")
    if any(not isinstance(row, dict) or set(row) != set(headers) or any(
        cell is not None and type(cell) not in (str, bool, int, float) for cell in row.values()
    ) for row in value):
        raise SceneError("MCP table rows must share columns and contain only scalar values.")
    return copy.deepcopy(value)


def table_csv(node: SceneNode) -> str:
    import csv
    import io

    rows = table_rows(node)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    headers = list(rows[0])
    writer.writerow(headers)
    writer.writerows(["" if row[key] is None else str(row[key]).lower() if type(row[key]) is bool else row[key]
                     for key in headers] for row in rows)
    return output.getvalue().removesuffix("\r\n")


def remap_sources(document: Any, ids: dict[str, str]) -> None:
    """Rebind source IDs after restore without changing stable fingerprints."""
    for node in document.nodes.values():
        if node.kind != KIND or type(node.state) not in (PluginStateSnapshot, PersistedPluginState):
            continue
        if isinstance(node.state, PersistedPluginState) and (node.state.unresolved_asset_ref or node.state.invalid_asset_descriptor):
            continue
        data = node.state.data
        if not isinstance(data, dict):
            continue
        config = data.get("config")
        if isinstance(config, dict) and isinstance(config.get("bindings"), dict):
            for binding in config["bindings"].values():
                if isinstance(binding, dict) and isinstance(binding.get("sourceId"), str):
                    binding["sourceId"] = ids.get(binding["sourceId"], "")
        results = [data.get("latest")]
        if isinstance(data.get("history"), list):
            results += data["history"]
        for result in results:
            provenance = result.get("provenance") if isinstance(result, dict) else None
            if isinstance(provenance, dict) and isinstance(provenance.get("sources"), list):
                for source in provenance["sources"]:
                    if isinstance(source, dict) and isinstance(source.get("sourceId"), str):
                        source["sourceId"] = ids.get(source["sourceId"], "")
        if data.get("status") in {"connecting", "awaiting-approval"}:
            data.update(status="idle", error="")
        if data.get("status") == "running":
            data.update(status="reconciliation-required", requiresRefresh=True,
                        error="The previous request was interrupted. Review its outcome before retrying.")
        node.state = PluginStateSnapshot.from_data(data)
