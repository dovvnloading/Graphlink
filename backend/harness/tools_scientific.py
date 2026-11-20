"""Scientific Workbench discovery and verified runs for the workspace agent.

The Settings > Plugins grant is a separate boundary from ToolRegistry scope
approval. Never import an ungranted pack's operation module, and never write
an unverified worker reply into the graph. Each successful run is a native
Workbench node with the same state/receipt as the manual Workbench path.
"""

from __future__ import annotations

import asyncio
import json
from typing import TypedDict
from uuid import uuid4

from api_provider import _raise_if_cancelled
from backend.domain.model import SceneError
from backend.domain.node_states import PluginStateSnapshot
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import CODE_EXECUTE, FS_READ, GRAPH_MUTATE, GRAPH_READ, RunContext, ToolRegistry, ToolResult
from graphlink_plugins.scientific.catalog import PACKS, operations
from graphlink_plugins.scientific.core import readable_result, validated_inputs_with_units
from graphlink_plugins.scientific.data_table import TableState
from graphlink_plugins.scientific.execution import ExecutionFailure, execute_in_process, execution_timeout

class EnabledPack(TypedDict):
    pack_id: str
    title: str
    description: str
    ready: bool


USAGE_HINT = (
    "science.search discovers operations and input examples in the user's enabled Scientific "
    "Workbench packs; science.run executes one in a bounded worker, creates a verified "
    "Workbench node beside this agent, and returns its values, summary and receipt."
)

SEARCH_SPEC = ToolSpec(
    name="science.search",
    description=(
        "List enabled Scientific Workbench packs, or search their operations. Without a "
        "query or pack_id, returns the enabled pack inventory. With either, returns "
        "paginated operation descriptions, fields and example inputs for science.run. "
        "Only enabled, activated packs are searched; never use disabled packs."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pack_id": {"type": "string", "description": "Optional pack ID, e.g. mathematics."},
            "query": {"type": "string", "description": "Optional operation title, ID or topic."},
            "offset": {"type": "integer", "description": "Zero-based result offset (default 0)."},
            "limit": {"type": "integer", "description": "Results per page, 1–25 (default 10)."},
        },
    },
)

RUN_SPEC = ToolSpec(
    name="science.run",
    description=(
        "Execute a discovered operation from an enabled Scientific Workbench pack using "
        "validated input values (see science.search fields/examples). Uses the Workbench's "
        "isolated, timed scientific worker and verified receipt. On success creates an "
        "undoable, linked Workbench node immediately right of the running workspace agent; "
        "returns the node ID, computed values, summary and full provenance receipt. "
        "Invalid inputs or failed calculations do not create nodes. Does not implicitly "
        "run connected nodes or accept source bindings."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pack_id": {"type": "string", "description": "Enabled pack ID returned by science.search."},
            "operation_id": {"type": "string", "description": "Operation ID returned by science.search."},
            "inputs": {"type": "object", "description": "Operation fields and values; see the search example."},
            "uncertainty_method": {
                "type": "string", "enum": ["auto", "gum", "monte_carlo"],
                "description": "Optional Workbench propagation method (default auto).",
            },
        },
        "required": ["pack_id", "operation_id", "inputs"],
    },
)


def _error(message: str) -> ToolResult:
    return ToolResult(content=json.dumps({"ok": False, "error": message}), is_error=True)


def _grants(ctx: RunContext) -> dict:
    """Read current user grants, not a cached registry-time snapshot."""
    manager = getattr(ctx, "settings_manager", None)
    if manager is None:
        return {}
    try:
        grants = manager.get_plugin_grants()
    except (AttributeError, OSError, ValueError):
        return {}
    return grants if isinstance(grants, dict) else {}


def _enabled(ctx: RunContext, pack_id: str) -> bool:
    return pack_id in PACKS and _grants(ctx).get(f"science_{pack_id}") is True


def _ready(document, pack_id: str) -> bool:
    # A grant may precede plugin activation. Without the plugin's registered
    # serializer this would be an opaque node, not a usable Workbench.
    return callable(document.plugin_node_serializers.get(f"science_{pack_id}.workbench"))


