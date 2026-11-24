"""Independent regression coverage for replay, cancellation and durable receipts."""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from dataclasses import asdict

import pytest

from backend.chat_library import get_default_workspace_id
from backend.tests.test_detached_workflow_engine import _engine, _entry, _spec, _until
from backend.workflow.contracts import TextValue
from backend.workflow.engine import UnattendedPolicy, WorkflowEngine
from backend.workflow.step_services import WorkflowRequestJournal
from backend.workflow.adapter import StepContext


def test_operation_fence_survives_pruning_and_restart(tmp_path):
    async def scenario():
        path = tmp_path / "chats.db"
        engine, ws, *_ = _engine(path)
        spec = _spec(engine.registry, [_entry("cloud", "cloud")])
        params = {"text": TextValue("seed")}
        policy = UnattendedPolicy(engine.preview(spec, params).fingerprint, frozenset({"cloud"}), 1)
        run_id = engine.start(ws, spec, params, unattended=policy, preset_id="preset", operation_id="operation-1")
        record = await engine.wait(ws, run_id)
        assert record["status"] == "completed"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("UPDATE detached_workflow_runs SET created_at=?",
                         ((datetime.now(timezone.utc) - timedelta(days=91)).isoformat(),))
        assert engine.store.list(ws) == []
        reopened, _, _, cloud, *_ = _engine(path)
        assert reopened.store.operation_run(ws, "operation-1", "preset") == run_id
        assert reopened.start(ws, spec, params, unattended=policy, preset_id="preset",
                              operation_id="operation-1") == run_id
        assert not cloud.started
        assert reopened.store.list(ws) == []
        with pytest.raises(ValueError, match="another preset"):
            reopened.start(ws, spec, params, unattended=policy, preset_id="other", operation_id="operation-1")

    asyncio.run(scenario())


def test_operation_migration_backfills_existing_receipts(tmp_path):
    async def scenario():
        path = tmp_path / "chats.db"
        engine, ws, *_ = _engine(path)
        spec = _spec(engine.registry, [])
        params = {"text": TextValue("seed")}
        policy = UnattendedPolicy(engine.preview(spec, params).fingerprint, frozenset({"pure"}), 1)
        run_id = engine.start(ws, spec, params, unattended=policy, preset_id="preset", operation_id="operation-1")
        await engine.wait(ws, run_id)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("DROP TABLE detached_workflow_operations")
            conn.execute("PRAGMA user_version=9")
        reopened, *_ = _engine(path)
        assert reopened.store.operation_run(ws, "operation-1", "preset") == run_id
        assert reopened.store.read(ws, run_id)["status"] == "completed"

    asyncio.run(scenario())


def test_cancelled_workflow_retains_ownership_until_adapter_settles(tmp_path, monkeypatch):
    async def scenario():
        engine, ws, _, cloud, *_ = _engine(tmp_path / "chats.db")
        entered, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def resistant(prepared, context):
            entered.set()
            context.report_cost(0.01)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                draining.set()
                await release.wait()
            return {"text": TextValue("late")}

        monkeypatch.setattr(cloud, "run", resistant)
        spec = _spec(engine.registry, [_entry("paid", "cloud")])
        params = {"text": TextValue("seed")}

        def start():
            return engine.start(ws, spec, params, approval_token=engine.approve(ws, engine.preview(spec, params)))

        run_id = start()
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert engine.cancel(ws, run_id)
            record = await asyncio.wait_for(engine.wait(ws, run_id), 3)
            await asyncio.wait_for(draining.wait(), 3)
            assert record["status"] == "cancelled"
            assert record["cost_usd"] == 0.25
            with pytest.raises(ValueError, match="active run"):
                start()
            assert record == engine.store.read(ws, run_id)
        finally:
            release.set()
        await _until(lambda: not engine._draining)
        assert record == engine.store.read(ws, run_id)

    asyncio.run(scenario())


