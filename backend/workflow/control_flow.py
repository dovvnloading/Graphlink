"""Deterministic typed control flow, with orchestration owned by WorkflowEngine."""
from __future__ import annotations

import json
import math
import operator
from dataclasses import asdict

import regex

from .artifacts import digest
from .contracts import JsonValue, PortSet, PortSpec, TableValue, TextValue
from .transforms import _extract, _path_tokens, _validate_json
from .wire import decode_spec

CONDITION = "control.condition"
MERGE = "control.merge"
APPROVAL = "control.approval"
ASSERT = "control.assert"
MAP = "control.map"
BRANCHES = {CONDITION: ("then", "else"), ASSERT: ("pass", "fail")}
MAX_MAP_ITEMS = 100
_COMPARE = {"eq": operator.eq, "ne": operator.ne, "lt": operator.lt,
            "lte": operator.le, "gt": operator.gt, "gte": operator.ge}


class ControlBlocked(Exception):
    """An intentionally visible, input-independent reason for blocking a run."""


def _number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _equal(left, right):
    if _number(left) and _number(right):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _data(value):
    if isinstance(value, TextValue):
        return value.text
    if isinstance(value, TableValue):
        return [dict(zip(value.columns, row, strict=True)) for row in value.rows]
    return value.value


def _bounds(cfg, *, count=False):
    low, high = cfg.get("min"), cfg.get("max")
    valid = (lambda v: type(v) is int and v >= 0) if count else _number
    if (low is None and high is None or low is not None and not valid(low)
            or high is not None and not valid(high)
            or low is not None and high is not None and low > high):
        raise ValueError("Specify valid inclusive min/max bounds.")


def _range(value, cfg):
    return (_number(value) and (cfg.get("min") is None or value >= cfg["min"])
            and (cfg.get("max") is None or value <= cfg["max"]))


def _validate_predicate(cfg, adapter_id):
    op = cfg.get("predicate")
    allowed = ({"equals", "contains", "regex", "numeric_compare", "empty", "json_path_truthy"}
               if adapter_id == CONDITION else
               {"contains", "regex", "json_schema", "numeric_range", "row_count"})
    if not isinstance(op, str) or op not in allowed:
        raise ValueError("Unknown control-flow predicate.")
    required = {"equals": {"value"}, "contains": {"value"}, "regex": {"pattern"},
                "numeric_compare": {"operator", "value"}, "empty": set(),
                "json_path_truthy": {"path"}, "json_schema": {"schema"},
                "numeric_range": set(), "row_count": set()}[op]
    optional = {"min", "max"} if op in ("numeric_range", "row_count") else set()
    common = {"kind", "predicate"}
    if adapter_id == ASSERT:
        common.add("on_failure")
        if cfg.get("on_failure", "block") not in ("block", "route"):
            raise ValueError("Assert on_failure must be block or route.")
    if not required <= cfg.keys() or cfg.keys() - common - required - optional:
        raise ValueError("Invalid control-flow predicate configuration.")
    if op == "regex":
        if not isinstance(cfg["pattern"], str) or len(cfg["pattern"]) > 4096:
            raise ValueError("Regex pattern must be text of at most 4096 characters.")
        try:
            regex.compile(cfg["pattern"])
        except regex.error:
            raise ValueError("Invalid regex pattern.") from None
    elif op == "numeric_compare":
        if cfg["operator"] not in _COMPARE or not _number(cfg["value"]):
            raise ValueError("Numeric comparison needs an operator and finite number.")
    elif op == "json_path_truthy":
        if cfg.get("kind", "json") != "json" or not isinstance(cfg["path"], str):
            raise ValueError("JSON path truthiness requires a JSON input and path.")
        _path_tokens(cfg["path"])
    elif op == "json_schema":
        from .step_adapters import _schema
        if cfg.get("kind", "json") != "json":
            raise ValueError("JSON schema requires a JSON input.")
        _schema(cfg["schema"])
    elif op in ("numeric_range", "row_count"):
        _bounds(cfg, count=op == "row_count")
    elif op in ("equals", "contains"):
        _validate_json(cfg["value"])


