"""Import and execute every shipped starter through the real detached engine."""
from __future__ import annotations

import asyncio
import json
from contextlib import closing
from copy import deepcopy

import pytest

from backend import chat_library, workflow_library_store as store
from backend.api.intents_workflow_library import register_workflow_library
from backend.domain.graph import SceneDocument
from backend.providers import graph_local_ollama
from backend.workflow import canvas
from backend.workflow.engine import WorkflowEngine
from backend.workflow.library import configured_spec, export_document, import_document
from backend.workflow.starters import ENDPOINT, MODEL_A, MODEL_B, list_starters, starter_document
from backend.workflow.wire import decode_request


SCRIPTS = {
    "table-chart-report-v1": [
        (MODEL_A, "Chart:", "Support has 12 tickets; Sales has 3."),
    ],
    "research-summary-v1": [
        (MODEL_A, "[R1] Pilot log:", "[R1] Wait fell from 12 to 8 minutes. [R2] No control group."),
        (MODEL_A, "Findings:", "- Wait fell [R1].\n- One team [R2].\n- Causality uncertain [R2]."),
    ],
    "compare-models-v1": [
        (MODEL_A, "reusable water bottle", "Refilling avoids disposable bottles."),
        (MODEL_B, "reusable water bottle", "Reuse reduces single-use plastic."),
    ],
    "document-qa-v1": [
        (MODEL_A, "[D2] Library handbook, page 3:", "A loan may be renewed once for 7 days [D2]."),
    ],
    "summarize-rows-v1": [
        (MODEL_A, "Delivery", "Delivery was two days early."),
        (MODEL_A, "Packaging", "Packaging used recyclable cardboard."),
        (MODEL_A, "Support", "Support resolved the issue on first contact."),
    ],
    "text-quality-gate-v1": [
        (MODEL_A, "launch checklist", "Summary: Checklist complete.\nAction: Request team review."),
    ],
}


class _Bus:
    def __init__(self):
        self.handlers = {}

    def register_intent(self, topic, intent, handler, **_kwargs):
        self.handlers[topic, intent] = handler


def _library_actions(path):
    bus = _Bus()
    document = SceneDocument()
    register_workflow_library(bus, document, lambda: chat_library._connect(path), lambda fn: fn)
    return document, bus.handlers["scene", "workflowLibrary"], bus.handlers["scene", "mutateWorkflowNode"]


def _script_model(monkeypatch, script):
    remaining = list(script)
    calls = []

    async def complete(*, prompt, model_id, endpoint):
        assert endpoint == ENDPOINT
        for index, (expected_model, needle, reply) in enumerate(remaining):
            if model_id == expected_model and needle in prompt:
                remaining.pop(index)
                calls.append((model_id, prompt))
                return {"message": {"content": reply}}
        raise AssertionError(f"Unexpected model call {model_id}: {prompt}")

    monkeypatch.setenv("OLLAMA_HOST", ENDPOINT)
    monkeypatch.setattr(graph_local_ollama, "async_local_ollama_complete", complete)
    return remaining, calls


def _assert_result(starter_id, outputs, calls):
    if starter_id == "table-chart-report-v1":
        chart = outputs["chart"]["value"]
        assert chart["labels"] == ["Support", "Sales"]
        assert chart["values"] == [12, 3]
        assert '"Support"' in calls[0][1] and "12" in calls[0][1]
        report = outputs["report"]["text"]
        assert report.startswith("Support has 12 tickets; Sales has 3.")
        assert "| Support | 12 |" in report and "| Sales | 3 |" in report
    elif starter_id == "research-summary-v1":
        assert outputs["findings"]["text"] in calls[1][1]
        assert outputs["summary"]["text"] == SCRIPTS[starter_id][1][2]
    elif starter_id == "compare-models-v1":
        assert calls[0][1] == calls[1][1]
        assert outputs["model_a"]["text"] == SCRIPTS[starter_id][0][2]
        assert outputs["model_b"]["text"] == SCRIPTS[starter_id][1][2]
        assert outputs["comparison"]["text"] == (
            "## Model A\nRefilling avoids disposable bottles.\n\n"
            "## Model B\nReuse reduces single-use plastic."
        )
    elif starter_id == "document-qa-v1":
        assert "How long may a loan be renewed?" in calls[0][1]
        assert outputs["answer"]["text"] == "A loan may be renewed once for 7 days [D2]."
    elif starter_id == "summarize-rows-v1":
        rows = outputs["rows"]["value"]
        assert [row["index"] for row in rows] == [0, 1, 2]
        assert [row["status"] for row in rows] == ["completed"] * 3
        assert [row["outputs"]["summary"]["text"] for row in rows] == [
            reply for _, _, reply in SCRIPTS[starter_id]
        ]
    elif starter_id == "text-quality-gate-v1":
        assert outputs["approved_text"]["text"] == SCRIPTS[starter_id][0][2]


