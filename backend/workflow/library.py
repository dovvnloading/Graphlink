"""Validated, portable workflow snapshots layered on the v1 contracts."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, replace
from typing import Any
from uuid import uuid4

from backend.secret_scrub import scrub, scrub_text

from .adapter import StepContext
from .contracts import (
    InputBinding, JsonValue, PortSet, PortSpec, StepOutputSource, TableValue,
    TextValue, WorkflowInputSource, WorkflowOutput, WorkflowSpec, WorkflowStepSpec,
    freeze_config,
)
from .ports import ALLOWED_COERCIONS, PORT_KINDS, coerce_port
from .registry import builtin_registry

FORMAT = "graphlink-workflow"
MAX_FILE = 1_000_000
MAX_STEPS = 200


def identifier() -> str:
    return uuid4().hex


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json(value: Any) -> Any:
    return json.loads(canonical(value))


def _keys(row: Any, names: str) -> None:
    if not isinstance(row, dict) or set(row) != set(names.split()):
        raise ValueError("Unexpected or missing workflow fields.")


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("Workflow names and identifiers must be nonempty and at most 200 characters.")
    return value


def _unique(values, label):
    values = list(values)
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}.")


def _safe(value: Any) -> None:
    # Config JSON must already be decoded before this check: otherwise
    # arbitrary credential-key values inside the JSON string escape scrub().
    if scrub(value) != value:
        raise ValueError("Remove credentials and private paths from the workflow.")
    if isinstance(value, dict):
        for key, item in value.items():
            if scrub_text(key) != key:
                raise ValueError("Remove credentials and private paths from workflow keys.")
            _safe(item)
    elif isinstance(value, list):
        for item in value:
            _safe(item)


class NoteTextAdapter:
    """Adapter for the existing canvas Copy text and Word count bindings."""
    side_effect_class = "pure"

    def ports(self, config_json):
        config = json.loads(config_json)
        _keys(config, "operation")
        if config["operation"] not in ("copy", "word_count"):
            raise ValueError("Unknown note text operation.")
        return PortSet((PortSpec("text", "text", True),), (PortSpec("text", "text", True),))

    def prepare(self, spec, inputs):
        self.ports(spec.config_json)
        if set(inputs) != {"text"} or not isinstance(inputs["text"], TextValue):
            raise ValueError("The note requires a text input.")
        return json.loads(spec.config_json)["operation"], inputs["text"].text

    def fingerprint(self, prepared):
        return hashlib.sha256(canonical(["note.text", "1", prepared]).encode()).hexdigest()

    def preview(self, prepared):
        operation, text = prepared
        return {"text": {"kind": "text", "characters": len(str(len(text.split())) if operation == "word_count" else text)}}

    async def run(self, prepared, context):
        operation, text = prepared
        return {"text": TextValue(str(len(text.split())) if operation == "word_count" else text)}

    async def cancel(self, context):
        return None


def library_registry():
    registry = builtin_registry()
    registry.register("note.text", "1", NoteTextAdapter())
    return registry


def _port(row):
    _keys(row, "name kind required")
    if row["kind"] not in PORT_KINDS or type(row["required"]) is not bool:
        raise ValueError("Invalid workflow port.")
    return PortSpec(_name(row["name"]), row["kind"], row["required"])


def _source(row):
    if isinstance(row, dict) and row.get("kind") == "input":
        _keys(row, "kind port")
        return WorkflowInputSource(_name(row["port"]))
    _keys(row, "kind step_id port")
    if row["kind"] != "step":
        raise ValueError("Invalid workflow source.")
    return StepOutputSource(_name(row["step_id"]), _name(row["port"]))


def _list(value):
    if not isinstance(value, list):
        raise ValueError("Expected a workflow list.")
    return value


def _config(text):
    if not isinstance(text, str):
        raise ValueError("Expected configuration JSON.")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a configuration object.")
    _safe(value)
    return value


def decode_spec(row: Any) -> WorkflowSpec:
    """Decode untrusted wire data; resolve every descriptor against the registry."""
    _keys(row, "schema_version workflow_id revision name inputs steps outputs")
    if type(row["schema_version"]) is not int or row["schema_version"] != 1:
        raise ValueError("Unsupported workflow schema version.")
    registry = library_registry()
    steps = []
    for step in _list(row["steps"]):
        _keys(step, "step_id adapter config_json bindings")
        declared = step["adapter"]
        _keys(declared, "adapter_id adapter_version ports side_effect_class")
        config = freeze_config(_config(step["config_json"]))
        try:
            adapter = registry.describe(_name(declared["adapter_id"]), _name(declared["adapter_version"]), config)
        except KeyError:
            raise ValueError("A pinned workflow adapter is not installed.") from None
        if _json(asdict(adapter)) != declared or adapter.side_effect_class != "pure":
            raise ValueError("Workflow adapter interface or side-effect class does not match.")
        bindings = []
        for binding in _list(step["bindings"]):
            _keys(binding, "port source")
            bindings.append(InputBinding(_name(binding["port"]), _source(binding["source"])))
        steps.append(WorkflowStepSpec(_name(step["step_id"]), adapter, config, tuple(bindings)))
    outputs = []
    for output in _list(row["outputs"]):
        _keys(output, "port source")
        outputs.append(WorkflowOutput(_port(output["port"]), _source(output["source"])))
    spec = WorkflowSpec(
        1, _name(row["workflow_id"]), _name(row["revision"]), _name(row["name"]),
        tuple(_port(port) for port in _list(row["inputs"])), tuple(steps), tuple(outputs),
    )
    _safe(_json(asdict(spec)))
    ordered_steps(spec)
    return spec


def ordered_steps(spec: WorkflowSpec) -> tuple[WorkflowStepSpec, ...]:
    if not 1 <= len(spec.steps) <= MAX_STEPS or not spec.outputs:
        raise ValueError("A workflow needs 1–200 steps and at least one selected output.")
    _unique((port.name for port in spec.inputs), "input")
    _unique((step.step_id for step in spec.steps), "step")
    _unique((output.port.name for output in spec.outputs), "output")
    by_id = {step.step_id: step for step in spec.steps}
    input_ports = {port.name: port for port in spec.inputs}
    used_inputs = set()

    def check(source, target):
        if isinstance(source, WorkflowInputSource):
            actual = input_ports.get(source.port)
            used_inputs.add(source.port)
        else:
            parent = by_id.get(source.step_id)
            actual = next((port for port in parent.adapter.ports.outputs if port.name == source.port), None) if parent else None
        if actual is None:
            raise ValueError(f"Port {target.name}: source does not exist.")
        if actual.kind != target.kind and (actual.kind, target.kind) not in ALLOWED_COERCIONS:
            raise ValueError(f"Port {target.name}: incompatible {actual.kind} to {target.kind} connection.")
        if target.required and not actual.required:
            raise ValueError(f"Port {target.name}: required input cannot depend on an optional source.")

    parents = {}
    for step in spec.steps:
        ports = {port.name: port for port in step.adapter.ports.inputs}
        _unique((binding.port for binding in step.bindings), "step binding")
        bound = {binding.port for binding in step.bindings}
        if not bound <= ports.keys() or any(p.required and p.name not in bound for p in ports.values()):
            raise ValueError("Unknown or unbound required step input.")
        for binding in step.bindings:
            check(binding.source, ports[binding.port])
        parents[step.step_id] = {binding.source.step_id for binding in step.bindings if isinstance(binding.source, StepOutputSource)}
    for output in spec.outputs:
        check(output.source, output.port)
    if used_inputs != set(input_ports):
        raise ValueError("Unused workflow input.")
    result = []
    done = set()
    while len(done) < len(spec.steps):
        ready = [step for step in spec.steps if step.step_id not in done and parents[step.step_id] <= done]
        if not ready:
            raise ValueError("A dependency cycle prevents the workflow from running.")
        result.extend(ready)
        done.update(step.step_id for step in ready)
    return tuple(result)


def _parameter_value(parameter, value):
    kind = parameter["kind"]
    if kind == "text":
        ok = isinstance(value, str)
    elif kind == "number":
        ok = type(value) in (int, float) and (type(value) is int or math.isfinite(value))
    elif kind == "choice":
        ok = isinstance(value, str) and value in parameter["choices"]
    else:
        ok = False
    if not ok:
        raise ValueError("Invalid workflow parameter value.")
    _safe(value)


def validate_document(document: Any) -> dict:
    """Return a canonical by-value version, never a live scene reference."""
    if len(canonical(document).encode()) > MAX_FILE:
        raise ValueError("Workflow file exceeds 1 MB.")
    _keys(document, "format spec parameters")
    if document["format"] != FORMAT:
        raise ValueError("Not a Graphlink workflow file.")
    spec = decode_spec(document["spec"])
    parameters = _list(document["parameters"])
    by_id = {step.step_id: step for step in spec.steps}
    registry = library_registry()
    names, targets = [], []
    for parameter in parameters:
        _keys(parameter, "name step_id setting kind choices")
        names.append(_name(parameter["name"]))
        target = (_name(parameter["step_id"]), _name(parameter["setting"]))
        targets.append(target)
        step = by_id.get(target[0])
        if step is None:
            raise ValueError("Parameter refers to an unknown step.")
        config = _config(step.config_json)
        if target[1] not in config:
            raise ValueError("Parameter refers to an unknown setting.")
        choices = _list(parameter["choices"])
        if parameter["kind"] == "choice":
            if not choices or any(not isinstance(c, str) for c in choices):
                raise ValueError("A choice parameter needs text choices.")
            _unique(choices, "parameter choice")
        elif choices:
            raise ValueError("Only choice parameters may have choices.")
        _parameter_value(parameter, config[target[1]])
        # The exposed interface is frozen. A choice cannot alter its ports.
        for choice in choices:
            candidate = {**config, target[1]: choice}
            _safe(candidate)
            if registry.describe(step.adapter.adapter_id, step.adapter.adapter_version, freeze_config(candidate)) != step.adapter:
                raise ValueError("A parameter cannot change the frozen step interface.")
    _unique(names, "parameter name")
    _unique(targets, "parameter setting")
    _safe(parameters)
    return {"format": FORMAT, "spec": _json(asdict(spec)), "parameters": _json(parameters)}


def make_document(spec: WorkflowSpec, parameters: list[dict]) -> dict:
    return validate_document({"format": FORMAT, "spec": _json(asdict(spec)), "parameters": parameters})


def import_document(text: str) -> dict:
    if not isinstance(text, str) or len(text.encode()) > MAX_FILE:
        raise ValueError("Workflow file exceeds 1 MB.")

    def pairs(items):
        _unique((key for key, _ in items), "JSON key")
        return dict(items)

    return validate_document(json.loads(text, object_pairs_hook=pairs))


def export_document(document: dict) -> str:
    # Scrub parsed settings, not merely the opaque config_json strings.
    copy = _json(document)
    for step in copy["spec"]["steps"]:
        step["config_json"] = freeze_config(scrub(json.loads(step["config_json"])))
    copy = scrub(copy)
    # Refuse exports that would be invalid or still contain a secret.
    return canonical(validate_document(copy))


def configured_spec(document: dict, values: dict) -> WorkflowSpec:
    document = validate_document(document)
    spec = decode_spec(document["spec"])
    parameters = {parameter["name"]: parameter for parameter in document["parameters"]}
    if not isinstance(values, dict) or not values.keys() <= parameters.keys():
        raise ValueError("Unknown workflow parameter.")
    configs = {step.step_id: json.loads(step.config_json) for step in spec.steps}
    for name, value in values.items():
        parameter = parameters[name]
        _parameter_value(parameter, value)
        configs[parameter["step_id"]][parameter["setting"]] = value
    registry = library_registry()
    steps = []
    for step in spec.steps:
        config = configs[step.step_id]
        _safe(config)
        frozen = freeze_config(config)
        if registry.describe(step.adapter.adapter_id, step.adapter.adapter_version, frozen) != step.adapter:
            raise ValueError("A parameter cannot change the frozen step interface.")
        steps.append(replace(step, config_json=frozen))
    return replace(spec, steps=tuple(steps))


async def execute(spec: WorkflowSpec, inputs: dict, run_id: str, on_step=None) -> dict:
    """Pure adapter execution; the caller owns durable admission and receipts."""
    registry = library_registry()
    declared = {port.name: port for port in spec.inputs}
    if not isinstance(inputs, dict) or not inputs.keys() <= declared.keys():
        raise ValueError("Unknown workflow input.")
    if any(port.required and port.name not in inputs for port in spec.inputs):
        raise ValueError("Supply every required workflow input.")
    typed = {}
    for name, value in inputs.items():
        kind = declared[name].kind
        if kind == "text" and isinstance(value, str):
            typed[name] = TextValue(value)
        elif kind == "json":
            typed[name] = JsonValue(_json(value))
        elif kind == "table" and isinstance(value, dict) and set(value) == {"columns", "rows"}:
            typed[name] = TableValue(tuple(value["columns"]), tuple(tuple(row) for row in value["rows"]))
        else:
            raise ValueError(f"Unsupported value for input {name}.")
    results = {}

    def resolve(source):
        if isinstance(source, WorkflowInputSource):
            return typed.get(source.port)
        return results[source.step_id].get(source.port)

    for step in ordered_steps(spec):
        adapter = registry.resolve(step.adapter.adapter_id, step.adapter.adapter_version)
        ports = {port.name: port for port in step.adapter.ports.inputs}
        step_inputs = {}
        for binding in step.bindings:
            value = resolve(binding.source)
            if value is not None:
                step_inputs[binding.port] = coerce_port(value, ports[binding.port])
        context = StepContext(run_id, step.step_id, 1)
        if on_step:
            on_step(step.step_id, "running", None)
        try:
            prepared = adapter.prepare(step, step_inputs)
            outputs = dict(await adapter.run(prepared, context))
            declared_outputs = {port.name: port for port in step.adapter.ports.outputs}
            if not outputs.keys() <= declared_outputs.keys() or any(
                port.required and name not in outputs for name, port in declared_outputs.items()
            ) or any(value.kind != declared_outputs[name].kind for name, value in outputs.items()):
                raise ValueError("Adapter output does not match its frozen interface.")
            results[step.step_id] = outputs
        except Exception:
            if on_step:
                on_step(step.step_id, "failed", None)
            raise
        if on_step:
            on_step(step.step_id, "completed", adapter.fingerprint(prepared))
    return {
        output.port.name: _json(asdict(coerce_port(value, output.port)))
        for output in spec.outputs if (value := resolve(output.source)) is not None
    }
