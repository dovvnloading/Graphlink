"""Workflow contracts are isolated, immutable and have no implicit conversions."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, asdict, replace
from itertools import product
from typing import get_args

import pytest

from backend.workflow.adapter import StepAdapter, StepContext
from backend.workflow.contracts import (
    AdapterDescriptor, AssetRef, FileValue, ImageValue, InputBinding, JsonValue, PortSet, PortSpec,
    SideEffectClass, StepOutputSource, TableValue, TextValue, WorkflowEventRecord, WorkflowInputSource,
    WorkflowOutput, WorkflowRunRecord, WorkflowSpec, WorkflowStepRecord, WorkflowStepSpec, freeze_config,
)
from backend.workflow.ports import ALLOWED_COERCIONS, PORT_KINDS, PortCoercionError, coerce_port

ASSET = AssetRef("asset", "a" * 64, "image/png", "pic.png", 5)
VALUES = {
    "text": TextValue("hello"),
    "json": JsonValue({"b": [1, None], "a": "é"}),
    "table": TableValue(("name", "value"), (("a,b", 2), ("a\"b", None), ("line\nbreak", True))),
    "file": FileValue(ASSET),
    "image": ImageValue(ASSET),
}


@pytest.mark.parametrize("source,target", list(product(VALUES, repeat=2)))
def test_complete_coercion_matrix(source, target):
    value = VALUES[source]
    port = PortSpec("destination", target, True)
    if source == target:
        assert coerce_port(value, port) is value
    elif (source, target) in {("json", "text"), ("table", "text"), ("table", "json")}:
        assert coerce_port(value, port).kind == target
    else:
        with pytest.raises(PortCoercionError, match=f"destination.*{source} to {target}.*no declared coercion"):
            coerce_port(value, port)


def test_exact_allowlist_and_class_names():
    assert PORT_KINDS == ("text", "json", "table", "file", "image")
    assert ALLOWED_COERCIONS == (("json", "text"), ("table", "text"), ("table", "json"))
    assert get_args(SideEffectClass.__value__) == ("pure", "local-read", "cloud", "executes", "writes")


def test_json_pretty_print_and_table_formats():
    port = PortSpec("input", "text", True)
    assert coerce_port(VALUES["json"], port) == TextValue('{\n  "a": "é",\n  "b": [\n    1,\n    null\n  ]\n}')
    assert coerce_port(VALUES["table"], port) == TextValue(
        'name,value\r\n"a,b",2\r\n"a""b",\r\n"line\nbreak",true\r\n'
    )
    assert coerce_port(VALUES["table"], replace(port, kind="json")) == JsonValue([
        {"name": "a,b", "value": 2}, {"name": 'a"b', "value": None}, {"name": "line\nbreak", "value": True},
    ])
    assert coerce_port(TableValue(("name",), ()), replace(port, kind="json")) == JsonValue([])
    assert coerce_port(TableValue(("name",), ()), port) == TextValue("name\r\n")
    for scalar in (None, True, 3, "hello", ["a", 1]):
        assert json.loads(coerce_port(JsonValue(scalar), port).text) == scalar


@pytest.mark.parametrize("bad,reason", [
    (TableValue(("a", "a"), ()), "unique"),
    (TableValue(("a",), ((1, 2),)), "row width"),
    (TableValue(("a",), ((float("nan"),),)), "finite"),
    (TableValue(("a",), (([],),)), "JSON scalars"),
    (TableValue((1,), ()), "strings"),
])
@pytest.mark.parametrize("target", ["json", "text"])
def test_malformed_table_errors_name_the_port(bad, reason, target):
    with pytest.raises(PortCoercionError, match=f"my_port.*{reason}"):
        coerce_port(bad, PortSpec("my_port", target, True))


def test_unknown_kinds_and_nonfinite_json_fail_closed():
    with pytest.raises(PortCoercionError, match="my_port.*unknown source"):
        coerce_port(object(), PortSpec("my_port", "text", True))
    with pytest.raises(PortCoercionError, match="my_port.*unknown destination"):
        coerce_port(TextValue("hello"), PortSpec("my_port", "other", True))
    with pytest.raises(PortCoercionError, match="my_port.*cannot coerce json to text"):
        coerce_port(JsonValue(float("inf")), PortSpec("my_port", "text", True))


def test_frozen_spec_and_records_roundtrip_to_json():
    config = {"b": [1], "a": "é"}
    frozen = freeze_config(config)
    config["b"].append(2)
    assert frozen == '{"a":"é","b":[1]}'
    assert frozen == freeze_config({"a": "é", "b": [1]})
    with pytest.raises(ValueError):
        freeze_config({"bad": float("inf")})

    port = PortSpec("text", "text", True)
    adapter = AdapterDescriptor("copy", "1", PortSet((port,), (port,)), "pure")
    step = WorkflowStepSpec("s1", adapter, frozen, (InputBinding("text", WorkflowInputSource("text")),))
    spec = WorkflowSpec(1, "w1", "r1", "Copy", (port,), (step,), (
        WorkflowOutput(port, StepOutputSource("s1", "text")),
    ))
    with pytest.raises(FrozenInstanceError):
        spec.name = "changed"
    with pytest.raises(FrozenInstanceError):
        spec.steps[0].adapter.ports.inputs[0].name = "changed"

    run = WorkflowRunRecord(1, "run", spec, "hash", "pending", "2026-01-01T00:00:00Z",
                            None, None, {"text": VALUES["text"]}, {}, None)
    attempt = WorkflowStepRecord(1, "run", "s1", 1, "pure", "completed", "hash",
                                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z", {"text": VALUES["text"]}, None)
    event = WorkflowEventRecord(1, "run", 1, "2026-01-01T00:00:00Z",
                                "step-completed", "s1", 1, "done", {"ok": True})
    wire = [json.loads(json.dumps(asdict(record))) for record in (run, attempt, event)]
    assert wire[0]["spec"]["steps"][0]["config_json"] == frozen
    assert wire[0]["started_at"] is None
    assert wire[1]["outputs"]["text"] == {"kind": "text", "text": "hello"}
    assert wire[2]["data"] == {"ok": True}


def test_adapter_is_a_structural_contract_not_a_runner():
    assert StepContext("run", "s", 2).attempt == 2
    assert all(hasattr(StepAdapter, name) for name in (
        "side_effect_class", "ports", "prepare", "fingerprint", "run", "cancel",
    ))