def _agent(document, ctx: RunContext):
    agent_id = getattr(ctx, "harness_node_id", None)
    run_id = getattr(ctx, "run_id", None)
    if not isinstance(agent_id, str) or not isinstance(run_id, str) or not run_id:
        return None
    node = document.nodes.get(agent_id)
    if (node is None or node.kind != "harness"
            or getattr(node.state, "harness_run_id", None) != run_id):
        return None
    return node


def _identity(document) -> tuple:
    return document.current_chat_id, document.current_workspace_id


async def _run_worker(pack_id, op, inputs, input_units, uncertainty_method, ctx):
    """Bridge the harness's threading.Event to the Workbench asyncio.Event."""
    stop = ctx.cancel.event if ctx.cancel is not None else None
    _raise_if_cancelled(stop)
    worker_cancel = asyncio.Event()

    async def observe_stop():
        while not stop.is_set():
            await asyncio.sleep(0.05)
        worker_cancel.set()

    observer = asyncio.create_task(observe_stop()) if stop is not None else None
    try:
        return await execute_in_process(
            pack_id, op, inputs, {}, timeout_s=execution_timeout(op),
            cancel_event=worker_cancel, input_units=input_units,
            uncertainty_method=uncertainty_method,
        )
    finally:
        if observer is not None:
            observer.cancel()


