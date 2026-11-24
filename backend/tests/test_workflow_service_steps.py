"""Regression coverage for the typed service adapter boundary (no paid calls)."""
import asyncio
from types import SimpleNamespace

import pytest

from backend.domain.graph_run_external import ExternalCall
from backend.workflow.adapter import StepContext
from backend.workflow.contracts import TableValue, TextValue, WorkflowSpec, WorkflowStepSpec, freeze_config
from backend.workflow.registry import builtin_registry
from backend.workflow.step_adapters import MODEL_ID, STEP_CLASSES, check_json_result
from backend.workflow.step_services import WorkflowRequestJournal, WorkflowRunHost
from graphlink_task_config import LOCAL_PROVIDER_OLLAMA


def _step(registry, adapter_id, config, step_id="step"):
    config_json = freeze_config(config)
    return WorkflowStepSpec(step_id, registry.describe(adapter_id, "1", config_json), config_json, ())


def _spec(*steps):
    return WorkflowSpec(1, "workflow", "revision", "Test workflow", (), steps, ())


def _settings():
    return SimpleNamespace(get_pricing_overrides=lambda: {})


def _model_config(**overrides):
    return {"provider": LOCAL_PROVIDER_OLLAMA, "model_id": "test-model", "system_prompt": "System",
            "template": "Summarize {subject}", "output_kind": "text", **overrides}


def test_all_service_steps_describe_and_preview_without_services():
    registry = builtin_registry()
    examples = {
        MODEL_ID: (_model_config(), {"subject": TextValue("subject")}),
        "python.execute": ({"code": "print(x)", "inputs": {"x": "text"}, "output_kind": "text"},
                           {"x": TextValue("hello")}),
        "knowledge.search": ({}, {"query": TextValue("question")}),
        "web.research": ({}, {"query": TextValue("question")}),
        "chart.generate": ({"label_column": "name", "value_column": "count"},
                           {"table": TableValue(("name", "count"), (("A", 2), ("A", 3))),
                            "text": TextValue("Chart")}),
        "report.generate": ({"label_column": "name", "value_column": "count"},
                            {"table": TableValue(("name", "count"), (("A", 2), ("A", 3)))}),
        "image.generate": ({"model_id": "fal-ai/flux/dev"}, {"prompt": TextValue("A landscape")}),
        "agent.run": ({"folder": "/example"}, {"task": TextValue("Review project")}),
    }
    for adapter_id, (config, inputs) in examples.items():
        step = _step(registry, adapter_id, config)
        adapter = registry.resolve(adapter_id, "1")
        prepared = adapter.prepare(step, inputs)
        assert adapter.preview(prepared)["sideEffectClass"] == STEP_CLASSES[adapter_id]
        assert adapter.fingerprint(prepared) == adapter.fingerprint(adapter.prepare(step, inputs))
        if STEP_CLASSES[adapter_id] == "pure":
            result = asyncio.run(adapter.run(prepared, StepContext("run", "step", 1)))
            assert result
        else:
            with pytest.raises(RuntimeError, match="admitted run host"):
                asyncio.run(adapter.run(prepared, StepContext("run", "step", 1)))


def test_model_named_inputs_and_schema():
    registry = builtin_registry()
    schema = {"type": "object", "properties": {"answer": {"type": "string"}},
              "required": ["answer"], "additionalProperties": False}
    step = _step(registry, MODEL_ID, _model_config(output_kind="json", schema=schema))
    assert [port.name for port in step.adapter.ports.inputs] == ["subject"]
    assert step.adapter.ports.outputs[0].kind == "json"
    check_json_result({"answer": "yes"}, schema)
    with pytest.raises(ValueError):
        check_json_result({"answer": 1}, schema)
    with pytest.raises(ValueError):
        check_json_result({"answer": "yes", "unexpected": 1}, schema)