def _predicate(cfg, value):
    op = cfg["predicate"]
    data = _data(value)
    if op == "equals":
        return _equal(data, cfg["value"])
    if op == "contains":
        target = cfg["value"]
        if isinstance(data, str):
            return isinstance(target, str) and target in data
        if isinstance(data, list):
            return any(_equal(item, target) for item in data)
        return isinstance(data, dict) and isinstance(target, str) and target in data
    if op == "regex":
        if not isinstance(data, str):
            return False
        try:
            return regex.search(cfg["pattern"], data, timeout=0.1) is not None
        except TimeoutError:
            raise ControlBlocked("Regex evaluation exceeded its safety limit.") from None
    if op == "numeric_compare":
        return _number(data) and _COMPARE[cfg["operator"]](data, cfg["value"])
    if op == "empty":
        return data is None or isinstance(data, (str, list, dict)) and len(data) == 0
    if op == "json_path_truthy":
        try:
            return bool(_extract(data, cfg["path"]))
        except ValueError:
            return False
    if op == "json_schema":
        from .step_adapters import check_json_result
        try:
            check_json_result(data, cfg["schema"])
        except ValueError:
            return False
        return True
    if op == "numeric_range":
        return _range(data, cfg)
    if op == "row_count":
        return isinstance(data, list) and _range(len(data), cfg)
    raise ValueError("Unknown control-flow predicate.")


def map_items(value):
    if not isinstance(value, JsonValue) or not isinstance(value.value, list):
        raise ValueError("Map requires a table or JSON array.")
    if len(value.value) > MAX_MAP_ITEMS:
        raise ValueError("Map supports at most 100 items.")
    return value.value


def map_workflow(cfg):
    """Decode the frozen child workflow; never resolve live graph references."""
    if not isinstance(cfg.get("workflow"), dict):
        raise ValueError("Map requires a contained workflow spec.")
    child = decode_spec(cfg["workflow"])
    if child.inputs != (PortSpec("item", "json", True),):
        raise ValueError("Map workflow must declare exactly one required JSON input named item.")
    # A bounded, single-level expansion keeps preview, admission and cancellation finite.
    if any(step.adapter.adapter_id in (MAP, APPROVAL) for step in child.steps):
        raise ValueError("Nested Map and Approval inside Map are not supported.")
    return child


