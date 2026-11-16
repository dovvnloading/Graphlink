"""Bridge catalog operations to explicitly run, undoable Graphlink nodes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from uuid import uuid4
from weakref import WeakKeyDictionary

from backend.domain.node_states import PersistedPluginState, PluginStateSnapshot
from backend.plugin_sdk import HostContext, PluginNodeSeed, PluginSceneUpdate

from .catalog import PACKS, operations
from .core import INPUT_LIMITS, calculate_result, canonical_bytes, fingerprint, readable_result, validated_inputs

STATE_TYPES = (PersistedPluginState, PluginStateSnapshot)
SCIENCE_KINDS = frozenset(f"science_{pack}.workbench" for pack in PACKS)


@dataclass
class RunArgs:
    node_id: str
    operation_id: str
    inputs: dict
    sources: dict | None = None
    expected_run: str | None = None


@dataclass
class ExportArgs:
    node_id: str
    expected_run: str | None = None


def _run_token(data: dict) -> str:
    result = data.get("latest")
    receipt = result["provenance"] if result else {}
    return fingerprint([data.get("nodeKey"), receipt.get("receiptHash") or fingerprint(receipt)])


def _current_state(document, node_id: str, kind: str, expected_run: str | None):
    node = document.nodes.get(node_id)
    if node is None or node.kind != kind:
        return None, {"ok": False, "error": "Select a node belonging to this scientific pack."}
    state = node.state
    pack = kind.removeprefix("science_").split(".")[0]
    if type(state) not in STATE_TYPES or not readable_result(state.data, pack):
        return None, {"ok": False, "error": "The scientific result is unavailable or damaged."}
    if expected_run is not None and expected_run != _run_token(state.data):
        return None, {"ok": False, "conflict": True, "error": "The result changed. Review the current run before continuing."}
    return node, None


def _extract(result: dict, selector: list):
    """Resolve a field from the exact artifact visible at selection time."""
    match selector:
        case ["values", str(name)]:
            return result["values"][name]
        case ["plot", ("x" | "y") as axis]:
            return result["plot"][axis]
        case ["column", int(column)] if type(column) is int and 0 <= column < len(result["table"]["columns"]):
            return [row[column] for row in result["table"]["rows"]]
        case ["table"]:
            return result["table"]["rows"]
    raise ValueError("Choose a metric, plot axis, numeric column or matrix from the source.")


def _verify_sources(document, node, inputs: dict, sources: dict, granted) -> dict:
    if not sources:
        return {}
    if set(sources) - set(inputs):
        raise ValueError("A linked field does not belong to this operation.")
    incoming = {edge.source for edge in document.edges.values() if edge.target == node.id}
    verified = {}
    for key, citation in sources.items():
        if not isinstance(citation, dict):
            raise ValueError("Each linked field must identify a source.")
        source_id = citation.get("nodeId")
        source_key = citation.get("sourceKey")
        if source_key is not None:
            if not isinstance(source_key, str) or not source_key:
                raise ValueError("Invalid source identity.")
            matches = [parent for parent in incoming if parent in document.nodes
                       and type(document.nodes[parent].state) in STATE_TYPES
                       and document.nodes[parent].state.data.get("nodeKey") == source_key]
            if len(matches) != 1:
                raise ValueError("The selected source is missing or ambiguous. Select a connected result.")
            source_id = matches[0]
        if not isinstance(source_id, str) or source_id not in incoming or source_id == node.id:
            raise ValueError("Connect the source node directly before using its result.")
        source = document.nodes.get(source_id)
        selector = citation.get("selector")
        if (source is None or source.kind not in SCIENCE_KINDS
                or type(source.state) not in STATE_TYPES or not isinstance(selector, list)):
            raise ValueError("The source no longer supplies scientific results.")
        plugin_id = source.kind.split(".", 1)[0]
        if granted is None or not granted(plugin_id):
            raise ValueError("Enable the source pack before using its result.")
        if not readable_result(source.state.data, plugin_id.removeprefix("science_")):
            raise ValueError("The source scientific result is unavailable or damaged.")
        latest = source.state.data.get("latest")
        if not isinstance(latest, dict) or not isinstance(latest.get("provenance"), dict):
            raise ValueError("Run the source operation first.")
        digest = latest["provenance"].get("resultHash")
        receipt_hash = latest["provenance"].get("receiptHash")
        if (not digest or citation.get("resultHash") != digest
                or ("receiptHash" in citation and citation["receiptHash"] != receipt_hash)):
            raise ValueError("The source result changed. Select its current output and run again.")
        try:
            selected = _extract(latest, selector)
        except (IndexError, KeyError, TypeError):
            raise ValueError("The selected output is unavailable.") from None
        if selected != inputs[key]:
            raise ValueError("The supplied value differs from the selected source output.")
        verified[key] = {"nodeId": source_id, "sourceKey": source.state.data.get("nodeKey"),
                         "resultHash": digest, "selector": selector,
                         "packId": latest["provenance"]["packId"], "operationId": latest["operationId"]}
        if receipt_hash:
            verified[key]["receiptHash"] = receipt_hash
    return verified


def _same_sources(previous: dict, current: dict) -> bool:
    # Scene IDs are allocated again on reopen. The durable key identifies
    # the same source node when comparing otherwise identical receipts.
    def identity(ref):
        return (ref.get("sourceKey") or ref.get("nodeId"), ref.get("receiptHash"), ref.get("resultHash"), ref.get("selector"))

    return (previous.keys() == current.keys()
            and all(isinstance(previous[k], dict) and identity(previous[k]) == identity(current[k]) for k in current))


def register_pack(host: HostContext, pack_id: str) -> None:
    title, description = PACKS[pack_id]
    catalog = {op.id: op for op in operations(pack_id)}
    schema = [op.describe() for op in catalog.values()]
    kind = f"science_{pack_id}.workbench"

    def seed(document, ctx, parent):
        return PluginNodeSeed(title=title, state=PluginStateSnapshot.from_data({"version": 1, "latest": None, "nodeKey": uuid4().hex}))

    # The host checks the current grant before calling this hook. Frozen
    # snapshots can safely reuse validated wire JSON across pans and undo.
    wire_cache: WeakKeyDictionary[PluginStateSnapshot, str] = WeakKeyDictionary()

    def serialize(node):
        state = node.state
        if type(state) is PluginStateSnapshot:
            cached = wire_cache.get(state)
            if cached is not None:
                return {"science": cached}
        data = state.data if type(state) in STATE_TYPES else {}
        error = ""
        if type(state) is PersistedPluginState and state.unresolved_asset_ref:
            error = "The saved scientific result asset is unavailable. Restore the asset and reopen this workspace."
        elif not readable_result(data, pack_id):
            error = "The saved scientific result has an unsupported version or a damaged receipt."
        wire = json.dumps({
            "scienceVersion": 1, "packId": pack_id, "operations": [] if error else schema,
            "nodeKey": data.get("nodeKey") if isinstance(data.get("nodeKey"), str) else "",
            "latest": None if error else data.get("latest"), "error": error,
            "limits": INPUT_LIMITS, "runToken": "" if error else _run_token(data),
        }, separators=(",", ":"), allow_nan=False)
        if type(state) is PluginStateSnapshot:
            wire_cache[state] = wire
        return {"science": wire}

    def deserialize(data):
        return PluginStateSnapshot.from_data({"nodeKey": uuid4().hex, **data})

    async def run(document, ctx, request):
        node, error = _current_state(document, request.node_id, kind, request.expected_run)
        if error:
            return error
        op = catalog.get(request.operation_id)
        if op is None:
            return {"ok": False, "error": "Choose an operation registered for this pack."}
        state = node.state
        try:
            inputs = validated_inputs(op, request.inputs)
            sources = _verify_sources(document, node, inputs, request.sources or {}, ctx.is_plugin_granted)
            # Compute on a detached input snapshot. All graph reads and the
            # compare-and-commit below stay on the session event loop.
            result = await asyncio.to_thread(calculate_result, pack_id, op, inputs, sources)
            if not ctx.is_plugin_granted or not ctx.is_plugin_granted(ctx.plugin_id):
                raise ValueError("The pack was disabled while the calculation was running.")
            if document.nodes.get(node.id) is not node or node.state is not state:
                return {"ok": False, "conflict": True, "error": "The node changed during calculation. Review its current run."}
            if sources:
                _verify_sources(document, node, inputs, sources, ctx.is_plugin_granted)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        latest = state.data.get("latest")
        if isinstance(latest, dict):
            previous = latest["provenance"]
            if previous["resultHash"] == result["provenance"]["resultHash"] and _same_sources(previous.get("sources", {}), sources):
                return {"ok": True}

        def save_result():
            node_key = state.data.get("nodeKey")
            node.state = PluginStateSnapshot.from_data({"version": 1, "nodeKey": node_key or uuid4().hex, "latest": result})
            node.content = result["summary"]

        document.record_command(f"science:{pack_id}:{op.id}", "user", save_result, node_ids=[node.id])
        return PluginSceneUpdate({"ok": True})

    def export_result(document, ctx, request):
        node, error = _current_state(document, request.node_id, kind, request.expected_run)
        if error:
            return error
        result = node.state.data["latest"]
        if result is None:
            return {"ok": False, "error": "Run a calculation before exporting its result."}
        return {"ok": True, "json": canonical_bytes(result).decode("ascii")}

    host.register_node_kind(
        "workbench", seed, requires_parent=False, serialize=serialize, deserialize=deserialize,
        externalize_state=True,
    )
    host.register_picker_entry(node_kind="workbench", name=title, description=description, category="Scientific Workbench")
    host.register_intent("run", run, args_schema=RunArgs, expose_to_builder=False)
    host.register_intent("export_result", export_result, args_schema=ExportArgs, expose_to_builder=False)
