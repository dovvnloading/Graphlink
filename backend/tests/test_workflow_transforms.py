"""Built-in transforms are typed, deterministic and previewable in isolation."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from backend.workflow.adapter import StepContext
from backend.workflow.contracts import JsonValue, PortSet, PortSpec, TableValue, TextValue, WorkflowStepSpec, freeze_config
from backend.workflow.registry import StepAdapterRegistry, builtin_registry
from backend.workflow.transforms import TransformAdapter

CONTEXT = StepContext("run", "step", 1)
TABLE = TableValue(("name", "score", "flag"), (
    ("a", 2, True), ("b", 10, False), ("c", 2, False), ("d", None, True),
))


def prepare(operation, config, inputs):
    registry = builtin_registry()
    adapter_id = f"transform.{operation}"
    config_json = freeze_config(config)
    descriptor = registry.describe(adapter_id, "1", config_json)
    adapter = registry.resolve(adapter_id, "1")
    spec = WorkflowStepSpec("step", descriptor, config_json, ())
    return adapter, adapter.prepare(spec, inputs)


def execute(operation, config, inputs):
    adapter, prepared = prepare(operation, config, inputs)
    return asyncio.run(adapter.run(prepared, CONTEXT))


@pytest.mark.parametrize("operation,config,inputs,expected,preview", [
    ("template", {"template": "{name}: {value} ({name}) {{literal}}"},
     {"name": TextValue("é"), "value": TextValue("42")},
     {"text": TextValue("é: 42 (é) {literal}")}, {"text": {"kind": "text", "characters": 19}}),
    ("json_path", {"path": '$.users[0]["a.b"]'}, {"json": JsonValue({"users": [{"a.b": [1, 2]}]})},
     {"value": JsonValue([1, 2])}, {"value": {"kind": "json", "type": "array", "length": 2}}),
    ("text_split", {"separator": ","}, {"text": TextValue("a,,b,")},
     {"parts": JsonValue(["a", "", "b", ""])}, {"parts": {"kind": "json", "type": "array", "length": 4}}),
    ("text_join", {"separator": "/"}, {"parts": JsonValue(["a", "", "b"])},
     {"text": TextValue("a//b")}, {"text": {"kind": "text", "characters": 4}}),
    ("table_filter", {"column": "score", "operator": "gte", "value": 2}, {"table": TABLE},
     {"table": TableValue(TABLE.columns, TABLE.rows[:3])},
     {"table": {"kind": "table", "columns": list(TABLE.columns), "row_count": 3}}),
    ("table_sort", {"column": "score", "direction": "desc"}, {"table": TABLE},
     {"table": TableValue(TABLE.columns, (TABLE.rows[1], TABLE.rows[0], TABLE.rows[2], TABLE.rows[3]))},
     {"table": {"kind": "table", "columns": list(TABLE.columns), "row_count": 4}}),
    ("table_select", {"columns": ["score", "name"]}, {"table": TABLE},
     {"table": TableValue(("score", "name"), ((2, "a"), (10, "b"), (2, "c"), (None, "d")))},
     {"table": {"kind": "table", "columns": ["score", "name"], "row_count": 4}}),
    ("table_rename", {"renames": {"score": "points"}}, {"table": TABLE},
     {"table": TableValue(("name", "points", "flag"), TABLE.rows)},
     {"table": {"kind": "table", "columns": ["name", "points", "flag"], "row_count": 4}}),
])
def test_registered_transforms_run_and_preview(operation, config, inputs, expected, preview, monkeypatch):
    adapter, prepared = prepare(operation, config, inputs)
    ports = adapter.ports(freeze_config(config))
    assert adapter.side_effect_class == "pure"
    assert {p.name: p.kind for p in ports.inputs} == {name: value.kind for name, value in inputs.items()}
    assert {p.name: p.kind for p in ports.outputs} == {name: value.kind for name, value in expected.items()}
    assert all(p.required for p in (*ports.inputs, *ports.outputs))
    assert asyncio.run(adapter.run(prepared, CONTEXT)) == expected
    assert asyncio.run(adapter.run(prepared, replace(CONTEXT, attempt=2))) == expected
    assert asyncio.run(adapter.cancel(CONTEXT)) is None

    async def forbidden_run(*args):
        pytest.fail("preview called run")

    monkeypatch.setattr(TransformAdapter, "run", forbidden_run)
    assert adapter.preview(prepared) == preview


def test_registry_pins_versions_and_is_fresh():
    registry = builtin_registry()
    assert tuple(key for key in registry.keys() if key[0].startswith("transform.")) == tuple(sorted((f"transform.{op}", "1") for op in (
        "template", "json_path", "text_split", "text_join", "table_filter",
        "table_sort", "table_select", "table_rename",
    )))
    adapter = registry.resolve("transform.template", "1")
    with pytest.raises(KeyError, match="unknown adapter"):
        registry.resolve("transform.template", "2")
    with pytest.raises(KeyError, match="unknown adapter"):
        registry.resolve("missing", "1")
    with pytest.raises(ValueError, match="already registered"):
        registry.register("transform.template", "1", adapter)
    with pytest.raises(ValueError, match="nonempty"):
        registry.register("", "1", adapter)
    registry.register("custom", "1", adapter)
    assert ("custom", "1") not in builtin_registry().keys()
    assert StepAdapterRegistry().keys() == ()
    with pytest.raises(ValueError, match="unknown transform"):
        TransformAdapter("not-supported")


def test_template_ports_first_occurrence_and_literal():
    adapter = TransformAdapter("template")
    assert adapter.ports(freeze_config({"template": "{b} {a} {b} {{c}}"})) == PortSet(
        (PortSpec("b", "text", True), PortSpec("a", "text", True)), (PortSpec("text", "text", True),)
    )
    assert execute("template", {"template": "{{hello}}"}, {}) == {"text": TextValue("{hello}")}
    assert execute("template", {"template": "{x}"}, {"x": TextValue("{other}")}) == {"text": TextValue("{other}")}


@pytest.mark.parametrize("template", ["{}", "{0}", "{x.__class__}", "{x[0]}", "{x!r}", "{x:>5}", "{"])
def test_invalid_template(template):
    with pytest.raises(ValueError):
        TransformAdapter("template").ports(freeze_config({"template": template}))


@pytest.mark.parametrize("path", ["name", "$..name", "$[*]", "$[-1]", "$[0:1]", '$["name"', "$[?(@.x)]"])
def test_invalid_json_path(path):
    with pytest.raises(ValueError):
        TransformAdapter("json_path").ports(freeze_config({"path": path}))


@pytest.mark.parametrize("value,shape", [
    (None, {"type": "null"}), (True, {"type": "boolean"}), (1.5, {"type": "number"}),
    ("secret", {"type": "string"}), ({'z': 1, 'a': 2}, {"type": "object", "keys": ["a", "z"]}),
    ([], {"type": "array", "length": 0}),
])
def test_json_root_and_scalar_previews(value, shape):
    adapter, prepared = prepare("json_path", {"path": "$"}, {"json": JsonValue(value)})
    assert asyncio.run(adapter.run(prepared, CONTEXT)) == {"value": JsonValue(value)}
    assert adapter.preview(prepared) == {"value": {"kind": "json", **shape}}


def test_fingerprint_is_content_based_and_snapshot_detaches_mutable_values():
    original = {"b": [1, 2], "a": "x"}
    adapter, prepared = prepare("json_path", {"path": "$"}, {"json": JsonValue(original)})
    _, reordered = prepare("json_path", {"path": "$"}, {"json": JsonValue({"a": "x", "b": [1, 2]})})
    fingerprint = adapter.fingerprint(prepared)
    assert len(fingerprint) == 64
    assert fingerprint == adapter.fingerprint(reordered)
    original["b"].append(3)
    _, changed = prepare("json_path", {"path": "$"}, {"json": JsonValue(original)})
    _, config_changed = prepare("json_path", {"path": "$.a"}, {"json": JsonValue(original)})
    assert fingerprint != adapter.fingerprint(changed)
    assert adapter.fingerprint(changed) != adapter.fingerprint(config_changed)
    result = asyncio.run(adapter.run(prepared, CONTEXT))
    result["value"].value["b"].append(99)
    assert asyncio.run(adapter.run(prepared, CONTEXT)) == {"value": JsonValue({"a": "x", "b": [1, 2]})}
    assert fingerprint == adapter.fingerprint(prepared)
    with pytest.raises(ValueError, match="different transform"):
        TransformAdapter("text_join").fingerprint(prepared)


def test_fingerprint_ignores_step_identity_and_config_whitespace():
    registry = builtin_registry()
    adapter = registry.resolve("transform.text_join", "1")
    config = '{"separator":"-"}'
    spec = WorkflowStepSpec("a", registry.describe(adapter.adapter_id, "1", config), config, ())
    inputs = {"parts": JsonValue(["a", "b"])}
    first = adapter.prepare(spec, inputs)
    second = adapter.prepare(replace(spec, step_id="b", config_json=' { "separator": "-" } '), inputs)
    assert adapter.fingerprint(first) == adapter.fingerprint(second)
    with pytest.raises(ValueError, match="descriptor"):
        adapter.prepare(replace(spec, adapter=replace(spec.adapter, adapter_version="2")), inputs)


@pytest.mark.parametrize("operation,config,inputs,reason", [
    ("template", {"template": "{name}"}, {}, "expected input ports"),
    ("template", {"template": "{name}"}, {"name": TextValue("a"), "extra": TextValue("b")}, "expected input ports"),
    ("template", {"template": "{name}"}, {"name": JsonValue("a")}, "requires text"),
    ("template", {"template": 3}, {}, "must be a string"),
    ("text_split", {}, {}, "exactly"),
    ("text_split", {"separator": "", "extra": 1}, {}, "exactly"),
    ("text_split", {"separator": ""}, {"text": TextValue("a")}, "must not be empty"),
    ("json_path", {"path": "$.missing"}, {"json": JsonValue({"x": None})}, "does not exist"),
    ("json_path", {"path": "$[2]"}, {"json": JsonValue([1])}, "does not exist"),
    ("json_path", {"path": "$[0]"}, {"json": JsonValue({"0": 1})}, "does not exist"),
    ("json_path", {"path": "$"}, {"json": JsonValue(float("nan"))}, "JSON input"),
    ("json_path", {"path": "$"}, {"json": JsonValue({1: "bad"})}, "JSON input"),
    ("text_join", {"separator": ","}, {"parts": JsonValue(["x", 1])}, "array of strings"),
    ("text_join", {"separator": ","}, {"parts": JsonValue("x")}, "array of strings"),
    ("table_filter", {"column": "name", "operator": "eval", "value": "x"}, {"table": TABLE}, "operator"),
    ("table_filter", {"column": "name", "operator": "eq", "value": []}, {"table": TABLE}, "scalar"),
    ("table_filter", {"column": "name", "operator": "contains", "value": 1}, {"table": TABLE}, "string"),
    ("table_sort", {"column": "name", "direction": "up"}, {"table": TABLE}, "direction"),
    ("table_sort", {"column": "missing", "direction": "asc"}, {"table": TABLE}, "unknown table column"),
    ("table_select", {"columns": "name"}, {"table": TABLE}, "array of strings"),
    ("table_select", {"columns": ["name", "name"]}, {"table": TABLE}, "unique"),
    ("table_select", {"columns": ["missing"]}, {"table": TABLE}, "unknown table column"),
    ("table_rename", {"renames": {"name": 1}}, {"table": TABLE}, "mapping"),
    ("table_rename", {"renames": {"name": "score"}}, {"table": TABLE}, "duplicate"),
    ("table_rename", {"renames": {"missing": "new"}}, {"table": TABLE}, "unknown table column"),
    ("table_select", {"columns": ["name"]}, {"table": TableValue(("name",), ((1, 2),))}, "row width"),
    ("table_select", {"columns": ["name"]}, {"table": TableValue(("name", "name"), ())}, "unique"),
])
def test_invalid_config_or_input_fails_at_prepare(operation, config, inputs, reason):
    with pytest.raises(ValueError, match=reason):
        prepare(operation, config, inputs)


@pytest.mark.parametrize("op,value,rows", [
    ("eq", 1, (("number", 1),)),
    ("ne", 1, (("bool", True), ("null", None), ("text", "1"), ("two", 2))),
    ("lt", 2, (("number", 1),)),
    ("lte", 1, (("number", 1),)),
    ("gt", 1, (("two", 2),)),
    ("contains", "1", (("text", "1"),)),
    ("eq", None, (("null", None),)),
])
def test_filter_type_aware(op, value, rows):
    table = TableValue(("name", "v"), (("bool", True), ("null", None), ("text", "1"), ("number", 1), ("two", 2)))
    assert execute("table_filter", {"column": "v", "operator": op, "value": value}, {"table": table}) == {
        "table": TableValue(table.columns, rows)
    }


def test_table_mixed_sort_stable_null_last_and_column_edges():
    table = TableValue(("id", "value"), (("a", "2"), ("b", 10), ("c", None), ("d", 2),
                                        ("e", False), ("f", 2.0), ("g", True)))
    for direction, order in [("asc", (4, 6, 3, 5, 1, 0, 2)), ("desc", (0, 1, 3, 5, 6, 4, 2))]:
        assert execute("table_sort", {"column": "value", "direction": direction}, {"table": table}) == {
            "table": TableValue(table.columns, tuple(table.rows[i] for i in order))
        }
    assert execute("table_rename", {"renames": {"id": "value", "value": "id"}}, {"table": table}) == {
        "table": TableValue(("value", "id"), table.rows)
    }
    assert execute("table_select", {"columns": []}, {"table": table}) == {
        "table": TableValue((), ((),) * len(table.rows))
    }
    assert execute("table_select", {"columns": ["id"]}, {"table": TableValue(table.columns, ())}) == {
        "table": TableValue(("id",), ())
    }


def test_empty_text_and_parts():
    assert execute("text_split", {"separator": ","}, {"text": TextValue("")}) == {"parts": JsonValue([""])}
    assert execute("text_join", {"separator": ""}, {"parts": JsonValue([])}) == {"text": TextValue("")}


def test_typed_pipeline_split_then_join():
    registry = builtin_registry()
    split = registry.resolve("transform.text_split", "1")
    join = registry.resolve("transform.text_join", "1")
    split_config = freeze_config({"separator": ","})
    join_config = freeze_config({"separator": " | "})
    split_spec = WorkflowStepSpec("split", registry.describe(split.adapter_id, "1", split_config), split_config, ())
    join_spec = WorkflowStepSpec("join", registry.describe(join.adapter_id, "1", join_config), join_config, ())
    first = asyncio.run(split.run(split.prepare(split_spec, {"text": TextValue("a,b")}), CONTEXT))
    second = join.prepare(join_spec, first)
    assert join.preview(second) == {"text": {"kind": "text", "characters": 5}}
    assert asyncio.run(join.run(second, CONTEXT)) == {"text": TextValue("a | b")}
