"""Pure, explicit Scientific Workbench bindings for the Graph Runner.

Only a saved, user-configured operation participates in a graph run. The
adapter resolves *live* connected scientific receipts into typed operands;
it never evaluates an operation or makes a model/tool call.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.domain.model import SceneError
from graphlink_plugins.scientific.catalog import PACKS, operations
from graphlink_plugins.scientific.core import VERSION, Operation, fingerprint, readable_result, validated_inputs_with_units
from graphlink_plugins.scientific.data_table import numeric_column, table_metadata_valid
from graphlink_plugins.scientific.propagation import connected_measurement, result_unit
from graphlink_plugins.scientific.reference_data import references_for_operation
from graphlink_plugins.scientific.workbench import (
    SCIENCE_KINDS, STATE_TYPES, _extract, _same_sources, _verify_sources,
)


def configured(node: Any) -> bool:
    return (node.kind in SCIENCE_KINDS and type(node.state) in STATE_TYPES
            and isinstance(node.state.data.get("runner"), dict))


def _configuration(node: Any) -> tuple[str, Operation, dict[str, Any]]:
    if not configured(node):
        raise SceneError("This scientific node has not been configured for Graph Runner.")
    pack = node.kind.removeprefix("science_").split(".", 1)[0]
    state = node.state.data
    config = state["runner"]
    if (pack not in PACKS or config.get("version") != 1
            or not isinstance(config.get("operationId"), str)
            or not isinstance(config.get("inputs"), dict)
            or not isinstance(config.get("sources"), dict)
            or len(config["sources"]) > 32):
        raise SceneError("The saved scientific Graph Runner configuration is invalid.")
    operation = next((op for op in operations(pack) if op.id == config["operationId"]), None)
    if operation is None:
        raise SceneError("The configured scientific operation is no longer available.")
    try:
        validated_inputs_with_units(operation, config["inputs"])
    except ValueError as exc:
        raise SceneError(str(exc)) from exc
    if set(config["sources"]) - set(config["inputs"]):
        raise SceneError("The saved scientific field bindings are invalid.")
    if not readable_result(state, pack):
        raise SceneError("The saved scientific result is damaged or unavailable.")
    return pack, operation, config


def operation_id(node: Any) -> str:
    """Use the validated catalog operation ID when pinning a Graph Runner step."""
    _, operation, _ = _configuration(node)
    return operation.id


def _connected_source(document: Any, node: Any, reference: dict) -> Any:
    if not isinstance(reference, dict):
        raise SceneError("A scientific input reference is invalid.")
    key = reference.get("sourceKey")
    selector = reference.get("selector")
    if not isinstance(key, str) or len(key) != 32 or not isinstance(selector, list):
        raise SceneError("Scientific connections require a saved source identity and selector.")
    candidates = [
        document.nodes[edge.source] for edge in document.edges.values()
        if edge.target == node.id and edge.source in document.nodes
        and document.nodes[edge.source].kind in SCIENCE_KINDS
        and type(document.nodes[edge.source].state) in STATE_TYPES
        and document.nodes[edge.source].state.data.get("nodeKey") == key
    ]
    if len(candidates) != 1 or candidates[0] is node:
        raise SceneError("The scientific source was disconnected or changed.")
    return candidates[0]


def dependencies(document: Any, node: Any) -> tuple[str, ...]:
    _, _, config = _configuration(node)
    return tuple(dict.fromkeys(
        _connected_source(document, node, reference).id
        for reference in config["sources"].values()
    ))


def _verify_lineage(
    document: Any, node: Any, granted: Callable[[str], bool],
    visiting: set[str], verified: set[str],
) -> None:
    """A saved receipt is not fresh merely because its own checksum is valid.

    Unconfigured Workbench nodes are legitimate inputs, not runnable steps.
    Verify their saved source chain against today's edges/receipts without
    silently running any operation. Also catches cycles in historical links.
    """
    if node.id in verified:
        return
    if node.id in visiting:
        raise SceneError("The connected scientific results contain a dependency cycle.")
    visiting.add(node.id)
    try:
        latest = node.state.data.get("latest")
        if isinstance(latest, dict):
            citations = latest["provenance"]["sources"]
            if citations:
                if not isinstance(citations, dict):
                    raise SceneError("The connected scientific provenance is invalid.")
                for citation in citations.values():
                    parent = _connected_source(document, node, citation)
                    if not granted(parent.kind.split(".", 1)[0]):
                        raise SceneError("Enable the connected scientific source pack.")
                    parent_pack = parent.kind.removeprefix("science_").split(".", 1)[0]
                    if not readable_result(parent.state.data, parent_pack):
                        raise SceneError("The connected scientific source is damaged.")
                    _verify_lineage(document, parent, granted, visiting, verified)
                pack = node.kind.removeprefix("science_").split(".", 1)[0]
                operation = next((op for op in operations(pack) if op.id == latest["operationId"]), None)
                if operation is None:
                    raise SceneError("The connected scientific operation is no longer available.")
                try:
                    _verify_sources(
                        document, node, latest["inputs"], citations, granted,
                        operation=operation,
                    )
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    raise SceneError("The connected scientific result is stale: " + str(exc)) from exc
    finally:
        visiting.remove(node.id)
    verified.add(node.id)


def _stable_sources(sources: dict) -> dict:
    # Runtime node IDs are reminted after workspace restore. Source identity
    # and the receipt/selection, not the transient id, determine freshness.
    return {
        field: {
            "sourceKey": ref.get("sourceKey"), "selector": ref.get("selector"),
            "resultHash": ref.get("resultHash"), "receiptHash": ref.get("receiptHash"),
        }
        for field, ref in sorted(sources.items())
    }


def config_hash(node: Any) -> str:
    pack, operation, config = _configuration(node)
    return fingerprint([
        "science_runner_v1", pack, VERSION, operation.id, node.state.data.get("nodeKey"),
        {key: value for key, value in config["inputs"].items() if key not in config["sources"]},
        {field: {"sourceKey": ref.get("sourceKey"), "selector": ref.get("selector")}
         for field, ref in sorted(config["sources"].items())},
    ])


def output_hash(node: Any) -> str:
    state = node.state.data
    table = state.get("table")
    return fingerprint([
        state.get("latest"), node.content,
        table.get("provenance", {}).get("receiptHash") if isinstance(table, dict) else None,
    ])


def prepare(document: Any, node: Any, granted: Any) -> tuple[Any, dict, dict, str, bool]:
    """Resolve operands from the live graph with the Workbench's own caps.

    The stored numerical value is only a fallback for an unlinked field.
    Linked fields are always replaced by the currently verified typed output.
    """
    pack, operation, config = _configuration(node)
    if not granted("science_" + pack):
        raise SceneError("Enable the scientific pack in Settings before running it.")
    raw = dict(config["inputs"])
    references = {}
    checked_sources: set[str] = set()
    for field, saved in config["sources"].items():
        source = _connected_source(document, node, saved)
        if not granted(source.kind.split(".", 1)[0]):
            raise SceneError("Enable the connected scientific source pack.")
        source_data = source.state.data
        source_pack = source.kind.removeprefix("science_").split(".", 1)[0]
        if not readable_result(source_data, source_pack):
            raise SceneError("The connected scientific result is damaged.")
        _verify_lineage(document, source, granted, set(), checked_sources)
        selector = saved["selector"]
        latest: dict[str, Any] | None = None
        if len(selector) == 5 and selector[0] == "table_column":
            table = source_data.get("table")
            if not table_metadata_valid(table):
                raise SceneError("The connected typed table is unavailable.")
            try:
                value = numeric_column(table, *selector[1:])
            except (ValueError, IndexError, KeyError, TypeError) as exc:
                raise SceneError("The connected typed table selection is invalid.") from exc
            result_hash = table["asset_ref"]
            receipt_hash = table["provenance"]["receiptHash"]
        else:
            latest = source_data.get("latest")
            if not isinstance(latest, dict):
                raise SceneError("Run the connected scientific source first.")
            try:
                value = _extract(latest, selector)
            except (ValueError, IndexError, KeyError, TypeError) as exc:
                raise SceneError("The connected scientific output selection is unavailable.") from exc
            result_hash = latest["provenance"]["resultHash"]
            receipt_hash = latest["provenance"]["receiptHash"]
        # Keep a linked scalar's *source* unit, rather than silently treating
        # a centimetre output as metres after replacing the saved literal.
        numeric_field = next((item for item in operation.fields if item.key == field), None)
        if (numeric_field is not None and numeric_field.kind in {"number", "integer"}
                and len(selector) == 2 and selector[0] == "values"
                and isinstance(selector[1], str) and type(value) in (int, float)):
            try:
                if latest is None:
                    raise SceneError("The connected scientific result is unavailable.")
                measurement = connected_measurement(latest, selector, source_data["nodeKey"])
            except ValueError as exc:
                raise SceneError(str(exc)) from exc
            unit = measurement["unit"] if measurement is not None else result_unit(selector[1])
            raw[field] = f"{value} {unit}" if unit else value
        else:
            raw[field] = value
        references[field] = {
            "nodeId": source.id, "sourceKey": source_data["nodeKey"],
            "selector": selector, "resultHash": result_hash, "receiptHash": receipt_hash,
        }
    try:
        inputs, input_units = validated_inputs_with_units(operation, raw)
        sources = _verify_sources(document, node, inputs, references, granted,
                                  operation=operation)
    except (ValueError, IndexError, TypeError, KeyError) as exc:
        raise SceneError(str(exc)) from exc
    pack = node.kind.removeprefix("science_").split(".", 1)[0]
    reference_data = references_for_operation(pack, operation.id, inputs)
    current = node.state.data.get("latest")
    if isinstance(current, dict) and node.content != current["summary"]:
        raise SceneError("The scientific output has manual edits; review them before running.")
    same = bool(
        isinstance(current, dict) and current["provenance"]["packVersion"] == VERSION
        and current["operationId"] == operation.id
        and current["inputs"] == inputs
        and current["provenance"].get("inputUnits") == input_units
        and current["provenance"].get("referenceData") == reference_data
        and _same_sources(current["provenance"].get("sources", {}), sources)
    )
    # Dispatch the original validated literals so the worker retains submitted
    # units in provenance; pin the normalized values AND unit choices for replay.
    digest = fingerprint([VERSION, operation.id, inputs, input_units, _stable_sources(sources), reference_data])
    return operation, raw, sources, digest, same
