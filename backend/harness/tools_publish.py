"""Publish an existing workspace file as a native, run-undoable canvas node.

The source file stays in the run's trusted/scratch workspace. Its bounded
contents are copied to the scene; the new node and its agent edge are a
single record_command mutation stamped with this particular harness run.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from pathlib import Path

from api_provider import _raise_if_cancelled
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.domain.table_workflows import MAX_TABLE_CHARS, MAX_TABLE_COLUMNS, MAX_TABLE_ROWS
from backend.harness import document_read
from backend.harness.tools_fs import _bound_root, _is_metadata_path, _read_bounded_bytes
from backend.harness.workspace import WorkspaceError, resolve_under_root
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import FS_READ, GRAPH_MUTATE, RunContext, ToolRegistry, ToolResult

USAGE_HINT = (
    "graph.publish_file copies a workspace image, CSV/Excel table, or "
    "Markdown/text document onto the canvas beside this agent; it is linked "
    "to the agent and undoable with this run."
)

PUBLISH_FILE_SPEC = ToolSpec(
    name="graph.publish_file",
    description=(
        "Publish an EXISTING file from this run's workspace to the canvas, "
        "immediately right of this agent, connected to the agent node. "
        "PNG, JPEG, GIF and WebP become image nodes; CSV and Excel "
        "(.xlsx/.xlsm) become editable table nodes; Markdown (.md/.markdown) "
        "and UTF-8 text (.txt) become document nodes. For Excel, the first "
        "worksheet is used unless sheet names another worksheet. This "
        "copies file contents into the canvas, not a live disk link. "
        "The node is part of this run's Undo run history."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the bound workspace (or an absolute path inside it).",
            },
            "sheet": {
                "type": "string",
                "description": "Optional Excel worksheet name; omitted means the first worksheet.",
            },
        },
        "required": ["path"],
    },
)

_IMAGE_FORMATS = {
    ".png": ("PNG", "image/png"),
    ".jpg": ("JPEG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
    ".gif": ("GIF", "image/gif"),
    ".webp": ("WEBP", "image/webp"),
}
_TEXT_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}
_EXCEL_TYPES = frozenset({".xlsx", ".xlsm"})
_CELL_REF = re.compile(r"([A-Z]+)[1-9][0-9]*\Z")


def _excel_value(cell, strings: list[str]) -> str:
    """Read a cached cell value, without evaluating workbook formulas."""
    ns = f"{{{document_read._SPREADSHEET_NS}}}"
    value = cell.find(f"{ns}v")
    raw = (value.text or "") if value is not None else ""
    kind = cell.get("t")
    if kind == "s" and raw:
        try:
            index = int(raw)
            if not 0 <= index < len(strings):
                raise IndexError(index)
            raw = strings[index]
        except (ValueError, IndexError) as exc:
            raise WorkspaceError("Excel file has an invalid shared-string index.") from exc
    elif kind == "inlineStr":
        inline = cell.find(f"{ns}is")
        raw = "".join(text.text or "" for text in inline.iter(f"{ns}t")) if inline is not None else ""
    elif kind == "b" and raw:
        raw = "TRUE" if raw == "1" else "FALSE"
    elif not raw:
        formula = cell.find(f"{ns}f")
        if formula is not None:
            raw = "=" + (formula.text or "")
    return raw


def _excel_column(reference: str) -> int:
    match = _CELL_REF.fullmatch(reference.upper())
    if match is None:
        raise WorkspaceError("Excel cell is missing a valid A1-style reference.")
    number = 0
    for letter in match.group(1):
        number = number * 26 + ord(letter) - ord("A") + 1
        if number > 16_384:  # Excel's XFD column limit; reject corrupt input.
            raise WorkspaceError("Excel cell is outside its supported column range.")
    return number


def _excel_to_csv(data: bytes, sheet: str | None) -> str:
    """Convert one Excel worksheet to the canvas table's bounded CSV format.

    Reuse fs.read's Office ZIP/XML security preflight. Only the chosen sheet
    is expanded into table rows; sparse cell references retain their columns.
    No formula, macro, or external workbook relationship is evaluated.
    """
    ns = f"{{{document_read._SPREADSHEET_NS}}}"
    with document_read._preflight_office_zip(data, "XLSX") as archive:
        entries = document_read._worksheet_entries(archive)
        selected = entries[0] if sheet is None else next(
            (entry for entry in entries if entry[0].casefold() == sheet.casefold()), None
        )
        if selected is None:
            raise WorkspaceError(f"Excel worksheet {sheet!r} was not found.")
        _name, member = selected

        strings = document_read._shared_strings(archive)
        xml = document_read._xml(archive, member)
        rows: list[dict[int, str]] = []
        first_col: int | None = None
        last_col = 0
        for row in xml.iter(f"{ns}row"):
            cells: dict[int, str] = {}
            for cell in row.findall(f"{ns}c"):
                value = _excel_value(cell, strings)
                if value:
                    index = _excel_column(cell.get("r") or "")
                    cells[index] = value
            if not cells:  # Blank styling/spacing rows aren't table data.
                continue
            first_col = min(min(cells), first_col) if first_col is not None else min(cells)
            last_col = max(last_col, max(cells))
            if last_col - first_col + 1 > MAX_TABLE_COLUMNS:
                raise WorkspaceError("Excel table has too many columns.")
            rows.append(cells)
            if len(rows) > MAX_TABLE_ROWS + 1:
                raise WorkspaceError("Excel table has too many rows.")

        table: list[list[str]] = []
        if first_col is not None:
            # Canvas CSV tables require nonempty headers. Give sparse header
            # columns stable placeholder names rather than rejecting an
            # otherwise valid worksheet whose later rows use those columns.
            headers = rows[0].copy()
            used = {value.strip() for value in headers.values() if value.strip()}
            for column in range(first_col, last_col + 1):
                # Excel preserves whitespace-only cell text. Canvas strips it
                # and rejects the resulting empty header, so repair those
                # cells just like wholly missing first-row cells.
                if not headers.get(column, "").strip():
                    base = f"Column {column}"
                    label = base
                    suffix = 2
                    while label in used:
                        label = f"{base} ({suffix})"
                        suffix += 1
                    headers[column] = label
                    used.add(label)
            rows[0] = headers
            table = [
                [cells.get(column, "") for column in range(first_col, last_col + 1)]
                for cells in rows
            ]

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(table)
    csv_text = output.getvalue()
    if len(csv_text) > MAX_TABLE_CHARS:
        raise WorkspaceError("Excel table exceeds the canvas CSV size limit.")
    return csv_text


def _image_type(data: bytes, suffix: str) -> str:
    """Verify actual image format, rather than trusting a file extension."""
    from PIL import Image, UnidentifiedImageError

    expected, mime_type = _IMAGE_FORMATS[suffix]
    try:
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format != expected:
                raise WorkspaceError("Image contents do not match the file extension.")
            if picture.width * picture.height > 40_000_000:
                raise WorkspaceError("Image dimensions are too large to publish.")
            picture.verify()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise WorkspaceError(f"Image is not a valid {expected} file.") from exc
    return mime_type


def _load_file(path: Path, sheet: str | None) -> tuple[str, str | bytes, str, int]:
    suffix = path.suffix.lower()
    if suffix not in _IMAGE_FORMATS and suffix not in _TEXT_TYPES and suffix not in _EXCEL_TYPES and suffix != ".csv":
        raise WorkspaceError(
            "Unsupported file type. Use PNG/JPEG/GIF/WebP, CSV, XLSX/XLSM, Markdown, or TXT."
        )
    if sheet is not None and suffix not in _EXCEL_TYPES:
        raise WorkspaceError("sheet is only supported for Excel files.")
    data = _read_bounded_bytes(path)  # Hard 8 MiB read cap shared with fs.read.
    if suffix in _IMAGE_FORMATS:
        return "image", data, _image_type(data, suffix), len(data)
    if suffix in _EXCEL_TYPES:
        return "table", _excel_to_csv(data, sheet), "text/csv", len(data)
    text = data.decode("utf-8-sig")  # Reject corrupt text; never silently replace.
    if len(text) > MAX_TABLE_CHARS:
        raise WorkspaceError("File exceeds the canvas text/table size limit.")
    if suffix == ".csv":
        return "table", text, "text/csv", len(data)
    return "document", text, _TEXT_TYPES[suffix], len(data)


def register_workspace_tools(registry: ToolRegistry, dispatcher, document: SceneDocument) -> None:
    async def publish_file(call: ToolCall, ctx: RunContext) -> ToolResult:
        raw_path = call.arguments.get("path")
        raw_sheet = call.arguments.get("sheet")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return ToolResult(content="graph.publish_file requires a file path.", is_error=True)
        if raw_sheet is not None and (not isinstance(raw_sheet, str) or not raw_sheet.strip()):
            return ToolResult(content="sheet must be a nonempty worksheet name.", is_error=True)

        root = _bound_root(ctx)
        agent_id = getattr(ctx, "harness_node_id", None)
        run_id = getattr(ctx, "run_id", None)
        if root is None or not agent_id or not run_id:
            return ToolResult(content="A running workspace agent and bound workspace are required.", is_error=True)

        agent = document.nodes.get(agent_id)
        if agent is None or agent.kind != "harness" or getattr(agent.state, "harness_run_id", None) != run_id:
            return ToolResult(content="This workspace-agent run is no longer active.", is_error=True)
        identity = (document.current_chat_id, document.current_workspace_id)
        try:
            source = resolve_under_root(root, raw_path)
            if _is_metadata_path(source):
                raise WorkspaceError("VCS metadata cannot be published.")
            if not source.is_file():
                raise WorkspaceError(f"Not a file: {raw_path!r}.")
            kind, payload, mime_type, byte_size = await asyncio.to_thread(_load_file, source, raw_sheet)
        except (OSError, WorkspaceError, UnicodeError, ValueError, SceneError) as exc:
            return ToolResult(content=f"Could not publish file: {exc}", is_error=True)

        _raise_if_cancelled(ctx.cancel.event if ctx.cancel is not None else None)
        if (document.current_chat_id, document.current_workspace_id) != identity:
            return ToolResult(content="The active chat or workspace changed during file loading.", is_error=True)
        agent = document.nodes.get(agent_id)
        if agent is None or agent.kind != "harness" or getattr(agent.state, "harness_run_id", None) != run_id:
            return ToolResult(content="This workspace-agent run is no longer active.", is_error=True)

        x, y = document.place_child(agent_id, "note" if kind == "table" else kind, prefer="right")

        def create():
            if kind == "image":
                return document.add_image_node(x, y, payload, source.name, agent_id, mime_type=mime_type)
            if kind == "document":
                return document.add_document_node(
                    x, y, source.name, payload, "document", agent_id,
                    mime_type=mime_type, byte_size=byte_size,
                )
            node = document.add_table(x, y, payload)
            node.title = source.name
            document.connect(agent_id, node.id)
            return node

        try:
            node, _command = document.record_command(
                "harnessPublishFile", "agent", create,
                node_ids=[agent_id], run_id=run_id,
            )
        except (SceneError, ValueError) as exc:
            return ToolResult(content=f"Could not publish file: {exc}", is_error=True)
        return ToolResult(content=json.dumps({
            "node_id": node.id, "kind": node.kind, "format": kind,
            "title": node.title, "path": raw_path,
        }))

    registry.register(
        PUBLISH_FILE_SPEC, publish_file, scopes={FS_READ, GRAPH_MUTATE}, approval="once",
    )
