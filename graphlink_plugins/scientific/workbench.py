"""Bridge catalog operations to explicitly run, undoable Graphlink nodes."""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import ClassVar
from uuid import uuid4
from weakref import WeakKeyDictionary

from backend.domain.node_states import ChartState, PersistedPluginState, PluginStateSnapshot
from backend.plugin_sdk import HostContext, PluginNodeSeed, PluginSceneUpdate

from .catalog import PACKS, operations
from .core import INPUT_LIMITS, _effective_unit, canonical_bytes, fingerprint, finite, readable_result, validated_inputs_with_units
from .data_table import (
    TableState, decode_input, numeric_column, parse_table, persist_table,
    stage_from_saved, table_metadata_valid,
)
from .execution import ExecutionFailure, execute_in_process, execution_timeout
from .units import convert_decimal_measurement, parse_unit

STATE_TYPES = (PersistedPluginState, PluginStateSnapshot, TableState)
SCIENCE_KINDS = frozenset(f"science_{pack}.workbench" for pack in PACKS)


@dataclass
class RunArgs:
    # Most run intents omit the default propagation method.
    WIRE_OMITS_DEFAULTS: ClassVar[bool] = True

    node_id: str
    operation_id: str
    inputs: dict
    sources: dict | None = None
    expected_run: str | None = None
    uncertainty_method: str = "auto"


@dataclass
class ExportArgs:
    node_id: str
    expected_run: str | None = None


@dataclass
class CancelArgs:
    node_id: str
    operation_id: str


@dataclass
class _ActiveCalculation:
    operation_id: str
    cancel_event: asyncio.Event
    started_at: float


@dataclass
class ImportTableArgs:
    # The UI omits defaulted options; accept them as optional on the intent wire.
    WIRE_OMITS_DEFAULTS: ClassVar[bool] = True

    node_id: str
    text: str | None = None
    encoded_file: str | None = None
    format: str = "csv"
    missing_values: list[str] | None = None
    column_types: dict[str, str] | None = None
    filename: str = ""
    expected_run: str | None = None


@dataclass
class TableColumnArgs:
    # Column selection likewise omits the default policy and row-window start.
    WIRE_OMITS_DEFAULTS: ClassVar[bool] = True

    node_id: str
    source_node_id: str
    column: int
    policy: str = "reject"
    start: int = 0
    end: int | None = None
    expected_hash: str | None = None
    expected_receipt: str | None = None


def _run_token(data: dict) -> str:
    result = data.get("latest")
    receipt = result["provenance"] if result else {}
    # Preserve all legacy run tokens for nodes without an imported table.
    original = [data.get("nodeKey"), receipt.get("receiptHash") or fingerprint(receipt)]
    table = data.get("table")
    return fingerprint([*original, table["provenance"]["receiptHash"]]) if isinstance(table, dict) and table_metadata_valid(table) else fingerprint(original)


def _current_state(document, node_id: str, kind: str, expected_run: str | None):
    node = document.nodes.get(node_id)
    if node is None or node.kind != kind:
        return None, {"ok": False, "error": "Select a node belonging to this scientific pack."}
    state = node.state
    pack = kind.removeprefix("science_").split(".")[0]
    if (type(state) not in STATE_TYPES or not readable_result(state.data, pack)
            or ("table" in state.data and not table_metadata_valid(state.data["table"]))):
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


