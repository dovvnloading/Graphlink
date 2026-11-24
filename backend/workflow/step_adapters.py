"""Typed workflow adapters. All preflight operations are effect-free.

A registry may be constructed without a run host for discovery/preview. The
host must explicitly bind an admitted workflow run before effectful execution.
The namespaced IDs deliberately do not shadow legacy Graph Runner kinds.
"""
from __future__ import annotations

import hashlib
import json
import keyword
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from string import Formatter
from typing import Any

from graphlink_cloud_providers import CLOUD_PROVIDERS
from graphlink_task_config import LOCAL_PROVIDER_LLAMACPP, LOCAL_PROVIDER_OLLAMA

from .adapter import StepContext
from .contracts import (
    AdapterDescriptor, AssetRef, FileValue, ImageValue, JsonValue, PortSet,
    PortSpec, PortValue, TableValue, TextValue, WorkflowStepSpec, freeze_config,
)
from .ports import _check_table, _table_csv, PORT_KINDS
from .transforms import _template_fields, _validate_json

MODEL_ID = "model.generate"
MAX_MODEL_STEPS = 64
LOCAL_PROVIDERS = frozenset((LOCAL_PROVIDER_OLLAMA, LOCAL_PROVIDER_LLAMACPP))
STEP_CLASSES = {
    MODEL_ID: "cloud",  # conservative: a model pin may disclose data
    "python.execute": "executes",
    "knowledge.search": "local-read",
    "web.research": "cloud",
    "chart.generate": "pure",
    "report.generate": "pure",
    "image.generate": "cloud",
    "agent.run": "executes",
}
_VALUE_TYPES = {"text": TextValue, "json": JsonValue, "table": TableValue,
                "file": FileValue, "image": ImageValue}
_DIGEST = re.compile(r"[0-9a-f]{64}")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest()


def _string(value, label, *, limit=100_000, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f"{label} must be {'a' if empty else 'a nonempty'} string of at most {limit} characters")
    return value


def _integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}")
    return value


def _schema(schema):
    """Validate the supported structured-output subset, rejecting ignored keywords."""
    if not isinstance(schema, dict) or set(schema) - {
        "type", "properties", "required", "items", "enum", "additionalProperties",
    }:
        raise ValueError("Use a JSON schema with type, properties, required, items, enum or additionalProperties")
    kind = schema.get("type")
    if kind not in ("object", "array", "string", "number", "integer", "boolean", "null"):
        raise ValueError("JSON schema needs an explicit supported type")
    if "enum" in schema:
        if not isinstance(schema["enum"], list) or not schema["enum"]:
            raise ValueError("JSON schema enum must be a nonempty array")
        _validate_json(schema["enum"])
    if set(schema) & {"properties", "required", "additionalProperties"}:
        if kind != "object":
            raise ValueError("Object schema keywords require object type")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if (not isinstance(properties, dict) or not isinstance(required, list)
                or any(not isinstance(key, str) or key not in properties for key in required)
                or type(schema.get("additionalProperties", True)) is not bool):
            raise ValueError("Invalid object schema")
        for child in properties.values():
            _schema(child)
    if "items" in schema:
        if kind != "array":
            raise ValueError("Items requires array type")
        _schema(schema["items"])


def check_json_result(value, schema):
    """Check actual output, never treat an invalid reply as a successful step."""
    _validate_json(value)
    from backend.structured_output import _validate_against_schema

    if _validate_against_schema(value, schema):
        raise ValueError("Model output does not match the configured JSON schema")
    if "enum" in schema and not any(digest(value) == digest(item) for item in schema["enum"]):
        raise ValueError("Value does not match JSON schema enum")
    if schema["type"] == "null" and value is not None:
        raise ValueError("Expected JSON null")
    if isinstance(value, dict) and schema["type"] == "object":
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError("Unexpected JSON object property")
        for key in properties.keys() & value.keys():
            check_json_result(value[key], properties[key])
    if isinstance(value, list) and schema["type"] == "array" and "items" in schema:
        for item in value:
            check_json_result(item, schema["items"])


