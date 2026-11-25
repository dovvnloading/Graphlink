"""Regression tests for lightweight operator history reads."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from backend.chat_library import get_default_workspace_id
from backend.workflow.api import WorkflowService
from backend.workflow.detached_store import DetachedRunStore


def test_history_summaries_exclude_payloads_and_preserve_retention(tmp_path):
    db = tmp_path / "chats.db"
    ws = get_default_workspace_id(db)
    store = DetachedRunStore(db)
    now = datetime.now(timezone.utc)
    large = "x" * (32 * 1024)
    rows = []
    for index in range(54):
        created = (now - timedelta(minutes=index)).isoformat()
        if index == 52:
            created = (now - timedelta(days=91)).isoformat()
        status = "running" if index in (0, 1, 53) else "completed"
        record = {
            "run_id": f"run-{index}", "status": status, "created_at": created,
            "spec": {"name": f"Workflow {index}", "config": large},
            "cost_usd": 0.1, "preset_id": "p1", "started_at": created, "finished_at": None,
            "inputs": {"input": {"kind": "text", "text": large}},
            "outputs": {"output": {"kind": "text", "text": large}},
            "steps": {"one": {"approval": {"status": "pending" if index == 1 else "approved"},
                              "outputs": {"output": {"kind": "text", "text": large}}}},
            "events": [{"data": large}],
            "awaiting_approval": {"nonce": "secret"} if index == 0 else None,
        }
        rows.append((record["run_id"], ws, created, status, json.dumps(record)))
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executemany("INSERT INTO detached_workflow_runs VALUES (?, ?, ?, ?, ?)", rows)

    summaries = store.list_summaries(ws)
    assert [row["run_id"] for row in summaries] == [f"run-{index}" for index in range(50)] + ["run-53"]
    assert [row["run_id"] for row in summaries if row["waiting_for_approval"]] == ["run-0", "run-1"]
    assert summaries[0]["spec"] == {"name": "Workflow 0"}
    assert summaries[0]["preset_id"] == "p1"
    assert len(json.dumps(summaries).encode()) < 25_000
    assert all(set(row) == {"run_id", "status", "created_at", "preset_id", "spec", "started_at",
                            "finished_at", "cost_usd", "waiting_for_approval"} for row in summaries)
    assert store.list_summaries(ws + 1) == []
    assert store.read_visible(ws, "run-50") is None
    assert store.read_visible(ws, "run-52") is None
    assert store.read_visible(ws + 1, "run-0") is None
    assert store.read_visible(ws, "run-53")["status"] == "running"
    assert store.read_visible(ws, "run-0")["inputs"]["input"]["text"] == large
    # Read-only history must not physically delete expired/excess records.
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT count(*) FROM detached_workflow_runs").fetchone()[0] == 54
    assert [row["run_id"] for row in store.list(ws)] == [row["run_id"] for row in summaries]


def test_preset_summaries_keep_display_metadata_without_requests(tmp_path):
    db = tmp_path / "chats.db"
    ws = get_default_workspace_id(db)
    automation = WorkflowService(db).automation
    presets = [
        {"preset_id": "local", "name": "Local", "workflow_id": "w", "cost_usd": 0,
         "allowed_effects": ["pure"], "trigger": None, "request": {"spec": {"name": "Local workflow"},
                                                                  "parameters": {"text": "x" * 900_000}}},
        {"preset_id": "scheduled", "name": "Scheduled", "workflow_id": "w", "cost_usd": 1,
         "allowed_effects": ["cloud", "pure"], "trigger": {"kind": "interval", "seconds": 60},
         "request": {"workflow_id": "w", "inputs": {"text": "y" * 900_000}}},
    ]
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executemany("INSERT INTO workflow_presets VALUES (?, ?, ?)",
                         [(preset["preset_id"], ws, json.dumps(preset)) for preset in presets])
    summaries = automation.list_summaries(ws)
    assert summaries == [
        {"preset_id": "local", "name": "Local", "workflow_id": "w", "workflow_name": "Local workflow",
         "cost_usd": 0, "allowed_effects": ["pure"], "trigger": None},
        {"preset_id": "scheduled", "name": "Scheduled", "workflow_id": "w", "workflow_name": None,
         "cost_usd": 1, "allowed_effects": ["cloud", "pure"], "trigger": {"kind": "interval", "seconds": 60}},
    ]
    assert len(json.dumps(summaries).encode()) < 1000
    assert automation.list_summaries(ws + 1) == []
    assert automation.list(ws) == presets
