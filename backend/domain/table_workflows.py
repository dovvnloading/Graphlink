"""Manually run, versioned chart and document outputs from a canvas CSV table."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from graphlink_chart_data import ChartDataError, canonicalize_chart_data
from backend.domain._composed import SceneDocumentParts
from backend.domain.model import SceneError, SceneNode
from backend.domain.node_access import with_state
from backend.domain.node_states import ARTIFACT_MAX_CHARS, ArtifactState, ChartState, NoteState, TableOutput, TableVersion

# CSV is edited inline and travels on the ordinary scene wire. Bound the work
# before invoking the CSV reader, which otherwise allocates per field.
MAX_TABLE_CHARS = 256_000
MAX_TABLE_ROWS = 10_000
MAX_TABLE_COLUMNS = 128
CONTRIBUTOR_PAGE_SIZE = 50


@dataclass
class _Table:
    headers: list[str]
    rows: list[list[str]]

    def columns(self, label: str, value: str) -> tuple[int, int]:
        if label == value:
            raise SceneError("Choose different group and numeric columns.")
        for name in (label, value):
            if name not in self.headers:
                raise SceneError(f"Column {name!r} is missing from this table.")
        return self.headers.index(label), self.headers.index(value)

    def fingerprint(self, label: str, value: str) -> str:
        group_index, value_index = self.columns(label, value)
        # Length-delimited JSON preserves quoted commas, embedded newlines,
        # Unicode and row order. Reordering columns doesn't change a result.
        return _fingerprint([[label, value], *[[r[group_index], r[value_index]] for r in self.rows]])


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")).hexdigest()


def parse_table(text: str) -> _Table:
    if not isinstance(text, str) or len(text) > MAX_TABLE_CHARS:
        raise SceneError(f"CSV must be text with at most {MAX_TABLE_CHARS:,} characters.")
    if "\x00" in text:
        raise SceneError("CSV contains a null character.")
    reader = csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline=""), strict=True)
    try:
        headers = [name.strip() for name in next(reader, [])]
        if len(headers) < 2 or len(headers) > MAX_TABLE_COLUMNS or not all(headers) or len(set(headers)) != len(headers):
            raise SceneError(f"Use 2 to {MAX_TABLE_COLUMNS} unique, nonempty column headers.")
        if any(len(name) > 160 for name in headers):
            raise SceneError("Column names must fit in 160 characters.")
        rows = []
        for index, row in enumerate(reader, start=1):
            if index > MAX_TABLE_ROWS:
                raise SceneError(f"A table supports up to {MAX_TABLE_ROWS:,} data rows.")
            if len(row) != len(headers):
                raise SceneError(f"Data row {index} has {len(row)} cells; expected {len(headers)}.")
            rows.append(row)
    except csv.Error as exc:
        raise SceneError(f"Invalid CSV near line {reader.line_num}: {exc}.") from exc
    if not rows:
        raise SceneError("Add at least one data row below the headers.")
    return _Table(headers, rows)


def _canonical_csv(table: _Table) -> str:
    output = io.StringIO(newline="")
    # CRLF makes csv.writer quote fields containing either CR or LF.
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(table.headers)
    writer.writerows(table.rows)
    result = output.getvalue()[:-2]
    if len(result) > MAX_TABLE_CHARS:
        raise SceneError("The normalized CSV exceeds the table size limit.")
    return result


@dataclass
class TableReadCache:
    """Parse a source once per publication and hash each requested projection once."""

    tables: dict[str, _Table] = field(default_factory=dict)
    hashes: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def digest(self, source: SceneNode, label: str, value: str) -> str:
        key = (source.id, label, value)
        if key not in self.hashes:
            if source.id not in self.tables:
                self.tables[source.id] = parse_table(source.content)
            try:
                self.hashes[key] = self.tables[source.id].fingerprint(label, value)
            except SceneError:
                self.hashes[key] = ""  # Missing column: retain the last result, mark it stale.
        return self.hashes[key]


def _markdown_literal(text: str) -> str:
    # GFM splits cells before parsing code spans. Render pipes as character
    # references between spans, even when a CSV cell contains a backslash
    # immediately before the pipe. Within each span links/HTML stay literal.
    parts = []
    for part in " ".join(text.split()).split("|"):
        if part:
            ticks = "`" * (1 + max((len(run) for run in re.findall(r"`+", part)), default=0))
            padded = part if part.isspace() else f" {part} "
            parts.append(f"{ticks}{padded}{ticks}")
        else:
            parts.append("")
    return "&#124;".join(parts)


def _group_label(cell: str) -> str:
    return " ".join(cell.split())


def _group_totals(table: _Table, label: str, value: str) -> dict[str, Decimal]:
    label_index, value_index = table.columns(label, value)
    totals: dict[str, Decimal] = {}
    # Every input must round-trip through Graphlink's JSON number renderer.
    # This bounds the exponents to [-324, 308]; 660 digits of accumulation
    # precision cover their full span plus the carry from 10,000 rows.
    with localcontext() as context:
        context.prec = 660
        for index, row in enumerate(table.rows, start=1):
            group = _group_label(row[label_index])
            if not group:
                raise SceneError(f"Data row {index} has an empty group in {label!r}.")
            token = row[value_index].strip()
            try:
                number = Decimal(token)
                rendered = float(number)
            except (ValueError, OverflowError, InvalidOperation):
                rendered = math.nan
            if not math.isfinite(rendered):
                raise SceneError(f"Data row {index} in {value!r} must be a finite number.")
            if number != Decimal(str(rendered)):
                raise SceneError(f"Data row {index} in {value!r} exceeds the precision of a chart number.")
            totals[group] = totals.get(group, Decimal(0)) + Decimal(str(rendered))
    return totals


def _generate(table: _Table, kind: str, label: str, value: str, version: int) -> TableVersion:
    totals = _group_totals(table, label, value)
    numbers = []
    for group, total in totals.items():
        rendered = float(total)
        if not math.isfinite(rendered) or total != Decimal(str(rendered)):
            raise SceneError(f"The total for {group!r} exceeds the range or precision of a chart number.")
        numbers.append(rendered)
    if kind == "chart":
        try:
            result: str | dict[str, Any] = canonicalize_chart_data({
                "type": "bar", "title": f"{value} by {label}", "labels": list(totals), "values": numbers,
                "xAxis": label, "yAxis": value,
            }, "bar")
        except ChartDataError as exc:
            raise SceneError(str(exc)) from exc
    else:
        lines = [
            "# Table report", "", f"Sum of {_markdown_literal(value)} by {_markdown_literal(label)} "
            f"across {len(table.rows)} data rows.", "",
            f"| {_markdown_literal(label)} | Sum of {_markdown_literal(value)} |", "| --- | ---: |",
        ]
        for name, number in zip(totals, numbers, strict=True):
            amount = str(number).removesuffix(".0")
            lines.append(f"| {_markdown_literal(name)} | {amount} |")
        result = "\n".join(lines)
        if len(result) > ARTIFACT_MAX_CHARS:
            raise SceneError("The generated report exceeds the Drafter document limit.")
    return TableVersion(version, table.fingerprint(label, value), result)


def _version_wire(version: TableVersion) -> dict[str, Any]:
    key = "markdown" if isinstance(version.result, str) else "chartData"
    return {"version": version.version, "sourceHash": version.source_hash, key: copy.deepcopy(version.result)}


def table_output(node: SceneNode) -> TableOutput | None:
    if (node.kind == "chart" and isinstance(node.state, ChartState)) or (
        node.kind == "artifact" and isinstance(node.state, ArtifactState)
    ):
        return node.state.table_output if isinstance(node.state.table_output, TableOutput) else None
    return None


def _current_result(node: SceneNode) -> str | dict[str, Any]:
    if isinstance(node.state, ArtifactState):
        return node.state.artifact_content
    return with_state(node, ChartState).state.chart_data


def _write_result(node: SceneNode, version: TableVersion) -> None:
    if isinstance(node.state, ArtifactState) and isinstance(version.result, str):
        node.state.artifact_content = version.result
        node.state.artifact_error = ""
    elif isinstance(node.state, ChartState) and isinstance(version.result, dict):
        node.state.chart_data = copy.deepcopy(version.result)
        node.state.chart_type = "bar"
        node.state.chart_error = ""
        node.title = version.result["title"]
    else:
        raise SceneError("This version has the wrong output type.")


class TableOps(SceneDocumentParts):
    def add_table(self, x: float, y: float, csv_text: str) -> SceneNode:
        content = _canonical_csv(parse_table(csv_text))
        node = self.add_note(x, y)
        with_state(node, NoteState).state.is_table_source = True
        node.title = "Table"
        node.content = content
        return node

    def set_table_source(self, source_id: str, csv_text: str, expected_content: str) -> None:
        node = self.nodes.get(source_id)
        if node is None or not isinstance(node.state, NoteState) or not node.state.is_table_source:
            raise SceneError("Choose a table source.")
        if node.content != expected_content:
            raise SceneError("The table changed while you were editing. Reopen it to use the latest data.")
        node.content = _canonical_csv(parse_table(csv_text))

    def _read_table(self, source_id: str) -> _Table:
        node = self.nodes.get(source_id)
        if node is None or not isinstance(node.state, NoteState) or not node.state.is_table_source:
            raise SceneError("The table source is unavailable.")
        return parse_table(node.content)

    def create_table_output(self, source_id: str, kind: str, label_column: str, value_column: str) -> SceneNode:
        if kind not in ("chart", "artifact"):
            raise SceneError("Choose a chart or a Drafter report.")
        version = _generate(self._read_table(source_id), kind, label_column, value_column, 1)
        x, y = self.place_child(source_id, kind, prefer="right")
        binding = TableOutput(source_id, label_column, value_column, version)
        if isinstance(version.result, dict):
            node = self.add_chart_node(x, y, None, "bar", copy.deepcopy(version.result))
            with_state(node, ChartState).state.table_output = binding
        else:
            node = self.add_artifact_node(x, y, None)
            artifact = with_state(node, ArtifactState)
            artifact.state.table_output = binding
            artifact.state.artifact_content = version.result
        return node

    def _require_table_output(self, target_id: str) -> tuple[SceneNode, TableOutput]:
        node = self.nodes.get(target_id)
        if node is not None:
            output = table_output(node)
            if output is not None:
                return node, output
        raise SceneError("Choose a generated table output.")

    def _editable_table_output(self, target_id: str) -> tuple[SceneNode, TableOutput]:
        node, output = self._require_table_output(target_id)
        if isinstance(node.state, ArtifactState) and (node.state.artifact_proposal_id or node.pending_request_id):
            raise SceneError("Finish the pending Drafter work before replacing this report.")
        if _current_result(node) != output.current.result:
            raise SceneError("This output was edited. Keep the edits and create another output, or undo them before rerunning.")
        return node, output

    def run_table_output(self, target_id: str) -> None:
        node, output = self._editable_table_output(target_id)
        version = _generate(self._read_table(output.source_id), node.kind, output.label_column, output.value_column, output.current.version + 1)
        if version.source_hash == output.current.source_hash:
            return
        _write_result(node, version)
        output.previous, output.current = output.current, version

    def get_table_contributors(self, target_id: str, group: str, offset: int) -> dict[str, Any]:
        node, output = self._require_table_output(target_id)
        if _current_result(node) != output.current.result:
            raise SceneError("The displayed output was edited. Its values cannot be traced to this table version.")
        if not isinstance(group, str) or not group or len(group) > MAX_TABLE_CHARS:
            raise SceneError("Choose a group from this output.")
        group = _group_label(group)
        if type(offset) is not int or offset < 0:
            raise SceneError("The row offset must be a nonnegative integer.")
        table = self._read_table(output.source_id)
        digest = table.fingerprint(output.label_column, output.value_column)
        if digest != output.current.source_hash:
            raise SceneError("The table changed. Rerun the output before inspecting its source rows.")
        label_index, value_index = table.columns(output.label_column, output.value_column)
        page = []
        matches = 0
        for index, row in enumerate(table.rows, start=1):
            if _group_label(row[label_index]) == group:
                if offset <= matches < offset + CONTRIBUTOR_PAGE_SIZE:
                    page.append({"rowNumber": index, "value": row[value_index]})
                matches += 1
        if not matches:
            raise SceneError("This group does not appear in the generated output.")
        if offset >= matches:
            raise SceneError("The row offset is outside this group.")
        return {
            "group": group, "labelColumn": output.label_column, "valueColumn": output.value_column,
            "sourceHash": digest, "totalRows": matches, "offset": offset, "rows": page,
        }

    def get_table_output_history(self, target_id: str) -> dict[str, Any]:
        _, output = self._require_table_output(target_id)
        pair = {"current": _version_wire(output.current), "previous": _version_wire(output.previous) if output.previous else None}
        return {**pair, "revisionToken": _fingerprint(pair)}

    def restore_table_output(self, target_id: str, expected_revision_token: str) -> None:
        node, output = self._editable_table_output(target_id)
        if output.previous is None:
            raise SceneError("This output has no previous version.")
        if self.get_table_output_history(target_id)["revisionToken"] != expected_revision_token:
            raise SceneError("These versions changed. Reopen the comparison before restoring.")
        version = TableVersion(output.current.version + 1, output.previous.source_hash, output.previous.result)
        _write_result(node, version)
        output.previous, output.current = output.current, version

    def _table_output_wire(self, node: SceneNode, cache: TableReadCache | None = None) -> dict[str, Any]:
        output = table_output(node)
        if output is None:
            return {}
        wire: dict[str, Any] = {
            "tableLabelColumn": output.label_column, "tableValueColumn": output.value_column,
            "tableOutputVersion": output.current.version, "tableSourceHash": output.current.source_hash,
        }
        if output.previous is not None:
            wire["tableHasPrevious"] = True
        if _current_result(node) != output.current.result:
            wire["tableOutputEdited"] = True
        source = self.nodes.get(output.source_id)
        if source is not None:
            digest = (cache or TableReadCache()).digest(source, output.label_column, output.value_column)
            wire["tableInputSourceId"] = source.id
            wire["tableInputStatus"] = "current" if digest == output.current.source_hash else "stale"
        return wire


def saved_table_output(output: TableOutput) -> dict[str, Any]:
    return {
        "source": output.source_id, "label_column": output.label_column, "value_column": output.value_column,
        "current": _version_wire(output.current), "previous": _version_wire(output.previous) if output.previous else None,
    }


def _load_version(raw: Any, kind: str) -> TableVersion | None:
    if not isinstance(raw, dict):
        return None
    number, digest = raw.get("version"), raw.get("sourceHash")
    if type(number) is not int or number <= 0 or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None
    if kind == "chart":
        try:
            result: str | dict[str, Any] = canonicalize_chart_data(raw.get("chartData"), "bar")
        except ChartDataError:
            return None
    else:
        body = raw.get("markdown")
        if not isinstance(body, str) or len(body) > ARTIFACT_MAX_CHARS:
            return None
        result = body
    return TableVersion(number, digest, result)


def restore_table_data(nodes: dict[str, SceneNode], ids: dict[str, str], sources: Any, outputs: Any) -> None:
    # SQLite notes use a fixed-column table. Restore their type and output
    # references from saved graph JSON after ordinary IDs have been remapped.
    if isinstance(sources, list):
        for saved in sources:
            node = nodes.get(ids.get(saved, "")) if isinstance(saved, str) else None
            if node is None or not isinstance(node.state, NoteState):
                continue
            if node.state.is_system_prompt or node.state.is_branch_comparison or node.state.is_summary_note:
                continue
            try:
                parse_table(node.content)
            except SceneError:
                continue  # Keep the original text as an editable note.
            node.title = "Table"
            node.state.is_table_source = True
    if not isinstance(outputs, dict):
        return
    for saved, raw in outputs.items():
        node = nodes.get(ids.get(saved, ""))
        if node is None or not isinstance(node.state, (ArtifactState, ChartState)) or not isinstance(raw, dict):
            continue
        label, value, source_id = raw.get("label_column"), raw.get("value_column"), raw.get("source")
        if not isinstance(label, str) or not isinstance(value, str):
            continue
        if not (0 < len(label) <= 160 and 0 < len(value) <= 160) or label == value:
            continue
        current = _load_version(raw.get("current"), node.kind)
        if current is None:
            continue
        previous = _load_version(raw.get("previous"), node.kind)
        if previous is not None and previous.version >= current.version:
            previous = None
        source = nodes.get(ids.get(source_id, "")) if isinstance(source_id, str) else None
        live_source = source.id if source is not None and isinstance(source.state, NoteState) and source.state.is_table_source else ""
        node.state.table_output = TableOutput(live_source, label, value, current, previous)
