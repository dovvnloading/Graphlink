"""Bridge catalog operations to explicitly run, undoable Graphlink nodes."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import ClassVar
from uuid import uuid4
from weakref import WeakKeyDictionary

from backend.domain.node_states import ChartState, PersistedPluginState, PluginStateSnapshot
from backend.plugin_sdk import HostContext, PluginNodeSeed, PluginSceneUpdate, SciencePickerOperation

from .attempts import AttemptLedger
from .bindings import column_binding, paired_rows
from .catalog import PACKS, PACK_FAMILIES, operations
from .core import UNCERTAINTY_METHODS, INPUT_LIMITS, _effective_unit, _unit_field, canonical_bytes, fingerprint, finite, readable_result, validated_inputs_with_units
from .data_table import (
    TableState, decode_input, numeric_column, parse_table, persist_table,
    read_table, stage_from_saved, table_metadata_valid, table_csv_chunk,
)
from .execution import ExecutionFailure, execute_in_process, execution_timeout
from .methods import render_result_methods
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
    graph_run_id: str | None = None
    uncertainty_method: str = "auto"


@dataclass(kw_only=True)
class StartAttemptArgs(RunArgs):
    attempt_id: str


@dataclass
class AttemptArgs:
    node_id: str
    attempt_id: str


@dataclass
class ConfigureRunnerArgs:
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
class ExportTableCsvArgs:
    WIRE_OMITS_DEFAULTS: ClassVar[bool] = True

    node_id: str
    table_source: str = "result"
    offset: int = 0
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
    attempt_id: str | None = None


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
    table_source: str = "import"
    operation_id: str | None = None
    field_key: str | None = None
    unit_inputs: dict | None = None
    complete_columns: list[int] | None = None
    assumed_unit: str | None = None


def _run_token(data: dict) -> str:
    result = data.get("latest")
    receipt = result["provenance"] if result else {}
    # Preserve all legacy run tokens for nodes without an imported table.
    original = [data.get("nodeKey"), receipt.get("receiptHash") or fingerprint(receipt)]
    table = data.get("table")
    token = (fingerprint([*original, table["provenance"]["receiptHash"]])
             if isinstance(table, dict) and table_metadata_valid(table) else fingerprint(original))
    # A saved runner configuration is independently undoable. Include it in
    # the optimistic token so an older editor cannot overwrite a newer save
    # just because the last *calculation* has not changed.
    runner = data.get("runner")
    return fingerprint([token, runner]) if isinstance(runner, dict) else token


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
    if not isinstance(sources, dict):
        raise ValueError("Linked sources must be an object keyed by input field.")
    if operation and any(field.kind == "table" and field.key not in sources for field in operation.fields):
        raise ValueError("Connect a typed table to every table input before running.")
    if not sources:
        return {}
    if set(sources) - set(inputs):
        raise ValueError("A linked field does not belong to this operation.")
    incoming = {edge.source for edge in document.edges.values() if edge.target == node.id}
    verified = {}
    tables = {}
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
        if selector and selector[0] in ("result_table", "result_table_column", "typed_table", "table_column"):
            generated = selector[0] in ("result_table", "result_table_column")
            latest = source.state.data.get("latest") if generated else None
            table = latest.get("tableAsset") if isinstance(latest, dict) else source.state.data.get("table")
            if (generated and not isinstance(latest, dict)) or not table_metadata_valid(table):
                label = "result table" if generated else "typed table"
                raise ValueError(f"The connected source has no readable {label}.")
            receipt = latest["provenance"] if generated else table["provenance"]
            digest = receipt["resultHash"] if generated else table["asset_ref"]
            if citation.get("resultHash") != digest or citation.get("receiptHash") != receipt["receiptHash"]:
                raise ValueError("The connected table changed. Select its current table or column.")
            ref = {
                "nodeId": source_id, "sourceKey": source.state.data.get("nodeKey"),
                "resultHash": digest, "receiptHash": receipt["receiptHash"], "selector": selector,
                "packId": plugin_id.removeprefix("science_"),
                "operationId": latest["operationId"] if generated else "data_table",
            }
            if selector in (["typed_table"], ["result_table"]):
                if fields.get(key) is None or fields[key].kind != "table" or inputs[key] != table:
                    raise ValueError("The supplied table differs from the connected table.")
            elif len(selector) == 5 and selector[0] in ("result_table_column", "table_column"):
                table_key = (table["asset_ref"], table["provenance"]["receiptHash"])
                if table_key not in tables:
                    tables[table_key] = read_table(table)
                _, _, normalized, evidence = column_binding(
                    table, selector, fields.get(key), inputs, citation.get("binding"), table=tables[table_key],
                )
                if normalized != inputs[key]:
                    raise ValueError(f"{key}: the supplied measurements differ from the selected column. "
                                     "Review its units and row selection.")
                if citation.get("binding") is not None and citation["binding"] != evidence:
                    raise ValueError(f"{key}: the connected column or its units changed. Review its binding.")
                if citation.get("binding") is None and evidence["sourceUnit"] != evidence["targetUnit"] and evidence["sourceUnit"]:
                    raise ValueError(f"{key}: review the source and target units before reusing this legacy binding.")
                ref["binding"] = evidence
                if input_units is not None:
                    input_units[key] = [evidence["sourceUnit"] or evidence["targetUnit"]] * len(normalized)
            else:
                raise ValueError("Invalid typed table selector.")
            verified[key] = ref
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
            declared = result_unit(selector[1], latest["provenance"].get("outputUnits"))
            if measurement and declared and measurement.get("unit") and measurement["unit"] != declared:
                raise ValueError("The saved uncertainty unit conflicts with the declared output unit. Rerun the source.")
            source_unit = declared or (measurement["unit"] if measurement else "")
        field = fields.get(key)
        target_unit = _effective_unit(field, inputs) if field and field.kind in {"number", "integer"} else ""
        assumed_unit = None
        if target_unit and target_unit != "1" and not source_unit and type(selected) in (int, float):
            setter = _unit_field(field, inputs)
            if setter is None or not isinstance(inputs.get(setter), str):
                raise ValueError(f"{key}: the connected result has no declared unit. Supply a measured literal with units.")
            # The configured sibling (e.g. from_unit) is the user's explicit
            # interpretation of the otherwise unitless published number.
            source_unit = assumed_unit = target_unit
        normalized_source = selected
        if target_unit and source_unit and type(selected) in (int, float):
            try:
                normalized_source = convert_decimal_measurement(str(selected), source_unit, target_unit)
            except ValueError as exc:
                raise ValueError(f"Connected input '{key}' has incompatible units: {exc}") from None
        if (type(normalized_source) in (int, float) and type(inputs[key]) in (int, float)
                and target_unit and source_unit):
            # Accept only binary64 roundoff from decimal unit normalization,
            # not a source value changed by many measurement standard deviations.
            # A relative 1e-13 tolerance incorrectly accepted modified values,
            # and a fixed 1e-14 tolerance incorrectly accepted zero for tiny ones.
            source_ulp = math.ulp(float(normalized_source))
            input_ulp = math.ulp(float(inputs[key]))
            matched = abs(normalized_source - inputs[key]) <= 2 * max(source_ulp, input_ulp)
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
        if assumed_unit:
            verified[key]["assumedUnit"] = assumed_unit
        if receipt_hash:
            verified[key]["receiptHash"] = receipt_hash
    if operation:
        paired_rows(operation, verified)
    return verified


def _same_sources(previous: dict, current: dict) -> bool:
    # Scene IDs are allocated again on reopen. The durable key identifies
    # the same source node when comparing otherwise identical receipts.
    def identity(ref):
        return (ref.get("sourceKey") or ref.get("nodeId"), ref.get("receiptHash"), ref.get("resultHash"), ref.get("selector"), ref.get("binding"), ref.get("assumedUnit"))

    return (previous.keys() == current.keys()
            and all(isinstance(previous[k], dict) and identity(previous[k]) == identity(current[k]) for k in current))


def register_pack(host: HostContext, pack_id: str) -> None:
    title, description = PACKS[pack_id]
    catalog = {op.id: op for op in operations(pack_id)}
    schema = [op.describe() for op in catalog.values()]
    kind = f"science_{pack_id}.workbench"
    # Plugin registration can be shared by several documents with identical wire IDs.
    active: dict[tuple[int, str], _ActiveCalculation] = {}
    attempts = AttemptLedger()

    def seed(document, ctx, parent):
        return PluginNodeSeed(title=title, state=PluginStateSnapshot.from_data({"version": 1, "latest": None, "nodeKey": uuid4().hex}))

    # The host checks the current grant before calling this hook. Frozen
    # snapshots can safely reuse validated wire JSON across pans and undo.
    wire_cache: WeakKeyDictionary[PluginStateSnapshot, str] = WeakKeyDictionary()

    def live_wire(node, base):
        attempt = attempts.current(node)
        if attempt is None:
            return {"science": base}
        # Immutable result snapshots cache only the persistent portion. Attempts
        # never enter a saved file, run token, chart receipt or undo command.
        return {"science": base[:-1] + ',"attempt":' + json.dumps(attempt.wire(), separators=(",", ":")) + "}"}

    def serialize(node):
        state = node.state
        if type(state) is PluginStateSnapshot:
            cached = wire_cache.get(state)
            if cached is not None:
                return live_wire(node, cached)
        data = state.data if type(state) in STATE_TYPES else {}
        error = ""
        if type(state) is PersistedPluginState and state.unresolved_asset_ref:
            error = "The saved scientific result asset is unavailable. Restore the asset and reopen this workspace."
        elif not readable_result(data, pack_id):
            error = "The saved scientific result has an unsupported version or a damaged receipt."
        elif "table" in data and not table_metadata_valid(data["table"]):
            error = "The scientific table has an invalid import receipt."
        wire = json.dumps({
            "scienceVersion": 1, "attemptProtocol": 1, "packId": pack_id, "operations": [] if error else schema,
            "nodeKey": data.get("nodeKey") if isinstance(data.get("nodeKey"), str) else "",
            "latest": None if error else data.get("latest"), "error": error,
            "runner": None if error else data.get("runner"),
            "table": None if error else data.get("table"),
            "limits": INPUT_LIMITS, "runToken": "" if error else _run_token(data),
        }, separators=(",", ":"), allow_nan=False)
        if type(state) is PluginStateSnapshot:
            wire_cache[state] = wire
        return live_wire(node, wire)

    def serialize_saved(node):
        # The host calls this under the actual session_save asset-store context.
        # The bytes themselves never enter the live graph or a saved JSON blob.
        return persist_table(node.state.data)

    def deserialize(data):
        recovered = {"nodeKey": uuid4().hex, **data}
        if "table" in recovered or (
                isinstance(recovered.get("latest"), dict)
                and "tableAsset" in recovered["latest"]):
            stage_from_saved(recovered)
            return TableState.from_data(recovered)
        return PluginStateSnapshot.from_data(recovered)

    def configure_runner(document, ctx, request):
        """Explicit, undoable opt-in; never runs a calculation or an agent tool."""
        node, error = _current_state(document, request.node_id, kind, request.expected_run)
        if error:
            return error
        operation = catalog.get(request.operation_id)
        if operation is None:
            return {"ok": False, "error": "Choose an operation registered for this pack."}
        if request.uncertainty_method not in UNCERTAINTY_METHODS:
            return {"ok": False, "error": "Uncertainty method must be auto, gum or monte_carlo."}
        try:
            inputs, _input_units = validated_inputs_with_units(operation, request.inputs)
            sources = _verify_sources(
                document, node, inputs, request.sources if request.sources is not None else {}, ctx.is_plugin_granted,
                operation=operation,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        configuration = {
            "version": 1, "operationId": operation.id, "inputs": dict(request.inputs), "sources": sources,
        }
        if request.uncertainty_method != "auto":
            # Older runners must reject forced methods they cannot reproduce.
            configuration.update(version=2, uncertaintyMethod=request.uncertainty_method)
        prior = node.state.data
        if prior.get("runner") == configuration:
            return {"ok": True}

        def save_config():
            saved = {**prior, "runner": configuration}
            node.state = (TableState.from_data(saved) if "table" in saved
                          else PluginStateSnapshot.from_data(saved))

        document.record_command(
            f"science:{pack_id}:configure_runner", "user", save_config, node_ids=[node.id],
        )
        return PluginSceneUpdate({"ok": True})

    def prepare_run(document, ctx, request, attempt_id=None):
        node, error = _current_state(document, request.node_id, kind, request.expected_run)
        if error:
            return error
        if request.graph_run_id is not None:
            # A caller cannot impersonate an unrelated run for undo grouping.
            authorized = any(
                record.get("runId") == request.graph_run_id and record.get("status") == "running"
                and any(
                    step.get("nodeId") == node.id and step.get("kind") == "scientific"
                    and step.get("operation") == request.operation_id and step.get("status") == "pending"
                    for step in record.get("steps", [])
                )
                for record in document.graph_runs
            )
            if not authorized:
                return {"ok": False, "errorType": "validation",
                        "error": "This scientific operation is not part of that running graph."}
        op = catalog.get(request.operation_id)
        if op is None:
            return {"ok": False, "error": "Choose an operation registered for this pack."}
        slot = (id(document), request.node_id)
        if slot in active:
            return {"ok": False, "errorType": "busy", "error": "A calculation is already running on this scientific node."}
        state = node.state
        try:
            inputs, input_units = validated_inputs_with_units(op, request.inputs)
            uncertainty_inputs: dict = {}
            sources = _verify_sources(document, node, inputs, request.sources if request.sources is not None else {},
                                      ctx.is_plugin_granted, uncertainty_inputs,
                                      operation=op, input_units=input_units)
            if request.uncertainty_method not in UNCERTAINTY_METHODS:
                raise ValueError("Uncertainty method must be auto, gum or monte_carlo.")
            budget = execution_timeout(op)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        # All graph reads and the eventual undoable commit remain in this
        # event loop. Only detached, validated inputs enter the spawn worker.
        ticket = _ActiveCalculation(op.id, asyncio.Event(), time.monotonic(), attempt_id)
        active[slot] = ticket
        return execute(document, ctx, request, node, state, op, inputs, input_units,
                       uncertainty_inputs, sources, budget, slot, ticket)

    async def execute(document, ctx, request, node, state, op, inputs, input_units,
                      uncertainty_inputs, sources, budget, slot, ticket):
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
                                latest_uncertainty, operation=op)
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
            if active.get(slot) is ticket:
                active.pop(slot)

        latest = state.data.get("latest")
        if isinstance(latest, dict):
            previous = latest["provenance"]
            if (previous["resultHash"] == result["provenance"]["resultHash"]
                    and previous.get("requestedUncertaintyMethod") == result["provenance"].get("requestedUncertaintyMethod")
                    and previous.get("inputUnits") == result["provenance"].get("inputUnits")
                    and previous.get("referenceData") == result["provenance"].get("referenceData")
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
            if "runner" in state.data:
                saved["runner"] = state.data["runner"]
            if "table" in state.data:
                saved["table"] = state.data["table"]
            node.state = (TableState.from_data(saved)
                          if "table" in saved or "tableAsset" in result
                          else PluginStateSnapshot.from_data(saved))
            node.content = result["summary"]

        document.record_command(f"science:{pack_id}:{op.id}", "user", save_result,
                                node_ids=[node.id, *chart_children], run_id=request.graph_run_id)
        return PluginSceneUpdate({"ok": True})

    async def run(document, ctx, request):
        calculation = prepare_run(document, ctx, request)
        return calculation if isinstance(calculation, dict) else await calculation

    def start_attempt(document, ctx, request):
        node = document.nodes.get(request.node_id)
        if node is None or node.kind != kind or request.graph_run_id is not None:
            return {"ok": False, "error": "Select a workbench for this manual calculation."}
        if request.operation_id not in catalog:
            return {"ok": False, "error": "Choose an operation registered for this pack."}
        if ctx.run_background_scene_job is None:
            return {"ok": False, "error": "This host does not support background calculations."}
        request_hash = fingerprint([request.operation_id, request.inputs, request.sources,
                                    request.expected_run, request.uncertainty_method])
        # A retry after a lost reply can arrive after a successful commit
        # changed expected_run. Resolve its identity before checking that token.
        existing = attempts.get(node, request.attempt_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                return {"ok": False, "errorType": "conflict", "error": "This attempt ID belongs to different inputs."}
            return {"ok": True, "attempt": existing.wire()}
        slot = (id(document), request.node_id)
        if slot in active:
            return {"ok": False, "errorType": "busy", "error": "A calculation is already running on this workbench."}
        try:
            attempt = attempts.reserve(node, request.attempt_id, request.operation_id, request_hash)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            calculation = prepare_run(document, ctx, request, request.attempt_id)
        except Exception:
            logging.getLogger(__name__).exception("Scientific attempt preparation failed")
            calculation = {"errorType": "worker_error", "error": "The scientific calculation could not start."}
        if isinstance(calculation, dict):
            attempt.finish(calculation)
            return PluginSceneUpdate({"ok": True, "attempt": attempt.wire()})

        async def settle():
            try:
                outcome = await calculation
                result = outcome.result if isinstance(outcome, PluginSceneUpdate) else outcome
                latest = node.state.data.get("latest") if result.get("ok") else None
                receipt = latest["provenance"] if latest else {}
                attempt.finish(result, receipt.get("receiptHash") or receipt.get("resultHash", ""))
            except asyncio.CancelledError:
                attempt.finish({"errorType": "cancelled", "error": "The calculation was interrupted."})
                raise
            except Exception:
                logging.getLogger(__name__).exception("Scientific calculation failed before acknowledgement")
                attempt.finish({"errorType": "worker_error", "error": "The scientific calculation could not finish."})

        ctx.run_background_scene_job(settle())
        return PluginSceneUpdate({"ok": True, "attempt": attempt.wire()})

    def status_attempt(document, ctx, request):
        current = attempts.get(document.nodes.get(request.node_id), request.attempt_id)
        return {"ok": True, "attempt": current.wire() if current is not None else None}

    def cancel_attempt(document, ctx, request):
        current = attempts.get(document.nodes.get(request.node_id), request.attempt_id)
        if current is None:
            return {"ok": False, "errorType": "not_running", "error": "This calculation attempt is unavailable."}
        ticket = active.get((id(document), request.node_id))
        if ticket is not None and ticket.attempt_id == request.attempt_id and current.status == "running":
            current.status = "cancelling"
            ticket.cancel_event.set()
            return PluginSceneUpdate({"ok": True, "attempt": current.wire()})
        return {"ok": True, "attempt": current.wire()}

    def cancel(document, ctx, request):
        # Key by the live run rather than the mutable node snapshot: Cancel
        # must still work if an editor changed the node during calculation.
        ticket = active.get((id(document), request.node_id))
        if ticket is None or ticket.attempt_id is not None or ticket.operation_id != request.operation_id:
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

    def export_methods(document, ctx, request):
        node, error = _current_state(document, request.node_id, kind, request.expected_run)
        if error:
            return error
        try:
            markdown = render_result_methods(
                pack_id, node.state.data, node_id=node.id, title=node.title,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "markdown": markdown}

    async def export_table_csv(document, ctx, request):
        node, error = _current_state(document, request.node_id, kind, request.expected_run)
        if error:
            return error
        if request.table_source not in ("result", "import"):
            return {"ok": False, "error": "Choose a result or imported typed table."}
        state = node.state
        latest = state.data.get("latest")
        meta = (latest.get("tableAsset") if isinstance(latest, dict) else None
                ) if request.table_source == "result" else state.data.get("table")
        if not table_metadata_valid(meta):
            return {"ok": False, "error": "No complete typed table is available to export."}
        try:
            page = await asyncio.to_thread(table_csv_chunk, meta, request.offset)
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        if document.nodes.get(node.id) is not node or node.state is not state:
            return {"ok": False, "conflict": True, "error": "The table changed during export."}
        if ctx.is_plugin_granted is None or not ctx.is_plugin_granted(ctx.plugin_id):
            return {"ok": False, "error": "The scientific pack was disabled during export."}
        return {"ok": True, **page}

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
        if request.table_source not in ("import", "result"):
            return {"ok": False, "error": "Choose an imported or result table."}
        latest = source.state.data.get("latest")
        if request.table_source == "result":
            meta = latest.get("tableAsset") if isinstance(latest, dict) else None
            receipt = latest["provenance"] if isinstance(latest, dict) else {}
            expected_hash = receipt.get("resultHash")
            expected_receipt = receipt.get("receiptHash")
            selector_name = "result_table_column"
        else:
            meta = source.state.data.get("table")
            receipt = meta.get("provenance", {}) if isinstance(meta, dict) else {}
            expected_hash = meta.get("asset_ref") if isinstance(meta, dict) else None
            expected_receipt = receipt.get("receiptHash")
            selector_name = "table_column"
        if not table_metadata_valid(meta):
            return {"ok": False, "error": "The connected source has no readable typed table."}
        if ((request.expected_hash is not None and request.expected_hash != expected_hash)
                or (request.expected_receipt is not None
                    and request.expected_receipt != expected_receipt)):
            return {"ok": False, "conflict": True, "error": "The table changed; select its current column."}
        source_state = source.state
        target_state = target.state
        end = meta["rowCount"] if request.end is None else request.end
        selector = [selector_name, request.column, request.policy, request.start, end]
        evidence = None
        try:
            if request.operation_id is None and request.field_key is None:
                if request.complete_columns is not None or request.assumed_unit is not None:
                    raise ValueError("Choose the target operation and vector field for this binding.")
                values = await asyncio.to_thread(
                    numeric_column, meta, request.column, request.policy, request.start, request.end,
                )
                literals = values
            else:
                operation = catalog.get(request.operation_id)
                field = next((f for f in operation.fields if f.key == request.field_key), None) if operation else None
                if request.complete_columns is not None and not any(
                    request.field_key in group for group in (operation.paired_fields if operation else ())
                ):
                    raise ValueError("Select complete rows only for paired fields.")
                values, literals, _, evidence = await asyncio.to_thread(
                    column_binding, meta, selector, field, request.unit_inputs or {}, {
                        "completeColumns": request.complete_columns, "assumedUnit": request.assumed_unit,
                    },
                )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if document.nodes.get(source_id) is not source or source.state is not source_state:
            return {"ok": False, "conflict": True, "error": "The source table changed during selection."}
        if (document.nodes.get(target.id) is not target or target.state is not target_state
                or not any(edge.source == source_id and edge.target == target.id
                           for edge in document.edges.values())):
            return {"ok": False, "conflict": True,
                    "error": "The target node or its connection changed during selection."}
        if (ctx.is_plugin_granted is None or not ctx.is_plugin_granted(ctx.plugin_id)
                or not ctx.is_plugin_granted(source.kind.split(".", 1)[0])):
            return {"ok": False, "error": "The scientific pack was disabled during selection."}
        return {
            "ok": True, "values": values, **({"inputs": literals} if evidence else {}),
            "reference": {
                "nodeId": source_id, "sourceKey": source_state.data.get("nodeKey"),
                "resultHash": expected_hash, "receiptHash": expected_receipt,
                "selector": selector, **({"binding": evidence} if evidence else {}),
            },
        }

    host.register_node_kind(
        "workbench", seed, requires_parent=False, serialize=serialize,
        serialize_saved=serialize_saved, deserialize=deserialize, externalize_state=True,
    )
    host.register_picker_entry(
        node_kind="workbench", name=title, description=description,
        category="Biology Workbench" if PACK_FAMILIES.get(pack_id) == "biology" else "Scientific Workbench",
        science_operations=tuple(
            SciencePickerOperation(op.id, op.title, op.description, tuple(field.kind for field in op.fields))
            for op in catalog.values()
        ),
    )
    host.register_intent("configure_runner", configure_runner, args_schema=ConfigureRunnerArgs, expose_to_builder=False)
    host.register_intent("run", run, args_schema=RunArgs, expose_to_builder=False)
    host.register_intent("start_attempt", start_attempt, args_schema=StartAttemptArgs, expose_to_builder=False)
    host.register_intent("status_attempt", status_attempt, args_schema=AttemptArgs, expose_to_builder=False)
    host.register_intent("cancel_attempt", cancel_attempt, args_schema=AttemptArgs, expose_to_builder=False)
    host.register_intent("cancel", cancel, args_schema=CancelArgs, expose_to_builder=False)
    host.register_intent("export_result", export_result, args_schema=ExportArgs, expose_to_builder=False)
    host.register_intent("export_methods", export_methods, args_schema=ExportArgs, expose_to_builder=False)
    host.register_intent("export_table_csv", export_table_csv, args_schema=ExportTableCsvArgs, expose_to_builder=False)
    host.register_intent("import_table", import_table, args_schema=ImportTableArgs, expose_to_builder=False)
    host.register_intent("table_column", table_column, args_schema=TableColumnArgs, expose_to_builder=False)