def _config(adapter_id, config_json):
    cfg = json.loads(config_json)
    if not isinstance(cfg, dict):
        raise ValueError("Step configuration must be a JSON object")
    _validate_json(cfg)
    allowed = {
        MODEL_ID: ({"provider", "model_id", "system_prompt", "template", "output_kind"}, {"schema", "estimated_output_tokens"}),
        "python.execute": ({"code", "inputs", "output_kind"}, {"timeout_seconds"}),
        "knowledge.search": (set(), {"k"}),
        "web.research": (set(), {"max_sources"}),
        "chart.generate": ({"label_column", "value_column"}, set()),
        "report.generate": ({"label_column", "value_column"}, set()),
        "image.generate": ({"model_id"}, set()),
        "agent.run": ({"folder"}, set()),
    }
    required, optional = allowed[adapter_id]
    if not required <= set(cfg) <= required | optional:
        raise ValueError(f"Invalid configuration keys for {adapter_id}")
    if adapter_id == MODEL_ID:
        if (not isinstance(cfg["provider"], str) or
                (cfg["provider"] not in LOCAL_PROVIDERS and cfg["provider"] not in CLOUD_PROVIDERS)):
            raise ValueError("Select a configured provider")
        _string(cfg["model_id"], "Model ID", limit=192)
        _string(cfg["system_prompt"], "System prompt", empty=True)
        _template_fields(_string(cfg["template"], "Prompt template"))
        if cfg["output_kind"] not in ("text", "json"):
            raise ValueError("Model output must be text or JSON")
        if cfg["output_kind"] == "json":
            _schema(cfg.get("schema"))
        elif "schema" in cfg:
            raise ValueError("A schema requires JSON output")
        cfg.setdefault("estimated_output_tokens", 1024)
        _integer(cfg["estimated_output_tokens"], "Estimated output tokens", 1, 65536)
    elif adapter_id == "python.execute":
        _string(cfg["code"], "Python code", limit=80_000)
        inputs = cfg["inputs"]
        if not isinstance(inputs, dict) or len(inputs) > 64:
            raise ValueError("Python inputs must map at most 64 variable names to port kinds")
        for name, kind in inputs.items():
            if (not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_")
                    or name in ("result", "print", "open", "exec", "globals")
                    or kind not in PORT_KINDS):
                raise ValueError("Invalid Python input variable or port kind")
        if cfg["output_kind"] not in ("text", "json", "table", "file"):
            raise ValueError("Python output must be text, JSON, table or file")
        cfg.setdefault("timeout_seconds", 60)
        _integer(cfg["timeout_seconds"], "Timeout seconds", 1, 240)
    elif adapter_id == "knowledge.search":
        cfg.setdefault("k", 5)
        _integer(cfg["k"], "Passage count", 1, 50)
    elif adapter_id == "web.research":
        cfg.setdefault("max_sources", 4)
        _integer(cfg["max_sources"], "Source count", 1, 8)
    elif adapter_id in ("chart.generate", "report.generate"):
        for name in ("label_column", "value_column"):
            _string(cfg[name], name, limit=160)
        if cfg["label_column"] == cfg["value_column"]:
            raise ValueError("Choose different label and value columns")
    elif adapter_id == "image.generate":
        from backend.fal_integration import model_path
        model_path(_string(cfg["model_id"], "Fal model ID", limit=192))
    elif adapter_id == "agent.run":
        _string(cfg["folder"], "Workspace folder", limit=4096)
    return cfg


def _port(name, kind, required=True):
    return PortSpec(name, kind, required)


def _ports(adapter_id, cfg):
    if adapter_id == MODEL_ID:
        return PortSet(tuple(_port(name, "text") for name in _template_fields(cfg["template"])),
                       (_port(cfg["output_kind"], cfg["output_kind"]),))
    if adapter_id == "python.execute":
        return PortSet(tuple(_port(name, kind) for name, kind in sorted(cfg["inputs"].items())),
                       (_port(cfg["output_kind"], cfg["output_kind"]),))
    if adapter_id == "knowledge.search":
        return PortSet((_port("query", "text"),), (_port("passages", "json"), _port("text", "text")))
    if adapter_id == "web.research":
        return PortSet((_port("query", "text"),), (_port("text", "text"), _port("sources", "json")))
    if adapter_id == "chart.generate":
        return PortSet((_port("table", "table"), _port("text", "text", False)), (_port("chart", "json"),))
    if adapter_id == "report.generate":
        return PortSet((_port("table", "table"), _port("text", "text", False)), (_port("report", "text"),))
    if adapter_id == "image.generate":
        return PortSet((_port("prompt", "text"),), (_port("image", "image"),))
    return PortSet((_port("task", "text"),), (_port("text", "text"),))


