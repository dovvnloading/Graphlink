"""Durable workflow receipts, including a hard-kill integration test."""

from __future__ import annotations

import asyncio
import copy
import json
import multiprocessing
import os
import sqlite3
import threading
from contextlib import closing

import pytest

from backend import chat_library, db_backup, knowledge_store
from backend.api import intents_graph_model
from backend.canvas import SceneDocument
from backend.events import SessionBus
from graphlink_task_config import LOCAL_PROVIDER_OLLAMA


def _plan():
    return {
        "runId": "durable-example", "previewHash": "a" * 64, "selectionHash": "b" * 64,
        "startedAt": "2026-11-24T10:00:00Z", "status": "running", "maxConcurrency": 1,
        "steps": [{
            "nodeId": "n2", "sourceId": "n1", "kind": "note", "operation": "copy",
            "status": "pending", "label": "private title", "detail": "private input body",
            "inputHash": "c" * 64, "configHash": "d" * 64, "targetHash": "e" * 64,
        }],
    }


def test_migration_checkpoint_rollback_order_and_backup(tmp_path):
    path = tmp_path / "chats.db"
    plan = _plan()
    graph_id, updated_at = chat_library.save_chat_atomically_row(
        path, None, "Workflow", {"graph_runs": [plan]}, [], [],
    )
    # Simulate a v5 DB with the same graph JSON, but no execution tables.
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("DROP TABLE workflow_events")
        conn.execute("DROP TABLE workflow_steps")
        conn.execute("DROP TABLE workflow_runs")
        conn.execute("PRAGMA user_version = 5")
    first = chat_library.load_workflow_run(path, graph_id, plan["runId"])
    assert first["status"] == "running"
    assert [event["status"] for event in first["events"]] == ["running", "pending"]
    assert json.loads(first["steps"][0]["input_ref"])["sha256"] == "c" * 64
    assert "private" not in json.dumps(first)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == chat_library.CHATS_DB_SCHEMA_VERSION
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    completed = copy.deepcopy(plan)
    completed.update(status="completed", finishedAt="2026-11-24T10:00:02Z")
    completed["steps"][0].update(
        status="completed", startedAt=plan["startedAt"], finishedAt=completed["finishedAt"],
        outputHash="f" * 64, outputVersion=2,
    )
    # The receipt INSERT/UPDATE precedes the notes write. A later failure
    # must roll back graph, receipt and event together.
    with pytest.raises(ValueError):
        chat_library.save_chat_atomically_row(
            path, graph_id, "Workflow", {"graph_runs": [completed]},
            [{"position": {"x": "not a number"}}], [], expected_updated_at=updated_at,
        )
    assert chat_library.load_workflow_run(path, graph_id, plan["runId"]) == first
    assert chat_library.load_chat_row(path, graph_id)["data"]["graph_runs"] == [plan]

    # Keep a second connection open to exercise the WAL-safe online backup.
    with closing(chat_library._connect(path)) as keeper:
        assert keeper.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        chat_library.save_chat_atomically_row(path, graph_id, "Workflow", {"graph_runs": [completed]}, [], [])
        finished = chat_library.load_workflow_run(path, graph_id, plan["runId"])
        step, = finished["steps"]
        assert step["duration_ms"] == 2000
        assert json.loads(step["output_ref"]) == {"nodeId": "n2", "sha256": "f" * 64, "version": 2}
        assert [event["status"] for event in finished["events"]] == ["running", "pending", "completed", "completed"]
        ids = [event["event_id"] for event in finished["events"]]
        assert ids == sorted(set(ids))
        # Identical saves are idempotent, not duplicate log entries.
        chat_library.save_chat_atomically_row(path, graph_id, "Workflow", {"graph_runs": [completed]}, [], [])
        assert chat_library.load_workflow_run(path, graph_id, plan["runId"]) == finished
        backup = db_backup.take_backup(path)
        assert backup is not None
    # A stale snapshot must not reset a finished step, even via a blind save.
    with pytest.raises(ValueError, match="cannot be restarted"):
        chat_library.save_chat_atomically_row(path, graph_id, "Workflow", {"graph_runs": [plan]}, [], [])
    assert chat_library.load_workflow_run(path, graph_id, plan["runId"]) == finished
    # The graph's bounded plan history may prune a finished entry, while
    # durable receipts remain available by run ID.
    chat_library.save_chat_atomically_row(path, graph_id, "Workflow", {}, [], [])
    assert chat_library.load_workflow_run(path, graph_id, plan["runId"]) == finished
    assert db_backup.restore_from_newest_backup(path, required_table="graphs") == backup
    assert chat_library.load_workflow_run(path, graph_id, plan["runId"]) == finished
    assert chat_library.load_chat_row(path, graph_id)["data"]["graph_runs"] == [completed]
    assert chat_library.delete_chat(path, graph_id)
    assert chat_library.load_workflow_run(path, graph_id, plan["runId"]) is None
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM workflow_steps").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM workflow_events").fetchone()[0] == 0