def test_catalog_and_import_boundary(tmp_path):
    async def scenario():
        path = tmp_path / "chats.db"
        document, action, _ = _library_actions(path)
        rows = await action("starters", {})
        assert rows == list_starters()
        assert len(rows) == 6
        assert {row["id"] for row in rows} == set(SCRIPTS)
        assert await action("list", {}) == []
        with pytest.raises(ValueError, match="Unknown"):
            starter_document("not-a-starter")
        for row in rows:
            original = starter_document(row["id"])
            pin = await action("importStarter", {"starter_id": row["id"]})
            frozen = await action("get", {"workflow_id": pin["workflow_id"], "revision": pin["revision"]})
            assert frozen["spec"]["workflow_id"] != original["spec"]["workflow_id"]
            assert frozen["spec"]["steps"] == original["spec"]["steps"]
            assert frozen["example_inputs"] == original["example_inputs"]
            assert import_document(await action("export", pin)) == frozen
            with pytest.raises(ValueError):
                configured_spec(frozen, {})
            with pytest.raises(ValueError, match="detached"):
                canvas.insert(document, frozen, 1, 0, 0)
            changed = deepcopy(frozen)
            changed["example_inputs"] = {}
            with pytest.raises(ValueError, match="published version"):
                import_document(json.dumps(changed))
            changed = deepcopy(frozen)
            changed["spec"]["steps"][0]["config_json"] = "{}"
            with pytest.raises(ValueError, match="published version"):
                import_document(json.dumps(changed))
            assert len(document.nodes) == 0
        assert len(await action("list", {})) == 6
        # Existing file import still creates a new local identity.
        pin = await action("import", {"text": export_document(starter_document(rows[0]["id"]))})
        assert pin["version"] == 1
        assert len(await action("list", {})) == 7
        with closing(chat_library._connect(path)) as conn, conn:
            frozen = store.get(conn, pin["workflow_id"], pin["revision"])
            duplicate = store.duplicate(conn, pin["workflow_id"], pin["revision"], "Copy")
            assert store.get(conn, duplicate["workflow_id"], duplicate["revision"])["example_inputs"] == frozen["example_inputs"]
            store.delete(conn, pin["workflow_id"])
            assert store.get(conn, pin["workflow_id"], pin["revision"]) == frozen

    asyncio.run(scenario())


@pytest.mark.parametrize("starter_id", SCRIPTS)
def test_starter_runs_offline(tmp_path, monkeypatch, starter_id):
    remaining, calls = _script_model(monkeypatch, SCRIPTS[starter_id])

    async def scenario():
        path = tmp_path / "chats.db"
        _, action, _ = _library_actions(path)
        pin = await action("importStarter", {"starter_id": starter_id})
        document = await action("get", pin)
        spec, inputs = decode_request({"spec": document["spec"], "parameters": document["example_inputs"]})
        workspace = chat_library.get_default_workspace_id(path)
        engine = WorkflowEngine(path)
        try:
            preview = engine.preview(spec, inputs)
            assert preview.requires_approval
            assert not calls
            with pytest.raises(PermissionError):
                engine.start(workspace, spec, inputs)
            if starter_id == "summarize-rows-v1":
                assert preview.control_flow["summarize_rows"]["item_count"] == 3
            run_id = engine.start(workspace, spec, inputs, approval_token=engine.approve(workspace, preview))
            record = await engine.wait(workspace, run_id)
            assert record["status"] == "completed", record
            assert not remaining
            _assert_result(starter_id, record["outputs"], calls)
            assert engine.store.read(workspace, run_id)["outputs"] == record["outputs"]
        finally:
            await engine.shutdown()

    asyncio.run(scenario())


def test_quality_gate_blocks_publication(tmp_path, monkeypatch):
    remaining, calls = _script_model(monkeypatch, [(MODEL_A, "launch checklist", "Summary: Checklist complete.")])

    async def scenario():
        path = tmp_path / "chats.db"
        document = starter_document("text-quality-gate-v1")
        spec, inputs = decode_request({"spec": document["spec"], "parameters": document["example_inputs"]})
        workspace = chat_library.get_default_workspace_id(path)
        engine = WorkflowEngine(path)
        try:
            preview = engine.preview(spec, inputs)
            run_id = engine.start(workspace, spec, inputs, approval_token=engine.approve(workspace, preview))
            record = await engine.wait(workspace, run_id)
            assert record["status"] == "failed"
            assert record["steps"]["quality_gate"]["status"] == "blocked"
            assert record["steps"]["publishable"]["status"] == "blocked"
            assert "approved_text" not in record["outputs"]
            assert not remaining and len(calls) == 1
        finally:
            await engine.shutdown()

    asyncio.run(scenario())
