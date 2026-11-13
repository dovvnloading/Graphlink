"""Builder places existing Fal jobs on the canvas without generating again."""
import asyncio
import json

import pytest

from backend.domain.graph import SceneDocument
from backend.fal_integration import FalClient
from backend.fal_media_domain import read_media
from backend.providers.base import ToolCall
from backend.tools import GRAPH_MUTATE, PROVIDER_CALL, RunContext, ToolRegistry
from backend.tools_graph import register_graph_tools
from graphlink_settings_store import SettingsManager

MODEL = "fal-ai/model/transcribe"
RID = "764cabcf-b745-4b3e-ae38-1200304cf45b"
TOOL = "graph.attach_fal_result"
RESULT = {"text": "A completed transcript", "audio_file": {"url": "https://v3.fal.media/out/sound.wav"}}


def setup_registry(tmp_path):
    settings = SettingsManager(tmp_path / "settings.json")
    doc = SceneDocument()
    registry = ToolRegistry()
    register_graph_tools(registry, doc, settings)
    return settings, doc, registry


def invoke(registry, *, scopes=(GRAPH_MUTATE, PROVIDER_CALL), approve=True, **extra):
    async def decision(_call):
        return approve

    ctx = RunContext(granted_scopes=frozenset(scopes), request_approval=decision)
    setattr(ctx, "run_id", "builder-run")
    call = ToolCall(id="attach", name=TOOL, arguments={"model_id": MODEL, "request_id": RID, **extra})
    return asyncio.run(registry.invoke(call, ctx))


def test_saved_job_placed_under_parent_once_with_history_and_undo(tmp_path, monkeypatch):
    manager, doc, registry = setup_registry(tmp_path)
    manager.upsert_fal_job(MODEL, RID, "COMPLETED", RESULT)
    monkeypatch.setattr(FalClient, "status", lambda *a: pytest.fail("Fetched a stored job"))
    parent = doc.add_chat_node(30, 40, "Source", True)
    args = {"parent_id": parent.id}
    response = invoke(registry, **args)
    assert not response.is_error, response.content
    node_id = json.loads(response.content)["node_id"]
    node = doc.nodes[node_id]
    assert node.kind == "fal_media"
    assert node.y > parent.y
    assert any(edge.target == node_id and edge.source == parent.id for edge in doc.edges.values())
    media = read_media(node)
    assert (media["modelId"], media["requestId"], media["status"]) == (MODEL, RID, "completed")
    assert media["history"][0]["outputs"] == [{"kind": "audio", "url": RESULT["audio_file"]["url"]}]
    assert RESULT["text"] in media["history"][0]["resultPreview"]
    repeated = invoke(registry, **args)
    assert json.loads(repeated.content)["node_id"] == node_id
    doc.undo()
    assert list(doc.nodes) == [parent.id]
    assert not doc.edges


@pytest.mark.parametrize("stored", ["absent", "truncated"])
def test_result_is_retrieved_for_missing_or_truncated_snapshot(tmp_path, monkeypatch, stored):
    manager, doc, registry = setup_registry(tmp_path)
    if stored == "truncated":
        manager.upsert_fal_job(MODEL, RID, "COMPLETED", {"text": "x" * 40000})
    looked_up = []

    def status(client, model, rid):
        looked_up.append(("status", model, rid))
        return {"status": "COMPLETED"}

    def result(client, model, rid):
        looked_up.append(("result", model, rid))
        return {"text": "Recovered output"}

    monkeypatch.setattr(FalClient, "submit", lambda *a: pytest.fail("Paid submission"))
    monkeypatch.setattr(FalClient, "run", lambda *a: pytest.fail("Paid synchronous run"))
    monkeypatch.setattr(FalClient, "status", status)
    monkeypatch.setattr(FalClient, "result", result)
    response = invoke(registry)
    assert not response.is_error, response.content
    node = doc.nodes[json.loads(response.content)["node_id"]]
    assert "Recovered output" in read_media(node)["history"][0]["resultPreview"]
    assert not doc.edges
    assert looked_up == [("status", MODEL, RID), ("result", MODEL, RID)]


@pytest.mark.parametrize("state", [{"status": "IN_QUEUE"}, {"status": "COMPLETED", "error": "failure"}])
def test_active_and_failed_jobs_do_not_create_nodes(tmp_path, monkeypatch, state):
    _, doc, registry = setup_registry(tmp_path)
    monkeypatch.setattr(FalClient, "status", lambda *_: state)
    monkeypatch.setattr(FalClient, "result", lambda *_: pytest.fail("No successful result"))
    assert invoke(registry).is_error
    assert not doc.nodes


def test_gates_and_invalid_job_or_parent_precede_network_or_graph_edits(tmp_path, monkeypatch):
    manager, doc, registry = setup_registry(tmp_path)
    manager.upsert_fal_job(MODEL, RID, "COMPLETED", RESULT)
    monkeypatch.setattr(FalClient, "status", lambda *_: pytest.fail("Unexpected network request"))
    assert registry.scopes_for(TOOL) == {GRAPH_MUTATE, PROVIDER_CALL}
    assert invoke(registry, approve=False).is_error
    assert invoke(registry, scopes=(GRAPH_MUTATE,)).is_error
    assert invoke(registry, parent_id="missing").is_error
    assert invoke(registry, model_id="fal-ai/another").is_error
    assert invoke(registry, request_id="not a UUID").is_error
    assert not doc.nodes
