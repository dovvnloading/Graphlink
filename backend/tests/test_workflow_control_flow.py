"""Typed control-flow regression tests: no provider calls or real human delays."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from backend.chat_library import get_default_workspace_id
from backend.workflow.contracts import (
    InputBinding, JsonValue, PortSet, PortSpec, StepOutputSource, TableValue,
    TextValue, WorkflowInputSource, WorkflowOutput, WorkflowSpec, WorkflowStepSpec, freeze_config,
)
from backend.workflow.control_flow import APPROVAL, ASSERT, CONDITION, MAP, MERGE
from backend.workflow.engine import RunLimits, UnattendedPolicy, WorkflowEngine
from backend.workflow.planning import StepPolicy
from backend.workflow.registry import builtin_registry


def _port(name, kind="json", required=True):
    return PortSpec(name, kind, required)


def _step(registry, step_id, adapter, config, **bindings):
    config_json = freeze_config(config)
    return WorkflowStepSpec(step_id, registry.describe(adapter, "1", config_json), config_json,
                            tuple(InputBinding(name, source) for name, source in bindings.items()))


def _spec(steps, *, inputs=(_port("input"),), outputs=()):
    return WorkflowSpec(1, "control", "1", "Control flow", inputs, tuple(steps), tuple(outputs))


def _out(step, port, kind="json"):
    return WorkflowOutput(_port("output", kind), StepOutputSource(step, port))


async def _until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.005)


class Work:
    def __init__(self, effect="pure"):
        self.side_effect_class = effect
        self.gate = asyncio.Event()
        self.started = []
        self.active = 0
        self.peak = 0

    def ports(self, config_json):
        return PortSet((_port("value"),), (_port("result"),))

    def prepare(self, spec, inputs):
        return json.loads(spec.config_json), inputs["value"]

    def fingerprint(self, prepared):
        return "a" * 64

    def preview(self, prepared):
        return {}

    async def run(self, prepared, context):
        config, value = prepared
        self.started.append((context.step_id, value.value))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if config.get("wait"):
                await self.gate.wait()
            if "fail_item" in config and value.value == config["fail_item"]:
                raise ValueError("private provider error")
            return {"result": JsonValue(config.get("result", value.value))}
        finally:
            self.active -= 1

    async def cancel(self, context):
        pass


def _engine(path, limits=None):
    registry = builtin_registry()
    local, cloud = Work(), Work("cloud")
    registry.register("test.local", "1", local)
    registry.register("test.cloud", "1", cloud)
    engine = WorkflowEngine(path, registry=registry, limits=limits,
                            policy_for=lambda step: StepPolicy("cloud", ("provider", "model"), 0.1)
                            if step.adapter.side_effect_class == "cloud" else StepPolicy())
    return engine, get_default_workspace_id(path), local, cloud


@pytest.mark.parametrize(("config", "value", "expected"), [
    ({"predicate": "equals", "value": {"flag": True}}, JsonValue({"flag": True}), True),
    ({"predicate": "equals", "value": {"flag": True}}, JsonValue({"flag": 1}), False),
    ({"predicate": "contains", "value": "bc", "kind": "text"}, TextValue("abcd"), True),
    ({"predicate": "contains", "value": 3}, JsonValue([1, 2]), False),
    ({"predicate": "regex", "pattern": "^a[0-9]+$", "kind": "text"}, TextValue("a42"), True),
    ({"predicate": "numeric_compare", "operator": "gte", "value": 10}, JsonValue(10), True),
    ({"predicate": "numeric_compare", "operator": "lt", "value": 10}, JsonValue(True), False),
    ({"predicate": "empty"}, JsonValue([]), True),
    ({"predicate": "empty"}, JsonValue(False), False),
    ({"predicate": "json_path_truthy", "path": "$.data[0].ok"}, JsonValue({"data": [{"ok": True}]}), True),
    ({"predicate": "json_path_truthy", "path": "$.missing"}, JsonValue({}), False),
])
def test_condition_predicates_and_preview(config, value, expected):
    async def scenario():
        registry = builtin_registry()
        step = _step(registry, "condition", CONDITION, config, value=WorkflowInputSource("input"))
        adapter = registry.resolve(CONDITION, "1")
        prepared = adapter.prepare(step, {"value": value})
        assert adapter.preview(prepared)["paths"] == ["then", "else"]
        from backend.workflow.adapter import StepContext
        output = await adapter.run(prepared, StepContext("run", "condition", 1))
        assert output == {"then" if expected else "else": value}
    asyncio.run(scenario())


@pytest.mark.parametrize("condition", [True, False])
def test_skips_propagate_and_merge_all_ignores_skipped_inputs(tmp_path, condition):
    async def scenario():
        engine, ws, local, _ = _engine(tmp_path / "chats.db")
        r = engine.registry
        root = WorkflowInputSource("input")
        steps = [
            _step(r, "after_else", "test.local", {}, value=StepOutputSource("else", "result")),
            _step(r, "merge", MERGE, {"inputs": ["yes", "no"]},
                  yes=StepOutputSource("then", "result"), no=StepOutputSource("after_else", "result")),
            _step(r, "then", "test.local", {"result": "yes"}, value=StepOutputSource("if", "then")),
            _step(r, "else", "test.local", {"result": "no"}, value=StepOutputSource("if", "else")),
            _step(r, "if", CONDITION, {"predicate": "equals", "value": True}, value=root),
        ]
        spec = _spec(steps, outputs=(_out("merge", "result"),))
        params = {"input": JsonValue(condition)}
        preview = engine.preview(spec, params)
        assert preview.control_flow["if"]["paths"] == ["then", "else"]
        assert not local.started
        record = await engine.wait(ws, engine.start(ws, spec, params))
        assert record["status"] == "completed", record["error"]
        assert record["outputs"]["output"]["value"] == ["yes" if condition else "no"]
        skipped = "else" if condition else "then"
        assert record["steps"]["if"]["skipped_outputs"] == [skipped]
        assert record["steps"][skipped]["status"] == "skipped"
        assert skipped not in [step for step, _ in local.started]
        if condition:
            assert record["steps"]["after_else"]["status"] == "skipped"
        assert not any(event["kind"] == "step-failed" for event in record["events"])
    asyncio.run(scenario())


def test_merge_first_finishes_before_other_input_and_uses_completion_order(tmp_path):
    async def scenario():
        engine, ws, local, _ = _engine(tmp_path / "chats.db", RunLimits(local_concurrency=2))
        r = engine.registry
        root = WorkflowInputSource("input")
        spec = _spec([
            _step(r, "slow", "test.local", {"wait": True, "result": "slow"}, value=root),
            _step(r, "fast", "test.local", {"result": "fast"}, value=root),
            _step(r, "merge", MERGE, {"mode": "first"},
                  left=StepOutputSource("slow", "result"), right=StepOutputSource("fast", "result")),
            _step(r, "after", "test.local", {}, value=StepOutputSource("merge", "result")),
        ], outputs=(_out("after", "result"),))
        run_id = engine.start(ws, spec, {"input": JsonValue(0)})
        await _until(lambda: any(key == "after" for key, _ in local.started))
        snapshot = engine.store.read(ws, run_id)
        assert snapshot["steps"]["slow"]["status"] == "running"
        assert snapshot["steps"]["merge"]["outputs"]["result"]["value"] == "fast"
        local.gate.set()
        record = await engine.wait(ws, run_id)
        assert record["status"] == "completed"
        assert record["outputs"]["output"]["value"] == "fast"
    asyncio.run(scenario())


@pytest.mark.parametrize(("config", "value", "passed"), [
    ({"predicate": "contains", "value": "x", "kind": "text"}, TextValue("text"), True),
    ({"predicate": "regex", "pattern": "^x", "kind": "text"}, TextValue("text"), False),
    ({"predicate": "numeric_range", "min": 1, "max": 2}, JsonValue(2), True),
    ({"predicate": "numeric_range", "min": 1}, JsonValue(True), False),
    ({"predicate": "row_count", "min": 2, "kind": "table"}, TableValue(("a",), ((1,),)), False),
    ({"predicate": "row_count", "max": 2}, JsonValue([1, 2]), True),
    ({"predicate": "json_schema", "schema": {"type": "object", "required": ["id"],
                                             "properties": {"id": {"type": "integer"}}}},
     JsonValue({"id": 1}), True),
    ({"predicate": "json_schema", "schema": {"type": "array", "items": {"type": "number"}}},
     JsonValue(["wrong"]), False),
])
def test_assert_route_predicates(config, value, passed):
    async def scenario():
        registry = builtin_registry()
        step = _step(registry, "assert", ASSERT, {**config, "on_failure": "route"},
                     value=WorkflowInputSource("input"))
        adapter = registry.resolve(ASSERT, "1")
        from backend.workflow.adapter import StepContext
        result = await adapter.run(adapter.prepare(step, {"value": value}), StepContext("run", "assert", 1))
        assert result == {"pass" if passed else "fail": value}
    asyncio.run(scenario())


@pytest.mark.parametrize("route", [False, True])
def test_assert_block_reason_or_fail_route(tmp_path, route):
    async def scenario():
        engine, ws, local, _ = _engine(tmp_path / "chats.db")
        r = engine.registry
        config = {"predicate": "numeric_range", "min": 2}
        if route:
            config["on_failure"] = "route"
        steps = [
            _step(r, "assert", ASSERT, config, value=WorkflowInputSource("input")),
            _step(r, "pass", "test.local", {}, value=StepOutputSource("assert", "pass")),
        ]
        if route:
            steps.append(_step(r, "fail", "test.local", {}, value=StepOutputSource("assert", "fail")))
        record = await engine.wait(ws, engine.start(ws, _spec(steps), {"input": JsonValue(1)}))
        if route:
            assert record["status"] == "completed"
            assert record["steps"]["pass"]["status"] == "skipped"
            assert record["steps"]["fail"]["status"] == "completed"
        else:
            assert record["status"] == "failed"
            assert record["steps"]["assert"]["status"] == "blocked"
            assert "Assertion failed: numeric range" in record["error"]
            assert not local.started
    asyncio.run(scenario())


@pytest.mark.parametrize("approved", [True, False])
def test_human_gate_pauses_without_duration_timeout(tmp_path, monkeypatch, approved):
    async def scenario():
        # Control the scheduler's clock so slow journal writes on Windows CI
        # cannot consume the execution budget. Keep real asyncio timers and
        # wait longer than the cap to exercise the human gate's timeout exemption.
        clock = SimpleNamespace(now=0.0)
        loop = asyncio.get_running_loop()
        scheduler_loop = SimpleNamespace(
            time=lambda: clock.now, create_task=loop.create_task, create_future=loop.create_future,
        )
        scheduler_asyncio = SimpleNamespace(**vars(asyncio))
        scheduler_asyncio.get_running_loop = lambda: scheduler_loop
        monkeypatch.setattr("backend.workflow.engine.asyncio", scheduler_asyncio)
        engine, ws, local, _ = _engine(tmp_path / "chats.db", RunLimits(wall_time_seconds=0.5))
        r = engine.registry
        root = WorkflowInputSource("input")
        spec = _spec([
            _step(r, "gate", APPROVAL, {"message": "Review the report"}, value=root),
            _step(r, "independent", "test.local", {}, value=root),
            _step(r, "after", "test.local", {}, value=StepOutputSource("gate", "value")),
        ])
        run_id = engine.start(ws, spec, {"input": JsonValue("report")})
        await _until(lambda: bool(engine.pending_approvals()))
        assert engine.pending_approvals() == [
            {"workspace_id": ws, "run_id": run_id, "workflow": "Control flow",
             "step_id": "gate", "message": "Review the report"},
        ]
        clock.now += 0.7
        await asyncio.sleep(0.7)
        snapshot = engine.store.read(ws, run_id)
        assert snapshot["status"] == "running" and not local.started
        assert snapshot["steps"]["gate"]["approval"]["status"] == "pending"
        assert not engine.decide_step(ws + 1, run_id, "gate", True)
        assert not engine.decide_step(ws, run_id, "wrong", True)
        assert engine.decide_step(ws, run_id, "gate", approved)
        assert not engine.decide_step(ws, run_id, "gate", approved)
        record = await engine.wait(ws, run_id)
        assert record["status"] == ("completed" if approved else "failed"), record["error"]
        assert record["steps"]["gate"]["approval"]["status"] == ("approved" if approved else "rejected")
        assert len(local.started) == (2 if approved else 0)
        assert not engine.pending_approvals()
    asyncio.run(scenario())


def _map_spec(registry, *, cloud=True, config=None, **map_config):
    child = _spec([
        _step(registry, "work", "test.cloud" if cloud else "test.local",
              config or {}, value=WorkflowInputSource("item")),
    ], inputs=(_port("item"),), outputs=(_out("work", "result"),))
    map_step = _step(registry, "map", MAP, {"workflow": asdict(child), **map_config},
                     items=WorkflowInputSource("input"))
    return _spec([map_step], outputs=(_out("map", "results"),))


def test_map_preview_order_provider_limit_and_continue(tmp_path):
    async def scenario():
        engine, ws, _, cloud = _engine(tmp_path / "chats.db", RunLimits(cloud_concurrency=2))
        spec = _map_spec(engine.registry, config={"wait": True, "fail_item": 2},
                         concurrency=8, on_error="continue")
        params = {"input": JsonValue([3, 2, 1, 0])}
        preview = engine.preview(spec, params)
        assert preview.control_flow["map"]["item_count"] == 4
        assert preview.control_flow["map"]["estimated_cost_usd"] == 0.4
        assert preview.requires_approval and not cloud.started
        with pytest.raises(PermissionError):
            engine.start(ws, spec, params)
        run_id = engine.start(ws, spec, params, approval_token=engine.approve(ws, preview))
        await _until(lambda: cloud.active == 2)
        assert cloud.peak == 2 and len(cloud.started) == 2
        cloud.gate.set()
        record = await engine.wait(ws, run_id)
        assert record["status"] == "completed", record["error"]
        results = record["outputs"]["output"]["value"]
        assert [item["index"] for item in results] == [0, 1, 2, 3]
        assert [item["status"] for item in results] == ["completed", "failed", "completed", "completed"]
        assert [item["outputs"]["output"]["value"] for item in results if item["status"] == "completed"] == [3, 1, 0]
        assert cloud.peak == 2 and record["cost_usd"] == 0.4
        assert "private provider error" not in json.dumps(record)
    asyncio.run(scenario())


def test_map_stop_cap_and_empty_table(tmp_path):
    async def scenario():
        engine, ws, local, _ = _engine(tmp_path / "chats.db")
        spec = _map_spec(engine.registry, cloud=False, config={"fail_item": 0})
        with pytest.raises(ValueError, match="at most 100"):
            engine.preview(spec, {"input": JsonValue(list(range(101)))})
        record = await engine.wait(ws, engine.start(ws, spec, {"input": JsonValue([0, 1, 2])}))
        assert record["status"] == "failed" and "Map item 1" in record["error"]
        assert local.started == [("work", 0)]
        table_spec = _spec(spec.steps, inputs=(_port("input", "table"),), outputs=spec.outputs)
        params = {"input": TableValue(("a",), ((3,), (4,)))}
        preview = engine.preview(table_spec, params)
        assert preview.control_flow["map"]["item_count"] == 2
        record = await engine.wait(ws, engine.start(ws, table_spec, params))
        assert [item["outputs"]["output"]["value"] for item in record["outputs"]["output"]["value"]] == [
            {"a": 3}, {"a": 4},
        ]
        empty = {"input": TableValue(("a",), ())}
        record = await engine.wait(ws, engine.start(ws, table_spec, empty))
        assert record["outputs"]["output"]["value"] == []
    asyncio.run(scenario())


def test_map_expansion_reserves_step_and_cost_limits_before_dispatch(tmp_path):
    async def scenario():
        engine, ws, _, cloud = _engine(tmp_path / "chats.db", RunLimits(max_steps=3))
        spec = _map_spec(engine.registry)
        params = {"input": JsonValue([1, 2, 3])}
        preview = engine.preview(spec, params)
        run_id = engine.start(ws, spec, params, approval_token=engine.approve(ws, preview))
        record = await engine.wait(ws, run_id)
        assert record["status"] == "failed" and "step limit" in record["error"]
        assert not cloud.started
    asyncio.run(scenario())


def test_unattended_map_respects_child_effect_policy_and_cost_cap(tmp_path):
    async def scenario():
        engine, ws, _, cloud = _engine(tmp_path / "chats.db")
        spec = _map_spec(engine.registry)
        params = {"input": JsonValue([1, 2])}
        preview = engine.preview(spec, params)
        with pytest.raises(PermissionError, match="Preset Map children"):
            engine.start(ws, spec, params, unattended=UnattendedPolicy(
                preview.fingerprint, frozenset({"pure"}), 1.0))
        assert not cloud.started
        run_id = engine.start(ws, spec, params, unattended=UnattendedPolicy(
            preview.fingerprint, frozenset({"pure", "cloud"}), 0.1))
        record = await engine.wait(ws, run_id)
        assert record["status"] == "failed" and not cloud.started
        run_id = engine.start(ws, spec, params, unattended=UnattendedPolicy(
            preview.fingerprint, frozenset({"pure", "cloud"}), 0.2))
        record = await engine.wait(ws, run_id)
        assert record["status"] == "completed", record["error"]
        assert record["cost_usd"] == 0.2
        assert len(cloud.started) == 2
    asyncio.run(scenario())
