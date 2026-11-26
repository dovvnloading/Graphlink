"""graph.group_sum emits numeric totals to typed downstream steps (#1095)."""

from __future__ import annotations

import asyncio

import pytest

from backend.canvas import SceneDocument
from backend.workflow.adapter import StepContext
from backend.workflow.artifacts import canonical
from backend.workflow.contracts import InputBinding, TableValue, WorkflowInputSource, WorkflowStepSpec, freeze_config
from backend.workflow.library import library_registry
from backend.workflow.scratch_adapters import detached_registry

REGISTRY = detached_registry(library_registry())


def _run(adapter_id, config, table):
    config_json = freeze_config(config)
    descriptor = REGISTRY.describe(adapter_id, "1", config_json)
    step = WorkflowStepSpec("s", descriptor, config_json, (InputBinding("table", WorkflowInputSource("t")),))
    adapter = REGISTRY.resolve(adapter_id, "1")
    prepared = adapter.prepare(step, {"table": table})
    context = StepContext("run", "s", 1, scratch_document=SceneDocument())
    return asyncio.run(adapter.run(prepared, context))


def _group(rows):
    table = TableValue(("group", "amount"), tuple(tuple(row) for row in rows))
    config = {"label_column": "group", "value_column": "amount", "output_kind": "table"}
    return _run("graph.group_sum", config, table)["result"]


def test_totals_are_numbers_and_chain_into_numeric_filter_and_sort():
    grouped = _group([["A", 2], ["B", 10]])
    assert grouped.rows == (("A", 2), ("B", 10))
    filtered = _run("transform.table_filter", {"column": "amount", "operator": "gte", "value": 5}, grouped)
    assert next(iter(filtered.values())).rows == (("B", 10),)
    ordered = _run("transform.table_sort", {"column": "amount", "direction": "asc"}, grouped)
    assert next(iter(ordered.values())).rows == (("A", 2), ("B", 10))


@pytest.mark.parametrize(("rows", "expected"), [
    ([["A", 0], ["A", 0]], 0),
    ([["A", -3], ["A", 1]], -2),
    ([["A", 0.25], ["A", 0.5]], 0.75),
    ([["A", 0.1], ["A", 0.2]], 0.3),  # exact decimal sum, not 0.30000000000000004
    ([["A", 9007199254740990], ["A", 1]], 9007199254740991),
])
def test_zero_negative_fractional_and_large_safe_totals(rows, expected):
    total = _group(rows).rows[0][1]
    assert total == expected and type(total) is type(expected)
    # Typed serialization keeps the number, not a string.
    assert canonical(_group(rows).rows) == canonical([["A", expected]])


@pytest.mark.parametrize("rows", [
    [["A", 9007199254740992], ["A", 1]],   # beyond the exact JSON integer range
    [["A", "0.12345678901234567891"]],     # more digits than a float keeps
])
def test_totals_without_an_exact_json_number_fail_loudly(rows):
    with pytest.raises(ValueError, match="exactly as a JSON number"):
        _group(rows)