def _verify_sources(document, node, inputs: dict, sources: dict, granted,
                    uncertainty_inputs: dict | None = None, *,
                    operation=None, input_units: dict | None = None) -> dict:
    """Verify cited outputs and normalize linked measurements into input units.

    Only an immutable, receipt-validated source determines a linked value's
    units/uncertainty. Units are converted before propagation; standard
    uncertainty and each signed lineage component use the linear scale only
    (affine temperature offsets never change a standard uncertainty).
    """
    fields = {field.key: field for field in operation.fields} if operation else {}
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
        # Imported tables have their own immutable, content-addressed receipt,
        # independent of the source node's last numerical calculation.
        if (len(selector) == 5 and selector[0] == "table_column"):
            _, column, policy, start, end = selector
            table = source.state.data.get("table")
            if not table_metadata_valid(table):
                raise ValueError("The connected source has no readable typed table.")
            receipt = table["provenance"]
            if (citation.get("resultHash") != table["asset_ref"]
                    or citation.get("receiptHash") != receipt["receiptHash"]):
                raise ValueError("The connected table changed. Select its current column.")
            selected = numeric_column(table, column, policy, start, end)
            if selected != inputs[key]:
                raise ValueError("The supplied measurements differ from the selected table column.")
            verified[key] = {
                "nodeId": source_id, "sourceKey": source.state.data.get("nodeKey"),
                "resultHash": table["asset_ref"], "receiptHash": receipt["receiptHash"],
                "selector": selector, "packId": source.kind.removeprefix("science_").split(".")[0],
                "operationId": "data_table",
            }
            continue
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
        measurement = None
        source_unit = ""
        if (uncertainty_inputs is not None or operation is not None) and (
                len(selector) == 2 and selector[0] == "values" and isinstance(selector[1], str)):
            from .propagation import connected_measurement, result_unit
            identity = source.state.data.get("nodeKey") or receipt_hash or digest
            measurement = connected_measurement(latest, selector, identity)
            source_unit = measurement["unit"] if measurement else result_unit(selector[1])
        field = fields.get(key)
        target_unit = _effective_unit(field, inputs) if field and field.kind == "number" else ""
        normalized_source = selected
        if target_unit and source_unit and type(selected) in (int, float):
            try:
                normalized_source = convert_decimal_measurement(str(selected), source_unit, target_unit)
            except ValueError as exc:
                raise ValueError(f"Connected input '{key}' has incompatible units: {exc}") from None
        if (type(normalized_source) in (int, float) and type(inputs[key]) in (int, float)
                and target_unit and source_unit):
            matched = math.isclose(normalized_source, inputs[key], rel_tol=1e-13, abs_tol=1e-14)
        else:
            matched = normalized_source == inputs[key]
        if not matched:
            raise ValueError("The supplied value differs from the selected source output.")
        if measurement is not None and uncertainty_inputs is not None:
            # Older GUM outputs can have no declared unit. In that case the
            # target operation explicitly supplies the physical interpretation.
            unit = target_unit or source_unit
            if unit and source_unit and source_unit != unit:
                from_unit, to_unit = parse_unit(source_unit), parse_unit(unit)
                if from_unit.dimensions != to_unit.dimensions or from_unit.kind != to_unit.kind:
                    raise ValueError(f"Connected input '{key}' has incompatible units.")
                factor = float(from_unit.scale / to_unit.scale)
                components = {origin: finite(weight * factor, "Converted uncertainty component")
                              for origin, weight in measurement["components"].items()}
                sigma = finite(measurement["standardUncertainty"] * abs(factor), "Converted standard uncertainty")
                interval = measurement.get("interval")
                measurement = {
                    **measurement, "value": inputs[key], "unit": unit,
                    "sourceUnit": source_unit, "sourceValue": selected,
                    "standardUncertainty": sigma, "components": components,
                    **({"interval": [convert_decimal_measurement(str(bound), source_unit, unit)
                                    for bound in interval]} if interval is not None else {}),
                }
            elif unit:
                measurement = {**measurement, "value": inputs[key], "unit": unit,
                               **({"sourceUnit": source_unit} if source_unit else {})}
            uncertainty_inputs[key] = measurement
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
    # Scoped to this host/session, never persisted as a result or receipt.
    active: dict[str, _ActiveCalculation] = {}

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
        elif "table" in data and not table_metadata_valid(data["table"]):
            error = "The scientific table has an invalid import receipt."
        wire = json.dumps({
            "scienceVersion": 1, "packId": pack_id, "operations": [] if error else schema,
            "nodeKey": data.get("nodeKey") if isinstance(data.get("nodeKey"), str) else "",
            "latest": None if error else data.get("latest"), "error": error,
            "table": None if error else data.get("table"),
            "limits": INPUT_LIMITS, "runToken": "" if error else _run_token(data),
        }, separators=(",", ":"), allow_nan=False)
        if type(state) is PluginStateSnapshot:
            wire_cache[state] = wire
        return {"science": wire}

    def serialize_saved(node):
        # The host calls this under the actual session_save asset-store context.
        # The bytes themselves never enter the live graph or a saved JSON blob.
        return persist_table(node.state.data)

    def deserialize(data):
        recovered = {"nodeKey": uuid4().hex, **data}
        if "table" in recovered:
            stage_from_saved(recovered)
            return TableState.from_data(recovered)
        return PluginStateSnapshot.from_data(recovered)

    async def run(document, ctx, request):
        node, error = _current_state(document, request.node_id, kind, request.expected_run)
        if error:
            return error
        op = catalog.get(request.operation_id)
        if op is None:
            return {"ok": False, "error": "Choose an operation registered for this pack."}
        if request.node_id in active:
            return {"ok": False, "errorType": "busy", "error": "A calculation is already running on this scientific node."}
        state = node.state
        try:
            inputs, input_units = validated_inputs_with_units(op, request.inputs)
            uncertainty_inputs: dict = {}
            sources = _verify_sources(document, node, inputs, request.sources or {},
                                      ctx.is_plugin_granted, uncertainty_inputs,
                                      operation=op, input_units=input_units)
            if request.uncertainty_method not in ("auto", "gum", "monte_carlo"):
                raise ValueError("Uncertainty method must be auto, gum or monte_carlo.")
            budget = execution_timeout(op)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        # All graph reads and the eventual undoable commit remain in this
        # event loop. Only detached, validated inputs enter the spawn worker.
        ticket = _ActiveCalculation(op.id, asyncio.Event(), time.monotonic())
        active[request.node_id] = ticket
        try:
            result = await execute_in_process(
                pack_id, op, inputs, sources, timeout_s=budget, cancel_event=ticket.cancel_event,
                input_units=input_units, uncertainty_inputs=uncertainty_inputs,
                uncertainty_method=request.uncertainty_method,
            )
            if ticket.cancel_event.is_set():
                elapsed_ms = int((time.monotonic() - ticket.started_at) * 1000)
                raise ExecutionFailure("cancelled", f"Scientific calculation cancelled after {elapsed_ms} ms.", elapsed_ms)
            if not ctx.is_plugin_granted or not ctx.is_plugin_granted(ctx.plugin_id):
                raise ValueError("The pack was disabled while the calculation was running.")
            if document.nodes.get(node.id) is not node or node.state is not state:
                return {"ok": False, "conflict": True, "error": "The node changed during calculation. Review the current run."}
            if sources:
                latest_uncertainty: dict = {}
                _verify_sources(document, node, inputs, sources, ctx.is_plugin_granted,
                                latest_uncertainty, operation=op, input_units=input_units)
                if latest_uncertainty != uncertainty_inputs:
                    return {"ok": False, "conflict": True,
                            "error": "The connected uncertainty changed while calculating. Run again."}
        except ExecutionFailure as exc:
            return exc.as_result()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            # Cancelled and timed-out requests never write a latest artifact,
            # change the node body, or add an undo command.
            if active.get(request.node_id) is ticket:
                active.pop(request.node_id)

        latest = state.data.get("latest")
        if isinstance(latest, dict):
            previous = latest["provenance"]
            if (previous["resultHash"] == result["provenance"]["resultHash"]
                    and previous.get("inputUnits") == result["provenance"].get("inputUnits")
                    and latest.get("uncertainty") == result.get("uncertainty")
                    and _same_sources(previous.get("sources", {}), sources)):
                return {"ok": True}

        # Chart children pin the full receipt, not just the numerical result
        # hash: identical numbers from a different source/run still invalidate
        # a plot. Include the children in the SAME undoable calculation edit.
        node_key = state.data.get("nodeKey")
        chart_children = {}
        for edge in document.edges.values():
            if edge.source != node.id:
                continue
            child = document.nodes.get(edge.target)
            if (child is None or child.kind != "chart" or not isinstance(child.state, ChartState)
                    or child.state.chart_source_node_id != node.id):
                continue
            receipt = child.state.chart_data.get("scienceReceipt") if isinstance(child.state.chart_data, dict) else None
            if isinstance(receipt, dict) and receipt.get("sourceKey") == node_key:
                chart_children[child.id] = child

        def save_result():
            for child in chart_children.values():
                receipt = child.state.chart_data["scienceReceipt"]
                if (receipt.get("receiptHash") != result["provenance"].get("receiptHash",
                                                                          result["provenance"]["resultHash"])
                        or receipt.get("resultHash") != result["provenance"]["resultHash"]):
                    child.state.chart_error = "Scientific result changed · refresh this chart from its source."
            node_key = state.data.get("nodeKey")
            saved = {"version": 1, "nodeKey": node_key or uuid4().hex, "latest": result}
            if "table" in state.data:
                saved["table"] = state.data["table"]
                node.state = TableState.from_data(saved)
            else:
                node.state = PluginStateSnapshot.from_data(saved)
            node.content = result["summary"]

        document.record_command(f"science:{pack_id}:{op.id}", "user", save_result,
                                node_ids=[node.id, *chart_children])
        return PluginSceneUpdate({"ok": True})

    def cancel(document, ctx, request):
        # Key by the live run rather than the mutable node snapshot: Cancel
        # must still work if an editor changed the node during calculation.
        ticket = active.get(request.node_id)
        if ticket is None or ticket.operation_id != request.operation_id:
            return {"ok": False, "errorType": "not_running", "error": "That scientific calculation is not running."}
        # The suspended run owns the worker. Its wakeable cancellation signal
        # causes execute_in_process to terminate and reap that process.
        ticket.cancel_event.set()
        return {"ok": True}

    def export_result(document, ctx, request):
        node, error = _current_state(document, request.node_id, kind, request.expected_run)
        if error:
            return error
        result = node.state.data["latest"]
        if result is None:
            return {"ok": False, "error": "Run a calculation before exporting its result."}
        return {"ok": True, "json": canonical_bytes(result).decode("ascii")}

    async def import_table(document, ctx, request):
        node, error = _current_state(document, request.node_id, kind, request.expected_run)
        if error:
            return error
        previous_state = node.state
        try:
            raw = decode_input(text=request.text, encoded_file=request.encoded_file)
            table = await asyncio.to_thread(
                parse_table, raw, fmt=request.format, missing=request.missing_values,
                hints=request.column_types, filename=request.filename,
            )
            if ctx.is_plugin_granted is None or not ctx.is_plugin_granted(ctx.plugin_id):
                raise ValueError("The pack was disabled during import.")
            if document.nodes.get(node.id) is not node or node.state is not previous_state:
                return {"ok": False, "conflict": True, "error": "The node changed during import."}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        current = previous_state.data
        if current.get("table") == table:
            return {"ok": True}

        def save_import():
            state_data = {**current, "table": table}
            node.state = TableState.from_data(state_data)
            if state_data.get("latest") is None:
                node.content = f"Imported {table['rowCount']} rows, {table['columnCount']} typed columns."

        document.record_command(f"science:{pack_id}:import_table", "user", save_import, node_ids=[node.id])
        return PluginSceneUpdate({"ok": True})

    async def table_column(document, ctx, request):
        target, error = _current_state(document, request.node_id, kind, None)
        if error:
            return error
        source_id = request.source_node_id
        connected = any(edge.source == source_id and edge.target == target.id for edge in document.edges.values())
        source = document.nodes.get(source_id)
        if not connected or source is None or source.kind not in SCIENCE_KINDS:
            return {"ok": False, "error": "Connect the source workbench before selecting its table."}
        source_pack = source.kind.removeprefix("science_").split(".")[0]
        if (ctx.is_plugin_granted is None or not ctx.is_plugin_granted(source.kind.split(".", 1)[0])
                or type(source.state) not in STATE_TYPES
                or not readable_result(source.state.data, source_pack)):
            return {"ok": False, "error": "The source pack or result is unavailable."}
        meta = source.state.data.get("table")
        if not table_metadata_valid(meta):
            return {"ok": False, "error": "The connected source has no readable typed table."}
        if ((request.expected_hash is not None and request.expected_hash != meta["asset_ref"])
                or (request.expected_receipt is not None
                    and request.expected_receipt != meta["provenance"]["receiptHash"])):
            return {"ok": False, "conflict": True, "error": "The table changed; select its current column."}
        source_state = source.state
        try:
            values = await asyncio.to_thread(
                numeric_column, meta, request.column, request.policy, request.start, request.end,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if document.nodes.get(source_id) is not source or source.state is not source_state:
            return {"ok": False, "conflict": True, "error": "The source table changed during selection."}
        receipt = meta["provenance"]
        return {
            "ok": True, "values": values,
            "reference": {
                "nodeId": source_id, "sourceKey": source_state.data.get("nodeKey"),
                "resultHash": meta["asset_ref"], "receiptHash": receipt["receiptHash"],
                "selector": ["table_column", request.column, request.policy, request.start,
                             meta["rowCount"] if request.end is None else request.end],
            },
        }

    host.register_node_kind(
        "workbench", seed, requires_parent=False, serialize=serialize,
        serialize_saved=serialize_saved, deserialize=deserialize, externalize_state=True,
    )
    host.register_picker_entry(node_kind="workbench", name=title, description=description, category="Scientific Workbench")
    host.register_intent("run", run, args_schema=RunArgs, expose_to_builder=False)
    host.register_intent("cancel", cancel, args_schema=CancelArgs, expose_to_builder=False)
    host.register_intent("export_result", export_result, args_schema=ExportArgs, expose_to_builder=False)
    host.register_intent("import_table", import_table, args_schema=ImportTableArgs, expose_to_builder=False)
    host.register_intent("table_column", table_column, args_schema=TableColumnArgs, expose_to_builder=False)
