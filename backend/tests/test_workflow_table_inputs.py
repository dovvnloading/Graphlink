"""Table inputs are decoded strictly, never reshaped (#1094)."""

from __future__ import annotations

import pytest

from backend import chat_library
from backend.workflow.contracts import PortSpec, TableValue, WorkflowSpec
from backend.workflow.library import typed_inputs
from backend.workflow.ports import table_from_json
from backend.workflow.starters import starter_document
from backend.workflow.wire import decode_value

MALFORMED = [
    pytest.param(["name", "value"], ["42"], id="string-row"),
    pytest.param("ab", [["one", "two"]], id="string-columns"),
    pytest.param(["a", "b"], [{"x": 1, "y": 2}], id="object-row"),
    pytest.param(["a", "b"], [["only-one"]], id="ragged-row"),
    pytest.param(["a"], "rows", id="string-rows"),
    pytest.param([1], [[1]], id="non-string-column"),
    pytest.param(["a"], [[{"nested": True}]], id="non-scalar-cell"),
    pytest.param(["a"], [[float("nan")]], id="nonfinite-cell"),
]
SPEC = WorkflowSpec(1, "w", "r", "Rows", (PortSpec("rows", "table", True),), (), ())


@pytest.mark.parametrize(("columns", "rows"), MALFORMED)
def test_malformed_tables_are_rejected_at_every_decoder(columns, rows):
    with pytest.raises(ValueError):
        table_from_json(columns, rows)
    with pytest.raises(ValueError):
        typed_inputs(SPEC, {"rows": {"columns": columns, "rows": rows}})
    with pytest.raises(ValueError):
        decode_value({"kind": "table", "columns": columns, "rows": rows})


def test_valid_tables_round_trip_without_coercion():
    columns, rows = ["text", "empty", "none", "flag", "n", "numeric_text"], [["x", "", None, True, 1.5, "42"]]
    expected = TableValue(tuple(columns), (tuple(rows[0]),))
    assert table_from_json(columns, rows) == expected
    assert typed_inputs(SPEC, {"rows": {"columns": columns, "rows": rows}}) == {"rows": expected}
    assert decode_value({"kind": "table", "columns": columns, "rows": rows}) == expected
    assert table_from_json([], []) == TableValue((), ())


def test_a_malformed_table_cannot_create_a_preset(tmp_path):
    from backend.workflow.api import WorkflowService

    path = tmp_path / "chats.db"
    workspace = chat_library.get_default_workspace_id(path)
    automation = WorkflowService(path).automation
    document = starter_document("table-chart-report-v1")
    request = {"spec": document["spec"], "parameters": {
        "table": {"kind": "table", "columns": ["team", "tickets"], "rows": ["42"]}}}
    body = {"request": request, "name": "Report", "allowed_effects": ["pure"], "cost_usd": 0,
            "trigger": None, "fingerprint": "0" * 64, "approved": True}
    with pytest.raises(ValueError, match="row arrays"):
        automation.save(workspace, body)
    assert automation.list(workspace) == []
