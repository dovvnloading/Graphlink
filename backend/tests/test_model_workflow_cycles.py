"""Model inputs reject cycles through reusable Workflow nodes (#1090)."""

from __future__ import annotations

import asyncio

import pytest

from backend.canvas import SceneDocument
from backend.domain.graph_runner import plan_graph_run
from backend.domain.model import SceneError
from backend.events import IntentValidationError
from backend.tests.test_graph_model_intents import session
from backend.tests.test_workflow_library import example
from backend.workflow import canvas
from graphlink_task_config import LOCAL_PROVIDER_OLLAMA

MODEL = (LOCAL_PROVIDER_OLLAMA, "qwen3:8b")


def _workflow(doc, source_id, x=800):
    node = doc.nodes[canvas.insert(doc, example(), 1, x, 0)]
    canvas.configure(doc, node, {}, {}, {"input": source_id})
    return node


def test_model_cannot_take_its_input_from_a_workflow_that_depends_on_it():
    doc = SceneDocument()
    source = doc.add_note(0, 0)
    model = doc.add_model_node(source.id, *MODEL)
    workflow = _workflow(doc, model.id)
    before = model.state.model_binding
    with pytest.raises(SceneError, match="cycle"):
        doc.configure_model_node(model.id, workflow.id, *MODEL)
    assert model.state.model_binding == before


def test_indirect_cycles_through_several_workflows_are_rejected():
    doc = SceneDocument()
    source = doc.add_note(0, 0)
    model = doc.add_model_node(source.id, *MODEL)
    first = _workflow(doc, model.id)
    second = _workflow(doc, first.id, x=1200)
    with pytest.raises(SceneError, match="cycle"):
        doc.configure_model_node(model.id, second.id, *MODEL)


def test_an_acyclic_workflow_source_is_still_accepted():
    doc = SceneDocument()
    source = doc.add_note(0, 0)
    model = doc.add_model_node(source.id, *MODEL)
    workflow = _workflow(doc, source.id)
    doc.configure_model_node(model.id, workflow.id, *MODEL)
    assert model.state.model_binding.source_id == workflow.id
    assert all(row["status"] != "blocked" for row in plan_graph_run(doc, [model.id]))


def test_rejected_intents_leave_binding_and_undo_stack_unchanged(tmp_path):
    async def scenario():
        _path, _bus, doc, source, _target, send = session(tmp_path)
        model = doc.add_model_node(source.id, *MODEL)
        workflow = _workflow(doc, model.id)
        before = model.state.model_binding
        log = list(doc.command_log)
        with pytest.raises(IntentValidationError, match="cycle"):
            await send("connectModelInput", workflow.id, model.id)
        with pytest.raises(IntentValidationError, match="cycle"):
            await send("configureModelNode", workflow.id, *MODEL, model.id)
        assert model.state.model_binding == before
        assert list(doc.command_log) == log
    asyncio.run(scenario())
