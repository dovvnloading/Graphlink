"""Fault assurance at the detached scheduler and durable external fence.

No provider, shell, or real external write is used. The fsynced ledger is the
independent stand-in for the remote system, not a count inferred from receipts.
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from backend.asset_store import content_ref
from backend.chat_library import get_default_workspace_id
from backend.tests.test_detached_workflow_engine import TEXT, _entry, _spec
from backend.workflow.artifacts import INLINE_LIMIT, canonical, digest
from backend.workflow.contracts import PortSet, StepOutputSource, TextValue
from backend.workflow.engine import RunLimits, WorkflowEngine
from backend.workflow.planning import StepPolicy
from backend.workflow.registry import StepAdapterRegistry


PARAMS = {"text": TextValue("seed")}
PREFIX = ["paid", "write"]


class LedgerAdapter:
    def __init__(self, ledger, effect, entered=None, release=None):
        self.ledger = ledger
        self.side_effect_class = effect
        self.entered = entered
        self.release = release
        self.cancelled = []

    def ports(self, config_json):
        return PortSet((TEXT,), (TEXT,))

    def prepare(self, spec, inputs):
        return json.loads(spec.config_json), inputs["text"].text

    def fingerprint(self, prepared):
        return digest(prepared)

    def preview(self, prepared):
        return {}

    async def run(self, prepared, context):
        config, text = prepared
        with self.ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps([context.step_id, context.attempt]) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if self.side_effect_class == "cloud":
            context.report_cost(0.25)
        if config.get("die"):
            os._exit(73)  # deliberately bypass all shutdown/checkpoint hooks
        if config.get("slow"):
            self.entered.set()
            if self.release is None:
                await asyncio.Event().wait()
            elif not await asyncio.to_thread(self.release.wait, 15):
                raise TimeoutError("test did not release adapter")
        return {"text": TextValue(text + config.get("suffix", ""))}

    async def cancel(self, context):
        self.cancelled.append(context.step_id)


def _registry(path, entered=None, release=None):
    registry = StepAdapterRegistry()
    ledger = path.with_suffix(".effects")
    for name in ("cloud", "writes"):
        registry.register(name, "1", LedgerAdapter(ledger, name, entered, release))
    return registry


def _policy(step):
    if step.adapter.side_effect_class == "cloud":
        return StepPolicy("cloud", ("scripted", "fixture-v1"), 0.25)
    return StepPolicy()


def _engine(path, *, limits=None, entered=None):
    return WorkflowEngine(path, registry=_registry(path, entered), policy_for=_policy, limits=limits)


def _chain(registry, *, tail=False, **fault):
    entries = [
        _entry("paid", "cloud", suffix=" paid"),
        _entry("write", "writes", source=StepOutputSource("paid", "text"), suffix=" written"),
    ]
    if tail:
        entries.extend([
            _entry("fault", "cloud", source=StepOutputSource("write", "text"), **fault),
            _entry("never", "writes", source=StepOutputSource("fault", "text")),
        ])
    return _spec(registry, entries, output=StepOutputSource("write", "text"))


def _start(engine, ws, spec):
    preview = engine.preview(spec, PARAMS)
    return engine.start(ws, spec, PARAMS, approval_token=engine.approve(ws, preview))


async def _wait(engine, ws, run_id):
    return await asyncio.wait_for(engine.wait(ws, run_id), 15)


def _effects(path):
    return [tuple(json.loads(line)) for line in path.with_suffix(".effects").read_text().splitlines()]


def _assert_assurance(engine, ws, run_id, expected, *, before=None):
    record = engine.store.read(ws, run_id)
    for step_id, text in (("paid", "seed paid"), ("write", "seed paid written")):
        step = record["steps"][step_id]
        assert step["status"] == "completed"
        assert engine.artifacts.unpack(step["outputs"]["text"]) == TextValue(text)
        assert sum(e["kind"] == "step-completed" and e["step_id"] == step_id
                   for e in record["events"]) == 1
        if before is not None:
            assert step == before["steps"][step_id]
    assert record["steps"]["paid"]["cost_usd"] == 0.25
    assert _effects(engine.store.db_path) == [(step, 1) for step in expected]
    assert [e["sequence"] for e in record["events"]] == list(range(len(record["events"])))
    return record


def _crash_child(path):
    async def scenario():
        engine = _engine(path)
        ws = get_default_workspace_id(path)
        run_id = _start(engine, ws, _chain(engine.registry, tail=True, die=True))
        await engine.wait(ws, run_id)

    asyncio.run(scenario())


def test_process_death_after_remote_dispatch(tmp_path):
    path = tmp_path / "chats.db"
    child = multiprocessing.get_context("spawn").Process(target=_crash_child, args=(path,))
    child.start()
    try:
        child.join(90)
        assert child.exitcode == 73, "worker did not die at the injected dispatch boundary"
    finally:
        if child.is_alive():
            child.kill()
        child.join(15)
    ws = get_default_workspace_id(path)
    observer = WorkflowEngine(path, registry=_registry(path), policy_for=_policy, recover=False)
    before, = observer.store.list(ws)
    assert before["steps"]["fault"]["status"] == "running"
    assert before["steps"]["never"]["status"] == "pending"
    run_id = before["run_id"]
    for _ in range(2):
        reopened = _engine(path)
        record = _assert_assurance(reopened, ws, run_id, PREFIX + ["fault"], before=before)
        assert record["status"] == "interrupted"
        assert record["steps"]["fault"]["status"] == "interrupted"
        assert record["steps"]["never"]["status"] == "interrupted"
        assert asyncio.run(reopened.wait(ws, run_id)) == record


def test_websocket_disconnect_during_detached_run(tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import create_app

    path = tmp_path / "chats.db"
    ws_id = get_default_workspace_id(path)
    entered, release = threading.Event(), threading.Event()
    app = create_app(spa_dir=tmp_path / "missing-spa", chat_db_path=path,
                     settings_state_file=tmp_path / "session.dat",
                     allow_unauthenticated_for_tests=True)
    service = app.state.workflows
    service.registry = _registry(path, entered, release)
    service.policy_for = _policy
    spec = _chain(service.registry, tail=True, slow=True)
    request = {"spec": asdict(spec), "parameters": {"text": asdict(PARAMS["text"])}}
    prefix = f"/api/workflows/{ws_id}"
    with TestClient(app, base_url="http://127.0.0.1", headers={"host": "127.0.0.1"}) as client:
        try:
            with client.websocket_connect("/ws?session=default") as socket:
                socket.send_json({"kind": "subscribe", "topics": ["system"]})
                assert socket.receive_json()["kind"] == "state"
                preview = client.post(prefix + "/preview", json=request)
                assert preview.status_code == 200
                approval = client.post(prefix + "/approve", json={
                    "approved": True, "fingerprint": preview.json()["fingerprint"],
                })
                assert approval.status_code == 200
                request["approval_token"] = approval.json()["approval_token"]
                start = client.post(prefix + "/runs", json=request)
                assert start.status_code == 202
                run_id = start.json()["run_id"]
                assert entered.wait(10)
                before = client.get(prefix + "/runs/" + run_id).json()
                assert before["steps"]["fault"]["status"] == "running"
            # The transport closes while an actual detached adapter is blocked.
            with client.websocket_connect("/ws?session=default") as socket:
                socket.send_json({"kind": "subscribe", "topics": ["system"]})
                assert socket.receive_json()["kind"] == "state"
                assert client.post(prefix + "/runs", json=request).status_code == 403
                release.set()
                engine = service.engine()
                record = client.portal.call(_wait, engine, ws_id, run_id)
                assert record["status"] == "completed"
                assert client.get(prefix + "/runs/" + run_id).json() == record
                assert _assert_assurance(engine, ws_id, run_id, PREFIX + ["fault", "never"],
                                         before=before) == record
        finally:
            release.set()


@pytest.mark.parametrize("fault", ["stale-revision", "denied-approval"])
def test_rejected_admission_preserves_previous_finished_run(tmp_path, fault):
    async def scenario():
        path = tmp_path / "chats.db"
        engine = _engine(path)
        ws = get_default_workspace_id(path)
        spec = _chain(engine.registry)
        run_id = _start(engine, ws, spec)
        before = await _wait(engine, ws, run_id)
        pending = _chain(engine.registry, tail=True)
        preview = engine.preview(pending, PARAMS)
        if fault == "stale-revision":
            token = engine.approve(ws, preview)
            pending = replace(pending, revision="changed-graph")
        else:
            token = None  # user withheld consent
        for _ in range(2):
            with pytest.raises(PermissionError):
                engine.start(ws, pending, PARAMS, approval_token=token)
        assert engine.store.list(ws) == [before]
        assert _assert_assurance(engine, ws, run_id, PREFIX, before=before) == before

    asyncio.run(scenario())


def test_duplicate_provider_reply_cannot_overwrite_durable_receipt(tmp_path):
    from types import SimpleNamespace
    from backend.domain.graph_run_external import ExternalCall
    from backend.workflow.adapter import StepContext
    from backend.workflow.contracts import WorkflowSpec, WorkflowStepSpec, freeze_config
    from backend.workflow.registry import builtin_registry
    from backend.workflow.step_services import WorkflowRequestJournal, WorkflowRunHost
    from graphlink_task_config import LOCAL_PROVIDER_OLLAMA

    async def scenario():
        registry = builtin_registry()
        config = freeze_config({"provider": LOCAL_PROVIDER_OLLAMA, "model_id": "test-model",
                                "system_prompt": "System", "template": "Summarize {subject}",
                                "output_kind": "text"})
        descriptor = registry.describe("model.generate", "1", config)
        steps = tuple(WorkflowStepSpec(name, descriptor, config, ()) for name in ("first", "second"))
        spec = WorkflowSpec(1, "workflow", "revision", "Duplicate reply", (), steps, ())
        path = tmp_path / "requests.sqlite"
        settings = SimpleNamespace(get_pricing_overrides=lambda: {})
        calls, applied = [], []
        reply = {"text": TextValue("fixed response")}

        async def deliver(request_id):
            calls.append(request_id)
            return reply

        async def dispatch(step, attempt):
            host = WorkflowRunHost(spec, "run", settings_manager=settings, journal=WorkflowRequestJournal(path))
            adapter = registry.resolve("model.generate", "1")
            prepared = adapter.prepare(step, {"subject": TextValue(step.step_id)})
            fingerprint = adapter.fingerprint(prepared)
            call = ExternalCall("model", "Ollama", "workflow-input:" + fingerprint, fingerprint,
                                model_id="test-model")
            result = await host._external(adapter, prepared, StepContext("run", step.step_id, attempt),
                                          call, deliver)
            applied.append(result)
            return result

        for step in steps:
            assert await dispatch(step, 1) == reply
        journal = WorkflowRequestJournal(path)
        first = journal.read("run", "first")
        second = journal.read("run", "second")
        # Re-delivery of the same response via a retried dispatch must be fenced
        # before the provider is called, including with a changed attempt.
        for attempt in (1, 2):
            with pytest.raises((ValueError, RuntimeError)):
                await dispatch(steps[1], attempt)
        assert len(calls) == len(set(calls)) == 2
        assert applied == [reply, reply]
        for step_id, receipt in (("first", first), ("second", second)):
            assert journal.read("run", step_id) == receipt
            saved, outputs = receipt
            assert saved["status"] == "completed"
            assert outputs["text"]["text"] == "fixed response"

    asyncio.run(scenario())


def test_slow_adapter_cancelled_without_replay(tmp_path):
    async def scenario():
        path = tmp_path / "chats.db"
        entered = threading.Event()
        engine = _engine(path, entered=entered)
        ws = get_default_workspace_id(path)
        run_id = _start(engine, ws, _chain(engine.registry, tail=True, slow=True))
        assert await asyncio.to_thread(entered.wait, 10)
        before = engine.store.read(ws, run_id)
        assert engine.cancel(ws, run_id)
        record = await _wait(engine, ws, run_id)
        assert record["status"] == "cancelled"
        assert record["steps"]["fault"]["status"] == "cancelled"
        assert record["steps"]["never"]["status"] == "cancelled"
        assert engine.registry.resolve("cloud", "1").cancelled == ["fault"]
        assert _assert_assurance(_engine(path), ws, run_id, PREFIX + ["fault"], before=before) == record

    asyncio.run(scenario())


def test_corrupt_active_record_fails_closed_without_erasing_healthy_receipt(tmp_path):
    async def scenario():
        path = tmp_path / "chats.db"
        engine = _engine(path)
        ws = get_default_workspace_id(path)
        run_id = _start(engine, ws, _chain(engine.registry))
        before = await _wait(engine, ws, run_id)
        # Inject a torn JSON payload in a separate unfinished record. Current
        # startup fails closed; there is no automatic reconstruction of bytes
        # that were destroyed by corruption.
        broken = '{"steps":'
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("INSERT INTO detached_workflow_runs VALUES (?, ?, ?, ?, ?)",
                         ("corrupt", ws, before["created_at"], "running", broken))
        with pytest.raises(json.JSONDecodeError):
            _engine(path)
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("SELECT status, payload FROM detached_workflow_runs WHERE run_id=?",
                                ("corrupt",)).fetchone() == ("running", broken)
        assert _assert_assurance(engine, ws, run_id, PREFIX, before=before) == before

    asyncio.run(scenario())


def test_exhausted_budget_blocks_next_paid_and_external_action(tmp_path):
    async def scenario():
        path = tmp_path / "chats.db"
        engine = _engine(path, limits=RunLimits(cost_usd=0.25))
        ws = get_default_workspace_id(path)
        run_id = _start(engine, ws, _chain(engine.registry, tail=True))
        record = await _wait(engine, ws, run_id)
        assert record["status"] == "failed" and "cost limit" in record["error"]
        assert record["cost_usd"] == 0.25
        assert record["steps"]["fault"]["status"] == record["steps"]["never"]["status"] == "blocked"
        assert _assert_assurance(_engine(path), ws, run_id, PREFIX) == record

    asyncio.run(scenario())


def test_partial_artifact_write_never_publishes_torn_output(tmp_path, monkeypatch):
    async def scenario():
        path = tmp_path / "chats.db"
        engine = _engine(path)
        ws = get_default_workspace_id(path)
        suffix = "x" * (INLINE_LIMIT + 1)
        raw = canonical(asdict(TextValue("seed paid written" + suffix))).encode()
        ref = content_ref(raw)
        target = engine.artifacts.store.root / ref[:2] / ref
        real_replace = os.replace
        injected = []

        def tear(src, dst):
            if Path(dst) == target:
                assert Path(src).read_bytes() == raw
                Path(src).write_bytes(raw[:17])
                injected.append(ref)
                raise OSError("injected partial write")
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", tear)
        run_id = _start(engine, ws, _chain(engine.registry, tail=True, suffix=suffix))
        record = await _wait(engine, ws, run_id)
        assert injected == [ref]
        assert record["status"] == "failed"
        assert record["steps"]["fault"]["status"] == "failed"
        assert record["steps"]["fault"]["outputs"] == {}
        assert record["steps"]["never"]["status"] == "blocked"
        assert not target.exists()
        assert list(target.parent.glob(ref + ".*.tmp")) == []
        assert engine.artifacts.store.get(ref) is None
        assert _assert_assurance(_engine(path), ws, run_id, PREFIX + ["fault"]) == record

    asyncio.run(scenario())
