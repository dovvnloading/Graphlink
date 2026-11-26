"""Strict decoding of the existing workflow v1 JSON contracts at HTTP ingress."""
from __future__ import annotations

from typing import Any

from .contracts import (
    AdapterDescriptor, AssetRef, FileValue, ImageValue, InputBinding, JsonValue,
    PortSet, PortSpec, StepOutputSource, TextValue, WorkflowInputSource,
    WorkflowOutput, WorkflowSpec, WorkflowStepSpec,
)
from .ports import PORT_KINDS, table_from_json


def _object(value: Any, *fields: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("Invalid workflow record fields.")
    return value


def _array(value: Any) -> list:
    if not isinstance(value, list):
        raise ValueError("Expected a workflow array.")
    return value


def _string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected workflow text.")
    return value


def _port(value: Any) -> PortSpec:
    value = _object(value, "name", "kind", "required")
    if type(value["required"]) is not bool or value["kind"] not in PORT_KINDS:
        raise ValueError("Invalid workflow port declaration.")
    return PortSpec(_string(value["name"]), value["kind"], value["required"])


def _source(value: Any):
    if not isinstance(value, dict):
        raise ValueError("Invalid workflow source.")
    if value.get("kind") == "input":
        _object(value, "kind", "port")
        return WorkflowInputSource(_string(value["port"]))
    if value.get("kind") == "step":
        _object(value, "kind", "step_id", "port")
        return StepOutputSource(_string(value["step_id"]), _string(value["port"]))
    raise ValueError("Unknown workflow source kind.")


def _step(value: Any) -> WorkflowStepSpec:
    value = _object(value, "step_id", "adapter", "config_json", "bindings")
    adapter = _object(value["adapter"], "adapter_id", "adapter_version", "ports", "side_effect_class")
    ports = _object(adapter["ports"], "inputs", "outputs")
    descriptor = AdapterDescriptor(
        _string(adapter["adapter_id"]), _string(adapter["adapter_version"]),
        PortSet(tuple(map(_port, _array(ports["inputs"]))), tuple(map(_port, _array(ports["outputs"])))),
        _string(adapter["side_effect_class"]),
    )
    bindings = []
    for binding in _array(value["bindings"]):
        _object(binding, "port", "source")
        bindings.append(InputBinding(_string(binding["port"]), _source(binding["source"])))
    return WorkflowStepSpec(_string(value["step_id"]), descriptor, _string(value["config_json"]), tuple(bindings))


def decode_spec(value: Any) -> WorkflowSpec:
    value = _object(value, "schema_version", "workflow_id", "revision", "name", "inputs", "steps", "outputs")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported workflow schema version.")
    outputs = []
    for output in _array(value["outputs"]):
        _object(output, "port", "source")
        outputs.append(WorkflowOutput(_port(output["port"]), _source(output["source"])))
    return WorkflowSpec(
        1, _string(value["workflow_id"]), _string(value["revision"]), _string(value["name"]),
        tuple(map(_port, _array(value["inputs"]))),
        tuple(map(_step, _array(value["steps"]))), tuple(outputs),
    )


def decode_value(value: Any):
    if not isinstance(value, dict):
        raise ValueError("Invalid workflow parameter.")
    kind = value.get("kind")
    if kind == "text":
        _object(value, "kind", "text")
        return TextValue(_string(value["text"]))
    if kind == "json":
        _object(value, "kind", "value")
        return JsonValue(value["value"])
    if kind == "table":
        _object(value, "kind", "columns", "rows")
        return table_from_json(value["columns"], value["rows"])
    if kind in ("file", "image"):
        _object(value, "kind", "asset")
        asset = _object(value["asset"], "asset_id", "sha256", "media_type", "filename", "size_bytes")
        if type(asset["size_bytes"]) is not int or asset["size_bytes"] < 0:
            raise ValueError("Invalid workflow asset size.")
        ref = AssetRef(*(_string(asset[k]) for k in ("asset_id", "sha256", "media_type", "filename")),
                       asset["size_bytes"])
        return FileValue(ref) if kind == "file" else ImageValue(ref)
    raise ValueError("Unknown workflow parameter kind.")


def decode_request(body: dict[str, Any]):
    spec = decode_spec(body.get("spec"))
    parameters = body.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("Workflow parameters must be an object.")
    return spec, {name: decode_value(value) for name, value in parameters.items()}