def encode_value(value):
    if not isinstance(value, tuple(_VALUE_TYPES.values())):
        raise ValueError("Invalid typed port value")
    if isinstance(value, TextValue):
        _string(value.text, "Text", limit=2_097_152, empty=True)
    elif isinstance(value, JsonValue):
        _validate_json(value.value)
    elif isinstance(value, TableValue):
        _check_table(value)
    else:
        asset = value.asset
        if (not isinstance(asset, AssetRef) or not isinstance(asset.sha256, str)
                or not _DIGEST.fullmatch(asset.sha256) or asset.asset_id != asset.sha256
                or type(asset.size_bytes) is not int or not 0 <= asset.size_bytes <= 20 * 1024 * 1024
                or not isinstance(asset.filename, str) or not isinstance(asset.media_type, str)
                or (isinstance(value, ImageValue) and not asset.media_type.startswith("image/"))):
            raise ValueError("Invalid content-addressed asset")
    return asdict(value)


def decode_value(raw):
    kind = raw["kind"]
    if kind == "table":
        return TableValue(tuple(raw["columns"]), tuple(tuple(row) for row in raw["rows"]))
    if kind in ("file", "image"):
        return _VALUE_TYPES[kind](AssetRef(**raw["asset"]))
    return _VALUE_TYPES[kind](raw["text"] if kind == "text" else raw["value"])


def validate_values(ports, values):
    names = {port.name for port in ports}
    if not {port.name for port in ports if port.required} <= set(values) <= names:
        raise ValueError("Missing required port or unexpected port")
    payload = {}
    for port in ports:
        if port.name not in values:
            continue
        value = values[port.name]
        if not isinstance(value, _VALUE_TYPES[port.kind]):
            raise ValueError(f"Port {port.name!r} requires {port.kind}")
        payload[port.name] = encode_value(value)
    return payload


@dataclass(frozen=True, slots=True)
class PreparedStep:
    spec: WorkflowStepSpec
    config_json: str
    inputs_json: str

    def config(self):
        return json.loads(self.config_json)

    def inputs(self):
        return {name: decode_value(value) for name, value in json.loads(self.inputs_json).items()}


def model_messages(cfg, inputs):
    prompt = "".join(literal + (inputs[name].text if name is not None else "")
                     for literal, name, _, _ in Formatter().parse(cfg["template"]))
    _string(prompt, "Rendered prompt", limit=262_144)
    system = cfg["system_prompt"]
    if cfg["output_kind"] == "json":
        system += "\nReturn only JSON matching this schema:\n" + freeze_config(cfg["schema"])
    return [{"role": "system", "content": system}, {"role": "user", "content": prompt}]


def _table_output(adapter_id, cfg, inputs):
    # Reuse the same validated group-sum projection as the canvas chart and
    # Drafter report; no model calls or mutation of a source table.
    from backend.domain.table_workflows import _generate, parse_table
    from backend.domain.node_states import ARTIFACT_MAX_CHARS
    from graphlink_chart_data import canonicalize_chart_data

    table = parse_table(_table_csv(inputs["table"]))
    is_chart = adapter_id == "chart.generate"
    result = _generate(table, "chart" if is_chart else "artifact",
                       cfg["label_column"], cfg["value_column"], 1).result
    text = inputs.get("text", TextValue("")).text
    if is_chart:
        if text:
            result = canonicalize_chart_data({**result, "title": text}, "bar")
        return {"chart": JsonValue(result)}
    report = (text + "\n\n" if text else "") + result
    if len(report) > ARTIFACT_MAX_CHARS:
        raise ValueError("Report exceeds the artifact document limit")
    return {"report": TextValue(report)}