def register_workspace_tools(registry: ToolRegistry, dispatcher, document) -> None:
    async def search(call: ToolCall, ctx: RunContext) -> ToolResult:
        args = call.arguments
        pack_id, query = args.get("pack_id"), args.get("query")
        offset, limit = args.get("offset", 0), args.get("limit", 10)
        if pack_id is not None and (not isinstance(pack_id, str) or not pack_id):
            return _error("pack_id must be a nonempty pack ID.")
        if query is not None and (not isinstance(query, str) or len(query) > 200):
            return _error("query must be a string of at most 200 characters.")
        if type(offset) is not int or not 0 <= offset <= 10000:
            return _error("offset must be an integer between 0 and 10000.")
        if type(limit) is not int or not 1 <= limit <= 25:
            return _error("limit must be an integer between 1 and 25.")

        packs: list[EnabledPack] = [
            {"pack_id": key, "title": title, "description": description, "ready": _ready(document, key)}
            for key, (title, description) in sorted(PACKS.items())
            if _enabled(ctx, key)
        ]
        if pack_id is not None and pack_id not in {pack["pack_id"] for pack in packs}:
            return _error("That scientific pack is unavailable or not enabled in Settings > Plugins.")
        if pack_id is None and not (query or "").strip():
            return ToolResult(content=json.dumps({
                "packs": packs, "operations": [],
                "hint": "Supply pack_id or query to discover operations and their input examples.",
            }))

        selected = [pack for pack in packs if pack_id is None or pack["pack_id"] == pack_id]
        if any(not pack["ready"] for pack in selected):
            if pack_id is not None:
                return _error("This enabled scientific pack is not activated yet.")
            selected = [pack for pack in selected if pack["ready"]]
        needle = (query or "").strip().casefold()
        matches = []
        for pack in selected:
            # This is intentionally AFTER the live grant and activation gates.
            for op in operations(pack["pack_id"]):
                searchable = " ".join((pack["title"], op.id, op.title, op.description)).casefold()
                if needle and needle not in searchable:
                    continue
                matches.append((pack, op))
        page = matches[offset:offset + limit]
        return ToolResult(content=json.dumps({
            "packs": packs,
            "operations": [
                {"pack_id": pack["pack_id"], "pack_title": pack["title"],
                 **{k: v for k, v in op.describe().items()
                    if k in ("id", "title", "description", "fields", "example", "headline")}}
                for pack, op in page
            ],
            "total": len(matches),
            "next_offset": offset + len(page) if offset + len(page) < len(matches) else None,
        }, allow_nan=False))

    async def run(call: ToolCall, ctx: RunContext) -> ToolResult:
        args = call.arguments
        if set(args) - {"pack_id", "operation_id", "inputs", "uncertainty_method"}:
            return _error("Unsupported arguments. Use pack_id, operation_id, inputs and optional uncertainty_method.")
        pack_id, op_id, raw = args.get("pack_id"), args.get("operation_id"), args.get("inputs")
        if not isinstance(pack_id, str) or not _enabled(ctx, pack_id):
            return _error("That scientific pack is unavailable or not enabled in Settings > Plugins.")
        if not _ready(document, pack_id):
            return _error("This enabled scientific pack is not activated yet.")
        if not isinstance(op_id, str) or type(raw) is not dict:
            return _error("operation_id must be a string and inputs must be an object.")
        method = args.get("uncertainty_method", "auto")
        if method not in ("auto", "gum", "monte_carlo"):
            return _error("uncertainty_method must be auto, gum or monte_carlo.")
        agent = _agent(document, ctx)
        if agent is None:
            return _error("A currently running workspace agent is required.")
        identity = _identity(document)
        stop = ctx.cancel.event if ctx.cancel is not None else None
        _raise_if_cancelled(stop)

        # Validate on the parent before launching a calculation. No node is
        # inserted until a complete, receipt-verified result is available.
        op = next((item for item in operations(pack_id) if item.id == op_id), None)
        if op is None:
            return _error("Choose an operation registered for this enabled pack.")
        try:
            inputs, input_units = validated_inputs_with_units(op, raw)
            result = await _run_worker(pack_id, op, inputs, input_units, method, ctx)
        except ValueError as exc:
            return _error(str(exc))
        except ExecutionFailure as exc:
            _raise_if_cancelled(stop)
            return ToolResult(content=json.dumps(exc.as_result()), is_error=True)

        _raise_if_cancelled(stop)
        if (_agent(document, ctx) is not agent or _identity(document) != identity
                or not _enabled(ctx, pack_id) or not _ready(document, pack_id)):
            return _error("The agent, workspace or pack grant changed during calculation; result discarded.")
        receipt = result.get("provenance") if isinstance(result, dict) else None
        if (not isinstance(receipt, dict)
                or result.get("operationId") != op.id
                or result.get("inputs") != inputs
                or receipt.get("sources") != {}
                or not readable_result({"version": 1, "latest": result}, pack_id)):
            return _error("The scientific worker returned an invalid or unverified receipt.")

        state_data = {"version": 1, "nodeKey": uuid4().hex, "latest": result}
        # Match the manual Workbench: inline result tables stay on the
        # immutable plugin snapshot; only externalized typed table assets
        # require TableState's metadata/storage lifecycle.
        state_type = TableState if "tableAsset" in result else PluginStateSnapshot
        state = state_type.from_data(state_data)
        kind = f"science_{pack_id}.workbench"
        x, y = document.place_child(agent.id, kind, prefer="right")

        def create():
            return document.add_plugin_node(
                kind, x, y, agent.id, title=PACKS[pack_id][0],
                content=result["summary"], state=state,
            )

        try:
            node, _ = document.record_command(
                "harnessScientificRun", "agent", create,
                node_ids=[agent.id], run_id=getattr(ctx, "run_id", None),
            )
        except (SceneError, ValueError) as exc:
            return _error(f"Could not create the scientific Workbench node: {exc}")
        return ToolResult(content=json.dumps({
            "ok": True, "verified": True, "node_id": node.id,
            "pack_id": pack_id, "operation_id": op.id,
            "values": result["values"], "summary": result["summary"],
            "receipt": result["provenance"],
        }, allow_nan=False))

    # FS_READ is a harness-only capability ceiling. The shared Builder registry
    # uses the same registration object, but must not offer these workspace-agent
    # tools to a Builder context that cannot supply the live harness node.
    registry.register(SEARCH_SPEC, search, scopes={FS_READ, GRAPH_READ}, approval="auto")
    registry.register(RUN_SPEC, run, scopes={FS_READ, CODE_EXECUTE, GRAPH_MUTATE}, approval="once")
