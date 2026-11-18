"""Inspector projections are historical, dependency ordered, and safe to export."""

import asyncio
import json

from backend import chat_library
from backend.canvas import SceneDocument
from backend.domain.graph_run_inspector import build_graph_run_trace
from backend.events import SessionBus


DIGEST = "a" * 64
SECRET_KEY = "DO_NOT_EXPORT_CREDENTIAL_k9X"
SECRET_PROMPT = "DO_NOT_EXPORT_PROMPT_private_instruction"
SECRET_OUTPUT = "DO_NOT_EXPORT_OUTPUT_private_response"


def test_trace_exports_only_allowlisted_metadata_in_dependency_order():
    upstream = {
        "nodeId": "model1", "sourceId": "source1", "kind": "model",
        "operation": "model", "status": "reconciliation-required",
        "label": SECRET_PROMPT, "detail": SECRET_OUTPUT,
        "prompt": SECRET_PROMPT, "outputText": SECRET_OUTPUT, "apiKey": SECRET_KEY,
        "inputHash": DIGEST, "configHash": DIGEST, "targetHash": DIGEST,
        "startedAt": "2026-11-18T09:00:00+00:00",
        "finishedAt": "2026-11-18T09:00:02+00:00",
        "externalAttempt": {
            "requestId": "graph-" + "1" * 32, "kind": "model",
            "provider": "Ollama", "model": "qwen3:8b", "tool": "",
            "inputRef": "private:credential-string", "inputHash": DIGEST,
            "phase": "dispatched",
        },
    }
    downstream = {
        "nodeId": "note1", "sourceId": "model1", "kind": "note",
        "operation": "copy", "status": "blocked",
        "detail": "The upstream step is blocked. " + SECRET_KEY,
        "label": SECRET_OUTPUT, "response": SECRET_OUTPUT,
        "inputHash": DIGEST, "outputHash": DIGEST, "outputVersion": 7,
    }
    run = {
        "runId": "saved-run-1", "status": "reconciliation-required",
        "startedAt": "2026-11-18T09:00:00+00:00",
        "finishedAt": "2026-11-18T09:00:03+00:00",
        "token": SECRET_KEY, "steps": [downstream, upstream],
    }

    trace = build_graph_run_trace(run)
    exported_json = json.dumps(trace, sort_keys=True)
    for forbidden in (SECRET_KEY, SECRET_PROMPT, SECRET_OUTPUT, "private:credential-string",
                      "apiKey", "prompt", "outputText", "response", "label", "detail"):
        assert forbidden not in exported_json
    assert trace["schemaVersion"] == 1
    assert [s["destinationNodeId"] for s in trace["steps"]] == ["model1", "note1"]
    assert trace["steps"][0]["model"] == "qwen3:8b"
    assert trace["steps"][0]["durationMs"] == 2000
    attempt, = trace["steps"][0]["attempts"]
    assert attempt["requestId"] == "graph-" + "1" * 32
    assert attempt["persistedPhase"] == "dispatched"
    assert attempt["phase"] == "reconciliation-required"
    assert trace["steps"][1]["blockedCause"] == "An upstream dependency was blocked, stale or incomplete."
    assert trace["steps"][1]["outputVersion"] == {"sha256": DIGEST, "number": 7}
    assert trace["durationMs"] == 3000


def test_untrusted_timestamps_and_metadata_cannot_become_export_text():
    trace = build_graph_run_trace({
        "runId": "safe-run-2", "status": "completed",
        "startedAt": "2026-11-18T00:00:00+00:00",
        "finishedAt": "2026-11-18T00:00:00+00:00 " + SECRET_KEY,
        "steps": [{
            "nodeId": "target", "sourceId": "source", "status": "blocked",
            "kind": "note", "operation": "copy",
            "detail": SECRET_PROMPT, "modelId": SECRET_KEY, "outputVersion": SECRET_OUTPUT,
            "inputHash": SECRET_OUTPUT, "startedAt": SECRET_KEY,
        }],
    })
    text = json.dumps(trace)
    assert SECRET_KEY not in text and SECRET_PROMPT not in text and SECRET_OUTPUT not in text
    assert trace["finishedAt"] is None
    assert trace["steps"][0]["startedAt"] is None
    assert trace["steps"][0]["outputVersion"]["number"] is None


def test_get_graph_run_trace_survives_sqlite_reopen_with_timing_and_output_version(tmp_path):
    async def scenario():
        path = tmp_path / "graphs.db"
        doc = SceneDocument()
        bus = SessionBus("inspector")
        bus.register_topic("scene", doc.scene_payload)
        chat_library.register_chat_library(bus, path, doc, autosave_interval_seconds=None)
        source = doc.add_table(0, 0, "Team,Hours\nExample,1")
        report = doc.create_table_output(source.id, "artifact", "Team", "Hours")
        note = doc.add_note(0, 0)
        doc.bind_note_text_input(report.id, note.id)
        doc.set_table_source(source.id, "Team,Hours\n" + SECRET_OUTPUT + ",8", source.content)

        async def send(intent, *args):
            return await bus.dispatch_intent("scene", intent, list(args))

        preview = await send("previewGraphRun", [])
        result = await send("executeGraphRun", [], preview["previewToken"], "inspect-saved-1")
        trace = result["trace"]
        assert trace["status"] == "completed"
        assert [s["destinationNodeId"] for s in trace["steps"]] == [report.id, note.id]
        assert [s["status"] for s in trace["steps"]] == ["completed", "completed"]
        assert trace["steps"][0]["outputVersion"]["number"] == 2
        assert trace["steps"][0]["outputVersion"]["sha256"]
        assert all(s["startedAt"] and s["finishedAt"] and s["durationMs"] is not None for s in trace["steps"])
        assert trace["startedAt"] and trace["finishedAt"] and trace["durationMs"] is not None
        assert SECRET_OUTPUT not in json.dumps(trace)

        fresh_doc = SceneDocument()
        fresh_bus = SessionBus("inspector-reopened")
        fresh_bus.register_topic("scene", fresh_doc.scene_payload)
        chat_library.register_chat_library(fresh_bus, path, fresh_doc, autosave_interval_seconds=None)
        await fresh_bus.dispatch_intent("app-chat-library", "loadChat", [doc.current_chat_id])
        persisted = await fresh_bus.dispatch_intent("scene", "getGraphRun", [])
        assert persisted["trace"]["steps"][0]["outputVersion"]["number"] == 2
        assert persisted["trace"]["steps"][0]["durationMs"] is not None
        assert persisted["trace"]["steps"][0]["destinationNodeId"] in fresh_doc.nodes
        assert SECRET_OUTPUT not in json.dumps(persisted["trace"])

    asyncio.run(scenario())