@dataclass(frozen=True, slots=True)
class ServiceStepAdapter:
    adapter_id: str
    host: Any = None
    adapter_version: str = "1"

    @property
    def side_effect_class(self):
        return STEP_CLASSES[self.adapter_id]

    def ports(self, config_json):
        return _ports(self.adapter_id, _config(self.adapter_id, config_json))

    def prepare(self, spec, inputs: Mapping[str, PortValue]):
        cfg = _config(self.adapter_id, spec.config_json)
        ports = _ports(self.adapter_id, cfg)
        expected = AdapterDescriptor(self.adapter_id, self.adapter_version, ports, self.side_effect_class)
        if spec.adapter != expected:
            raise ValueError("Step adapter descriptor does not match the pinned adapter")
        prepared = PreparedStep(spec, freeze_config(cfg), freeze_config(validate_values(ports.inputs, inputs)))
        # Early semantic validation; previews must not probe providers or files.
        if self.adapter_id == MODEL_ID:
            model_messages(cfg, prepared.inputs())
        elif self.adapter_id in ("chart.generate", "report.generate"):
            _table_output(self.adapter_id, cfg, prepared.inputs())
        elif self.adapter_id in ("knowledge.search", "web.research", "image.generate", "agent.run"):
            port = ports.inputs[0].name
            _string(prepared.inputs()[port].text, port,
                    limit={"knowledge.search": 4000, "web.research": 1000, "image.generate": 10_000,
                           "agent.run": 20_000}[self.adapter_id])
        return prepared

    def fingerprint(self, prepared):
        self._check(prepared)
        return digest([self.adapter_id, self.adapter_version, prepared.config_json, prepared.inputs_json])

    def _check(self, prepared):
        if (prepared.spec.adapter.adapter_id, prepared.spec.adapter.adapter_version) != (
                self.adapter_id, self.adapter_version):
            raise ValueError("Prepared state belongs to a different adapter")

    def preview(self, prepared):
        self._check(prepared)
        cfg, inputs = prepared.config(), prepared.inputs()
        result = {port.name: {"kind": port.kind} for port in prepared.spec.adapter.ports.outputs}
        result["sideEffectClass"] = self.side_effect_class
        if self.adapter_id == MODEL_ID:
            from backend.token_counter import estimate_cost_usd, estimate_tokens
            count = sum(estimate_tokens(msg["content"]) for msg in model_messages(cfg, inputs))
            price = estimate_cost_usd(cfg["provider"], cfg["model_id"], count, cfg["estimated_output_tokens"],
                                      overrides=self.host.pricing if self.host else None)
            result["model"] = {"provider": cfg["provider"], "modelId": cfg["model_id"],
                               "cloudConsentRequired": cfg["provider"] not in LOCAL_PROVIDERS,
                               "estimatedInputTokens": count,
                               "estimatedOutputTokens": cfg["estimated_output_tokens"], "costUsd": price}
        elif self.adapter_id == "python.execute":
            result["execution"] = {"timeoutSeconds": cfg["timeout_seconds"], "approvalRequired": True}
        elif self.adapter_id == "knowledge.search":
            result["search"] = {"maxPassages": cfg["k"]}
        elif self.adapter_id == "web.research":
            result["research"] = {"maxSources": cfg["max_sources"], "domainApprovalRequired": True}
        elif self.adapter_id == "image.generate":
            result["generation"] = {"modelId": cfg["model_id"], "pricedApprovalRequired": True, "costUsd": None}
        elif self.adapter_id == "agent.run":
            result["agent"] = {"folder": cfg["folder"], "permissionMode": "ask"}
        else:
            from .transforms import _shape
            result.update({name: _shape(value) for name, value in _table_output(self.adapter_id, cfg, inputs).items()})
        return result

    async def run(self, prepared, context: StepContext):
        self._check(prepared)
        if context.step_id != prepared.spec.step_id:
            raise ValueError("Execution context does not match the prepared step")
        if self.side_effect_class == "pure":
            outputs = _table_output(self.adapter_id, prepared.config(), prepared.inputs())
        else:
            if self.host is None:
                raise RuntimeError("Effectful workflow execution requires an admitted run host")
            outputs = await self.host.run(self, prepared, context)
        validate_values(prepared.spec.adapter.ports.outputs, outputs)
        return outputs

    async def cancel(self, context: StepContext):
        if self.host is not None:
            await self.host.cancel(context)