class ControlAdapter:
    adapter_version = "1"
    side_effect_class = "pure"

    def __init__(self, adapter_id):
        self.adapter_id = adapter_id

    def config(self, config_json):
        cfg = json.loads(config_json)
        if not isinstance(cfg, dict):
            raise ValueError("Control-flow configuration must be an object.")
        kind = cfg.get("kind", "json")
        if kind not in ("text", "json", "table"):
            raise ValueError("Control-flow kind must be text, json or table.")
        if self.adapter_id in (CONDITION, ASSERT):
            _validate_predicate(cfg, self.adapter_id)
        elif self.adapter_id == MERGE:
            names = cfg.get("inputs", ["left", "right"])
            if (cfg.keys() - {"kind", "inputs", "mode"} or cfg.get("mode", "all") not in ("all", "first")
                    or not isinstance(names, list) or not names
                    or not all(isinstance(name, str) and name for name in names)
                    or len(set(names)) != len(names)):
                raise ValueError("Merge needs unique named inputs and all or first mode.")
        elif self.adapter_id == APPROVAL:
            if (cfg.keys() - {"kind", "message"} or not isinstance(cfg.get("message"), str)
                    or not cfg["message"].strip() or len(cfg["message"]) > 4096):
                raise ValueError("Approval requires a nonempty message of at most 4096 characters.")
        elif self.adapter_id == MAP:
            if (cfg.keys() - {"workflow", "on_error", "concurrency", "item_count"}
                    or cfg.get("on_error", "stop") not in ("stop", "continue")
                    or type(cfg.get("concurrency", 1)) is not int
                    or not 1 <= cfg.get("concurrency", 1) <= MAX_MAP_ITEMS):
                raise ValueError("Map needs stop/continue and concurrency between 1 and 100.")
            if "item_count" in cfg and (type(cfg["item_count"]) is not int
                                        or not 0 <= cfg["item_count"] <= MAX_MAP_ITEMS):
                raise ValueError("Map item_count must be between 0 and 100.")
            map_workflow(cfg)
        return cfg

    def ports(self, config_json):
        cfg = self.config(config_json)
        kind = cfg.get("kind", "json")
        if self.adapter_id == CONDITION:
            return PortSet((PortSpec("value", kind, True),),
                           tuple(PortSpec(name, kind, False) for name in BRANCHES[CONDITION]))
        if self.adapter_id == ASSERT:
            routed = cfg.get("on_failure", "block") == "route"
            outputs = (PortSpec("pass", kind, not routed),)
            if routed:
                outputs += (PortSpec("fail", kind, False),)
            return PortSet((PortSpec("value", kind, True),), outputs)
        if self.adapter_id == MERGE:
            mode = cfg.get("mode", "all")
            return PortSet(tuple(PortSpec(name, kind, False) for name in cfg.get("inputs", ["left", "right"])),
                           (PortSpec("result", kind if mode == "first" else "json", True),))
        if self.adapter_id == MAP:
            return PortSet((PortSpec("items", "json", True),), (PortSpec("results", "json", True),))
        return PortSet((PortSpec("value", kind, True),), (PortSpec("value", kind, True),))

    def prepare(self, spec, inputs):
        cfg = self.config(spec.config_json)
        ports = self.ports(spec.config_json)
        by_name = {port.name: port for port in ports.inputs}
        if (set(inputs) - by_name.keys()
                or any(port.required and port.name not in inputs for port in ports.inputs)
                or any(value.kind != by_name[name].kind for name, value in inputs.items())):
            raise ValueError("Control-flow inputs do not match the declared ports.")
        if self.adapter_id == MAP:
            map_items(inputs["items"])
        # Preserve declared order for all-mode merges, while first-mode receives
        # only the winning input from the scheduler.
        return cfg, dict(inputs)

    def fingerprint(self, prepared):
        cfg, inputs = prepared
        return digest([self.adapter_id, self.adapter_version, cfg,
                       {name: asdict(value) for name, value in inputs.items()}])

    def preview_config(self, cfg):
        if self.adapter_id == CONDITION:
            return {"paths": ["then", "else"], "predicate": cfg["predicate"]}
        if self.adapter_id == ASSERT:
            return {"paths": ["pass", "fail"] if cfg.get("on_failure") == "route" else ["pass"],
                    "predicate": cfg["predicate"], "on_failure": cfg.get("on_failure", "block")}
        if self.adapter_id == APPROVAL:
            return {"message": cfg["message"], "waits_for_approval": True}
        if self.adapter_id == MERGE:
            return {"mode": cfg.get("mode", "all")}
        return {"maximum_items": MAX_MAP_ITEMS}

    def preview(self, prepared):
        cfg, inputs = prepared
        result = self.preview_config(cfg)
        if self.adapter_id == MAP:
            result["item_count"] = len(map_items(inputs["items"]))
        return result

    async def run(self, prepared, context):
        cfg, inputs = prepared
        if self.adapter_id == CONDITION:
            return {"then" if _predicate(cfg, inputs["value"]) else "else": inputs["value"]}
        if self.adapter_id == ASSERT:
            if _predicate(cfg, inputs["value"]):
                return {"pass": inputs["value"]}
            if cfg.get("on_failure", "block") == "route":
                return {"fail": inputs["value"]}
            raise ControlBlocked(f"Assertion failed: {cfg['predicate'].replace('_', ' ')} check did not pass.")
        if self.adapter_id == MERGE:
            if not inputs:
                raise ValueError("Merge requires a completed input.")
            if cfg.get("mode", "all") == "first":
                return {"result": next(iter(inputs.values()))}
            names = cfg.get("inputs", ["left", "right"])
            return {"result": JsonValue([_data(inputs[name]) for name in names if name in inputs])}
        if self.adapter_id == APPROVAL:
            if context.wait_for_approval is None:
                raise ValueError("Approval requires the workflow scheduler.")
            await context.wait_for_approval(cfg["message"])
            return {"value": inputs["value"]}
        if context.run_map is None:
            raise ValueError("Map requires the workflow scheduler.")
        return {"results": JsonValue(await context.run_map(cfg, map_items(inputs["items"])))}

    async def cancel(self, context):
        pass


CONTROL_ADAPTERS = tuple(ControlAdapter(name) for name in (CONDITION, MERGE, APPROVAL, ASSERT, MAP))
