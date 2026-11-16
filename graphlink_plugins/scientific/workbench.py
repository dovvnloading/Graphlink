"""Bridge catalog operations to explicitly run, undoable Graphlink nodes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from backend.domain.node_states import PersistedPluginState
from backend.plugin_sdk import HostContext, PluginNodeSeed, PluginSceneUpdate

from .catalog import PACKS, operations
from .core import readable_result, run_operation, validated_inputs

SCIENCE_KINDS = frozenset(f"science_{pack}.workbench" for pack in PACKS)


@dataclass
class RunArgs:
    node_id: str
    operation_id: str
    inputs: dict
    sources: dict | None = None


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


def _verify_sources(document, node, inputs: dict, sources: dict) -> dict:
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
                       and type(document.nodes[parent].state) is PersistedPluginState
                       and document.nodes[parent].state.data.get("nodeKey") == source_key]
            if len(matches) != 1:
                raise ValueError("The selected source is missing or ambiguous. Select a connected result.")
            source_id = matches[0]
        if not isinstance(source_id, str) or source_id not in incoming or source_id == node.id:
            raise ValueError("Connect the source node directly before using its result.")
        source = document.nodes.get(source_id)
        selector = citation.get("selector")
        if (source is None or source.kind not in SCIENCE_KINDS
                or type(source.state) is not PersistedPluginState or not isinstance(selector, list)):
            raise ValueError("The source no longer supplies scientific results.")
        # A revoked pack may still have a passive result in memory for saving.
        # Only a live, grant-checked serializer can authorize reading it here.
        raw = document._plugin_state_wire(source).get("science")
        if not raw:
            raise ValueError("Enable the source pack before using its result.")
        if json.loads(raw).get("error"):
            raise ValueError("The source scientific result is unavailable or damaged.")
        latest = source.state.data.get("latest")
        if not isinstance(latest, dict) or not isinstance(latest.get("provenance"), dict):
            raise ValueError("Run the source operation first.")
        digest = latest["provenance"].get("resultHash")
        if not digest or citation.get("resultHash") != digest:
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
    return verified


def _same_sources(previous: dict, current: dict) -> bool:
    # Scene IDs are allocated again on reopen. The durable key identifies
    # the same source node when comparing otherwise identical receipts.
    def identity(ref):
        return (ref.get("sourceKey") or ref.get("nodeId"), ref.get("resultHash"), ref.get("selector"))

    return (previous.keys() == current.keys()
            and all(isinstance(previous[k], dict) and identity(previous[k]) == identity(current[k]) for k in current))


def register_pack(host: HostContext, pack_id: str) -> None:
    title, description = PACKS[pack_id]
    catalog = {op.id: op for op in operations(pack_id)}
    schema = [op.describe() for op in catalog.values()]
    kind = f"science_{pack_id}.workbench"

    def seed(document, ctx, parent):
        return PluginNodeSeed(title=title, state=PersistedPluginState(data={"version": 1, "latest": None, "nodeKey": uuid4().hex}))

    def serialize(node):
        state = node.state
        data = state.data if type(state) is PersistedPluginState else {}
        error = ""
        if type(state) is PersistedPluginState and state.unresolved_asset_ref:
            error = "The saved scientific result asset is unavailable. Restore the asset and reopen this workspace."
        elif not readable_result(data, pack_id):
            error = "The saved scientific result has an unsupported version or a damaged receipt."
        return {"science": json.dumps({
            "scienceVersion": 1, "packId": pack_id, "operations": [] if error else schema,
            "nodeKey": data.get("nodeKey"), "latest": None if error else data.get("latest"), "error": error,
        }, separators=(",", ":"), allow_nan=False)}

    def deserialize(data):
        return PersistedPluginState(data={"nodeKey": uuid4().hex, **data})

    def run(document, ctx, request):
        node = document.nodes.get(request.node_id)
        if node is None or node.kind != kind:
            return {"ok": False, "error": "Select a node belonging to this scientific pack."}
        op = catalog.get(request.operation_id)
        if op is None:
            return {"ok": False, "error": "Choose an operation registered for this pack."}
        state = node.state
        if type(state) is PersistedPluginState and (state.unresolved_asset_ref or not readable_result(state.data, pack_id)):
            return {"ok": False, "error": "Open a readable, supported result before replacing this node's saved work."}
        try:
            inputs = validated_inputs(op, request.inputs)
            sources = _verify_sources(document, node, inputs, request.sources or {})
            result = run_operation(pack_id, op, inputs, sources)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        latest = state.data.get("latest") if type(state) is PersistedPluginState else None
        if isinstance(latest, dict):
            previous = latest["provenance"]
            if previous["resultHash"] == result["provenance"]["resultHash"] and _same_sources(previous.get("sources", {}), sources):
                return {"ok": True}

        def save_result():
            node_key = state.data.get("nodeKey") if type(state) is PersistedPluginState else None
            node.state = PersistedPluginState(data={"version": 1, "nodeKey": node_key or uuid4().hex, "latest": result})
            node.content = result["summary"]

        document.record_command(f"science:{pack_id}:{op.id}", "user", save_result, node_ids=[node.id])
        return PluginSceneUpdate({"ok": True})

    host.register_node_kind(
        "workbench", seed, requires_parent=False, serialize=serialize, deserialize=deserialize,
        externalize_state=True,
    )
    host.register_picker_entry(node_kind="workbench", name=title, description=description, category="Scientific Workbench")
    host.register_intent("run", run, args_schema=RunArgs, expose_to_builder=False)