def test_recovery_accounts_for_reserved_and_reported_cost(tmp_path, monkeypatch):
    async def scenario():
        path = tmp_path / "chats.db"
        engine, ws, _, cloud, *_ = _engine(path)
        entered, release = asyncio.Event(), asyncio.Event()

        async def run(prepared, context):
            context.report_cost(0.01)
            entered.set()
            await release.wait()
            return {"text": TextValue("late")}

        monkeypatch.setattr(cloud, "run", run)
        spec = _spec(engine.registry, [_entry("paid", "cloud")])
        params = {"text": TextValue("seed")}
        run_id = engine.start(ws, spec, params, approval_token=engine.approve(ws, engine.preview(spec, params)))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            record = engine.store.read(ws, run_id)
            assert record["steps"]["paid"]["cost_usd"] == 0.25
        finally:
            release.set()
            await engine.wait(ws, run_id)
        # Simulate the last durable pre-crash record, including an older
        # version whose running step still held zero rather than a reservation.
        record["steps"]["paid"]["cost_usd"] = 0.0
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("UPDATE detached_workflow_runs SET status='running', payload=? WHERE run_id=?",
                         (json.dumps(record), run_id))
        reopened = WorkflowEngine(path, registry=engine.registry)
        recovered = reopened.store.read(ws, run_id)
        assert recovered["status"] == "interrupted"
        assert recovered["steps"]["paid"]["cost_usd"] == 0.25
        assert recovered["cost_usd"] == 0.25

    asyncio.run(scenario())


def test_terminal_external_receipt_rejects_conflicting_redelivery(tmp_path):
    engine, _, *_ = _engine(tmp_path / "chats.db")
    spec = _spec(engine.registry, [_entry("paid", "cloud")])
    journal = WorkflowRequestJournal(tmp_path / "requests.sqlite")
    context = StepContext("run", "paid", 1)
    step = {"status": "running", "inputHash": "a" * 64,
            "externalAttempt": {"requestId": "graph-" + "b" * 32, "kind": "model", "phase": "dispatched"}}
    assert journal.checkpoint(context, spec, step, None)
    complete = copy.deepcopy(step)
    complete["status"] = "completed"
    complete["externalAttempt"]["phase"] = "completed"
    output = {"text": asdict(TextValue("original"))}
    assert journal.checkpoint(context, spec, complete, output)
    assert journal.checkpoint(context, spec, complete, output)
    with pytest.raises(RuntimeError, match="cannot be overwritten"):
        journal.checkpoint(context, spec, complete, {"text": asdict(TextValue("conflict"))})
    with pytest.raises(RuntimeError, match="cannot be overwritten"):
        journal.checkpoint(context, spec, step, None)
    assert journal.read("run", "paid") == (complete, output)


def test_preset_approval_nonce_not_retained_in_event_history(tmp_path):
    async def scenario():
        engine, ws, *_ = _engine(tmp_path / "chats.db")
        spec = _spec(engine.registry, [_entry("write", "writes")])
        params = {"text": TextValue("seed")}
        policy = UnattendedPolicy(engine.preview(spec, params).fingerprint, frozenset({"pure"}), 1)
        run_id = engine.start(ws, spec, params, unattended=policy)
        await _until(lambda: engine.store.read(ws, run_id)["awaiting_approval"] is not None)
        pending = engine.store.read(ws, run_id)["awaiting_approval"]
        engine.decide_step(ws, run_id, "write", pending["nonce"], approved=False)
        record = await engine.wait(ws, run_id)
        assert record["awaiting_approval"] is None
        assert pending["nonce"] not in json.dumps(record)

    asyncio.run(scenario())


def test_fingerprint_approval_requires_preview_in_same_workspace(tmp_path):
    engine, ws, *_ = _engine(tmp_path / "chats.db")
    spec = _spec(engine.registry, [_entry("paid", "cloud")])
    params = {"text": TextValue("seed")}
    preview = engine.preview(spec, params, workspace_id=ws)
    with pytest.raises(ValueError, match="Preview"):
        engine.approve_fingerprint(ws + 1, preview.fingerprint)
    assert engine.approve_fingerprint(ws, preview.fingerprint)
