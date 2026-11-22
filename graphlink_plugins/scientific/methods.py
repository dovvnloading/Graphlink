"""Readable scientific methods built from verified, saved result receipts."""

from __future__ import annotations

import json

from .catalog import PACKS, operations
from .core import VERSION, readable_result

MAX_VALUE_CHARS = 2000
MAX_OUTPUT_VALUES = 64


def _single_line(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def _value_lines(label: str, value: object) -> list[str]:
    # Indented JSON prevents data containing Markdown from becoming headings.
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    if len(rendered) > MAX_VALUE_CHARS:
        rendered = (rendered[:MAX_VALUE_CHARS] +
                    f"… [excerpt of {len(rendered)} characters; see canonical result JSON]")
    return [f"- {_single_line(label)}:", f"    {rendered}"]


def render_result_methods(pack: str, data: dict, *, node_id: str, title: str) -> str:
    """Check the receipt before formatting; never trust browser-provided result JSON.

    Operation method descriptions belong to the live catalog, not the signed
    receipt, so they are only attributed when the saved pack version matches.
    """
    if pack not in PACKS or not isinstance(data, dict) or not readable_result(data, pack):
        raise ValueError("The scientific result is unavailable or its receipt is damaged.")
    result = data.get("latest")
    if not isinstance(result, dict):
        raise ValueError("Run a calculation before exporting its methods.")

    receipt = result["provenance"]
    version = receipt["packVersion"]
    operation = next((op for op in operations(pack) if op.id == result["operationId"]), None)
    catalog_matches = version == VERSION and operation is not None
    lines = [
        f"## {_single_line(title)}", "",
        f"**Source node:** {_single_line(node_id)}",
        f"**Pack:** {pack} (version {version})",
        f"**Operation:** {result['operationId']}" +
        (f" — {operation.title}" if catalog_matches and operation is not None else ""),
        f"**Recorded:** {receipt['createdAt']}",
        f"**Input SHA-256:** {receipt['inputHash']}",
        f"**Result SHA-256:** {receipt['resultHash']}",
        f"**Receipt SHA-256:** {receipt.get('receiptHash', 'legacy 1.0.0 receipt; result hash only')}",
        "", "### Method", "",
    ]
    if catalog_matches and operation is not None:
        lines.append(operation.methods or operation.description or "No catalog method description was provided.")
    else:
        lines.append(
            "The saved pack version differs from the installed catalog (or this "
            "operation is no longer registered). Consult the saved version's "
            "documentation; current method prose is not attributed to this run."
        )

    lines.extend(["", "### Recorded inputs (normalized)", ""])
    labels = {field.key: field.label for field in operation.fields} if catalog_matches and operation is not None else {}
    for name, value in result["inputs"].items():
        label = name + (f" ({labels[name]})" if name in labels else "")
        lines.extend(_value_lines(label, value))
    if receipt.get("inputUnits") is not None:
        lines.extend(["", "**Input units as supplied:**", ""])
        lines.extend(_value_lines("inputUnits", receipt["inputUnits"]))

    lines.extend(["", "### Connected input provenance", ""])
    if receipt["sources"]:
        for field, reference in sorted(receipt["sources"].items()):
            lines.extend(_value_lines(field, reference))
    else:
        lines.append("No connected result inputs were recorded.")

    lines.extend(["", "### Reference datasets", ""])
    if receipt.get("referenceData"):
        for reference in receipt["referenceData"]:
            lines.extend(_value_lines("Dataset receipt", reference))
    else:
        lines.append("None recorded.")

    lines.extend(["", "### Recorded result", ""])
    lines.extend(_value_lines("Summary", result["summary"]))
    values = list(result["values"].items())
    for name, value in values[:MAX_OUTPUT_VALUES]:
        lines.extend(_value_lines(name, value))
    if len(values) > MAX_OUTPUT_VALUES:
        lines.append(f"{len(values) - MAX_OUTPUT_VALUES} further named outputs are in Export JSON.")
    if "requestedUncertaintyMethod" in receipt:
        lines.extend(_value_lines("Requested uncertainty method", receipt["requestedUncertaintyMethod"]))
    if "uncertainty" in result:
        lines.extend(_value_lines("Propagated uncertainty method", result["uncertainty"]["method"]))
        lines.append("Complete uncertainty estimates and budgets are in Export JSON.")
    if "table" in result:
        table = result["table"]
        lines.append(f"Inline result table: {len(table['rows'])} rows, {len(table['columns'])} columns.")
    if "tableAsset" in result:
        asset = result["tableAsset"]
        lines.append(
            f"Asset-backed result table: {asset['rowCount']} rows, {asset['columnCount']} columns; "
            "use Export CSV for all rows."
        )
    if "plot" in result:
        lines.append(f"Result plot: {len(result['plot']['x'])} recorded points.")
    if "views" in result:
        lines.append(f"Typed result views: {len(result['views'])}.")

    if catalog_matches and operation is not None and operation.citations:
        lines.extend(["", "### Method references", ""])
        lines.extend(f"- {citation.label}: {citation.url}" for citation in operation.citations)
    lines.extend([
        "", "This is a verified saved snapshot, not a recalculation or a check that "
        "connected sources are still current. Large values are explicitly excerpted; "
        "Export JSON for complete receipt-protected inputs and outputs.",
    ])
    return "\n".join(lines).rstrip() + "\n"