def _crash_worker(path, reached):
    """Run the real engine in a separate process, with fake local transport."""
    os.environ["OLLAMA_HOST"] = "http://127.0.0.1:11434"
    knowledge_store.DEFAULT_DB_PATH = path.parent / "child-knowledge.db"

    async def scenario():
        doc = SceneDocument()
        bus = SessionBus("workflow-crash-child")
        bus.register_topic("scene", doc.scene_payload)
        chat_library.register_chat_library(bus, path, doc, autosave_interval_seconds=None)
        for _ in range(3):
            doc.remove_nodes([doc.add_note(0, 0).id])
        source = doc.add_note(0, 0)
        doc.set_note_content(source.id, "private prompt")
        first = doc.add_model_node(source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        second = doc.add_model_node(first.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        output = doc.add_note(0, 0)
        doc.bind_note_text_input(second.id, output.id, "word_count")

        def transport(**kwargs):
            if kwargs["prompt"] == "private prompt":
                return {"message": {"content": "private first result"}, "prompt_eval_count": 11, "eval_count": 7}
            assert kwargs["prompt"] == "private first result"
            receipt = chat_library.load_workflow_run(path, doc.current_chat_id, "kill-mid-run")
            assert [s["status"] for s in receipt["steps"]] == ["completed", "running", "pending"]
            assert receipt["steps"][1]["attempt_phase"] == "dispatched"
            reached.set()
            # The parent kills this process before the external response.
            threading.Event().wait(180)
            raise AssertionError("parent did not kill process")

        intents_graph_model.local_ollama_complete = transport
        preview = await bus.dispatch_intent("scene", "previewGraphRun", [[]])
        await bus.dispatch_intent("scene", "executeGraphRun", [[], preview["previewToken"], "kill-mid-run", 1])

    asyncio.run(scenario())


def test_kill_mid_run_preserves_finished_and_uncertain_external_steps(tmp_path, monkeypatch):
    path = tmp_path / "chats.db"
    context = multiprocessing.get_context("spawn")
    reached = context.Event()
    child = context.Process(target=_crash_worker, args=(path, reached))
    child.start()
    try:
        assert reached.wait(90), "child did not reach its durably fenced second model call"
        child.kill()
    finally:
        if child.is_alive():
            child.kill()
        child.join(15)
    assert child.exitcode is not None and child.exitcode != 0
    with closing(sqlite3.connect(path)) as conn:
        graph_id, = conn.execute("SELECT id FROM graphs").fetchone()
    receipt = chat_library.load_workflow_run(path, graph_id, "kill-mid-run")
    first, second, pending = receipt["steps"]
    assert first["status"] == "completed"
    assert (first["input_tokens"], first["output_tokens"], first["total_tokens"]) == (11, 7, 18)
    assert first["cost_usd"] == 0 and first["approval"] == "reviewed"
    assert first["model"] == "qwen3:8b" and first["provider"] == LOCAL_PROVIDER_OLLAMA
    assert first["duration_ms"] is not None and first["attempt_phase"] == "completed"
    assert second["status"] == "running" and second["attempt_phase"] == "dispatched"
    assert pending["status"] == "pending"
    assert "private prompt" not in json.dumps(receipt) and "private first result" not in json.dumps(receipt)

    calls = []
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setattr(intents_graph_model, "local_ollama_complete", lambda **kw: calls.append(kw))

    async def reopen():
        doc = SceneDocument()
        bus = SessionBus("workflow-crash-reopened")
        bus.register_topic("scene", doc.scene_payload)
        chat_library.register_chat_library(bus, path, doc, autosave_interval_seconds=None)
        await bus.dispatch_intent("app-chat-library", "loadChat", [graph_id])
        run = await bus.dispatch_intent("scene", "getGraphRun", [])
        assert run["status"] == "reconciliation-required"
        assert [s["status"] for s in run["steps"]] == ["completed", "reconciliation-required", "pending"]
        assert doc.nodes[run["steps"][0]["nodeId"]].content == "private first result"
        assert run["steps"][0]["inputTokens"] == 11
        for _ in range(2):
            result = await bus.dispatch_intent("scene", "resumeGraphRun", ["kill-mid-run"])
            assert result["status"] == "reconciliation-required"
            assert result["steps"][0]["status"] == "completed"
            assert result["steps"][1]["status"] == "reconciliation-required"
            assert result["steps"][2]["status"] == "blocked"
        assert not calls and not doc.command_log

    asyncio.run(reopen())
    after = chat_library.load_workflow_run(path, graph_id, "kill-mid-run")
    assert [s["request_id"] for s in after["steps"]] == [s["request_id"] for s in receipt["steps"]]
    assert after["steps"][0]["attempt_phase"] == "completed"
    assert after["steps"][1]["attempt_phase"] in {"dispatched", "reconciliation-required"}