def test_model_limit_admission(tmp_path):
    registry = builtin_registry()
    steps = [_step(registry, MODEL_ID, _model_config(), str(i)) for i in range(65)]
    journal = WorkflowRequestJournal(tmp_path / "journal.sqlite")
    WorkflowRunHost(_spec(*steps[:64]), "run", settings_manager=_settings(), journal=journal)
    with pytest.raises(ValueError, match="64"):
        WorkflowRunHost(_spec(*steps), "run", settings_manager=_settings(), journal=journal)


def test_durable_fence_blocks_replay_and_keeps_typed_output(tmp_path):
    discovery = builtin_registry()
    step = _step(discovery, MODEL_ID, _model_config())
    spec = _spec(step)
    path = tmp_path / "journal.sqlite"
    host = WorkflowRunHost(spec, "run", settings_manager=_settings(), journal=WorkflowRequestJournal(path))
    adapter = builtin_registry(host).resolve(MODEL_ID, "1")
    prepared = adapter.prepare(step, {"subject": TextValue("topic")})
    fingerprint = adapter.fingerprint(prepared)
    calls = []

    async def invoke(request_id):
        saved, outputs = WorkflowRequestJournal(path).read("run", "step")
        assert saved["externalAttempt"]["requestId"] == request_id
        assert outputs is None
        calls.append(request_id)
        return {"text": TextValue("result")}

    def call():
        return ExternalCall("model", "Ollama", "workflow-input:" + fingerprint, fingerprint,
                            model_id="test-model")

    result = asyncio.run(host._external(adapter, prepared, StepContext("run", "step", 1), call(), invoke))
    assert result == {"text": TextValue("result")}
    saved, outputs = WorkflowRequestJournal(path).read("run", "step")
    assert saved["externalAttempt"]["phase"] == "completed"
    assert outputs["text"]["text"] == "result"
    restarted = WorkflowRunHost(spec, "run", settings_manager=_settings(),
                                journal=WorkflowRequestJournal(path))
    with pytest.raises(Exception):
        asyncio.run(restarted._external(adapter, prepared, StepContext("run", "step", 2), call(), invoke))
    assert len(calls) == 1


def test_uncertain_failure_does_not_replay(tmp_path):
    registry = builtin_registry()
    step = _step(registry, MODEL_ID, _model_config())
    journal = WorkflowRequestJournal(tmp_path / "journal.sqlite")
    host = WorkflowRunHost(_spec(step), "run", settings_manager=_settings(), journal=journal)
    adapter = registry.resolve(MODEL_ID, "1")
    prepared = adapter.prepare(step, {"subject": TextValue("topic")})
    fingerprint = adapter.fingerprint(prepared)
    calls = []

    async def fail(request_id):
        calls.append(request_id)
        raise TimeoutError("uncertain")

    for attempt in (1, 2):
        call = ExternalCall("model", "Ollama", "workflow-input:" + fingerprint, fingerprint,
                            model_id="test-model")
        with pytest.raises(Exception):
            asyncio.run(host._external(adapter, prepared, StepContext("run", "step", attempt), call, fail))
    assert len(calls) == 1
    receipt, outputs = journal.read("run", "step")
    assert receipt["externalAttempt"]["phase"] == "reconciliation-required"
    assert outputs is None


def test_python_approval_denial(tmp_path):
    registry = builtin_registry()
    step = _step(registry, "python.execute", {"code": "print('never')", "inputs": {}, "output_kind": "text"})
    host = WorkflowRunHost(_spec(step), "run", settings_manager=_settings(),
                           journal=WorkflowRequestJournal(tmp_path / "journal.sqlite"))
    adapter = builtin_registry(host).resolve("python.execute", "1")
    with pytest.raises(PermissionError):
        asyncio.run(adapter.run(adapter.prepare(step, {}), StepContext("run", "step", 1)))


def test_cloud_consent_is_scoped(tmp_path):
    host = WorkflowRunHost(_spec(), "run", settings_manager=_settings(),
                           journal=WorkflowRequestJournal(tmp_path / "journal.sqlite"),
                           cloud_consent=lambda *args: args == ("workflow", "revision", "provider", "https://allowed"))
    host._require_cloud("provider", "https://allowed")
    with pytest.raises(PermissionError):
        host._require_cloud("provider", "https://different")
