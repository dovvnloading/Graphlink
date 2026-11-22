"""Resolve receipt-pinned column values, observation identities and units."""
from __future__ import annotations

from .core import Operation, _effective_unit, _field_with_units, _unit_field, fingerprint
from .data_table import numeric_selection
from .units import parse_unit


def column_binding(meta, selector, field, unit_inputs, request=None, *, table=None):
    if field is None or field.kind != "vector":
        raise ValueError("A connected numeric column requires a vector field.")
    request = {} if request is None else request
    if not isinstance(request, dict):
        raise ValueError("Invalid table binding. Select the source again.")
    _, column, policy, start, end = selector
    group = request.get("completeColumns")
    values, rows = numeric_selection(meta, column, policy, start, end, group, table=table)
    declared = meta["columns"][column]["unit"]
    assumed = request.get("assumedUnit")
    if assumed is not None:
        if not isinstance(assumed, str) or not assumed.strip() or len(assumed) > 128:
            raise ValueError("Declare a valid source unit.")
        parse_unit(assumed)
        if declared:
            try:
                parse_unit(declared)
            except ValueError:
                pass
            else:
                if assumed != declared:
                    raise ValueError("An assumption cannot replace a recognized source unit.")
    source_unit = assumed or declared
    target_unit = _effective_unit(field, unit_inputs)
    # Generic data operations (e.g. sample statistics) preserve the input's scale.
    # Their solver accepts bare magnitudes and imposes no physical quantity.
    generic = not field.unit and not _unit_field(field, unit_inputs) and target_unit == "1"
    if not generic and not source_unit:
        raise ValueError(f"{field.label}: the source column has no unit. Declare its unit before running.")
    if source_unit:
        try:
            parse_unit(source_unit)
        except ValueError as exc:
            raise ValueError(f"{field.label}: unrecognized source unit {source_unit!r}. Declare its unit before running.") from exc
    literals = values if generic else [f"{value} {source_unit}" for value in values]
    normalized, _ = _field_with_units(field, literals, target_unit)
    evidence = {
        "columnLabel": meta["columns"][column]["label"],
        "sourceUnit": source_unit, "targetUnit": (source_unit or "1") if generic else target_unit,
        "rowDigest": fingerprint(rows), "retained": len(rows), "omitted": end - start - len(rows),
    }
    if group is not None:
        evidence["completeColumns"] = sorted(group)
        evidence["completeLabels"] = [meta["columns"][i]["label"] for i in sorted(group)]
    if assumed is not None:
        evidence["assumedUnit"] = assumed
    return values, literals, normalized, evidence


def paired_rows(operation: Operation, sources: dict) -> None:
    """Equal vector lengths do not establish common observations."""
    for group in operation.paired_fields:
        identities = set()
        columns = set()
        bound = [sources[key] for key in group if key in sources]
        complete = []
        for ref in bound:
            if ref["selector"][0] in ("table_column", "result_table_column"):
                columns.add(ref["selector"][1])
                if "completeColumns" in ref["binding"]:
                    complete.append(set(ref["binding"]["completeColumns"]))
        if any(mask != columns for mask in complete):
            raise ValueError("Complete-case columns must match the connected columns of the paired fields.")
        if columns and any(ref["selector"][0] not in ("table_column", "result_table_column") for ref in bound):
            raise ValueError("The paired sources have no common row identity. Prepare them in one typed table.")

        for key in group:
            ref = sources.get(key)
            if not ref or ref["selector"][0] not in ("table_column", "result_table_column"):
                continue
            selector = ref["selector"]
            identities.add((ref.get("sourceKey") or ref["nodeId"], ref["receiptHash"],
                            selector[0], selector[3], selector[4], ref["binding"]["rowDigest"]))
        if len(identities) > 1:
            raise ValueError(f"Paired fields {', '.join(group)} require the same source rows. "
                             "Select one table and row range, then use complete rows across the paired columns.")
