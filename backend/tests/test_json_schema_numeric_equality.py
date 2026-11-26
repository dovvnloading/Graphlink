"""JSON Schema numeric semantics in the shared validator (#1093)."""

from __future__ import annotations

import pytest

from backend.structured_output import _validate_against_schema, json_values_equal
from backend.workflow.step_adapters import check_json_result


@pytest.mark.parametrize(("left", "right", "equal"), [
    (1, 1.0, True),
    (-0.0, 0, True),
    ({"x": [1, {"y": 2.0}]}, {"x": [1.0, {"y": 2}]}, True),
    ([1, 2], [2, 1], False),
    (True, 1, False),
    (False, 0, False),
    (True, True, True),
    (None, 0, False),
    ("1", 1, False),
    ({"x": 1}, {"x": 1, "y": 2}, False),
])
def test_json_values_equal(left, right, equal):
    assert json_values_equal(left, right) is equal
    assert json_values_equal(right, left) is equal


@pytest.mark.parametrize(("value", "valid"), [
    (1, True), (1.0, True), (-0.0, True), (1e3, True),
    (1.5, False), (True, False), ("1", False), (float("inf"), False), (float("nan"), False),
])
def test_integer_is_judged_by_value(value, valid):
    assert (not _validate_against_schema(value, {"type": "integer"})) is valid


def test_structured_output_and_workflow_enum_accept_equal_numbers_only():
    assert not _validate_against_schema(1.0, {"type": "number", "enum": [1]})
    assert _validate_against_schema(True, {"type": "boolean", "enum": [1]})
    check_json_result(1.0, {"type": "integer"})
    check_json_result({"x": 1.0}, {"type": "object", "enum": [{"x": 1}]})
    with pytest.raises(ValueError):
        check_json_result(True, {"type": "number", "enum": [1]})
    with pytest.raises(ValueError):
        check_json_result(2, {"type": "number", "enum": [1]})
