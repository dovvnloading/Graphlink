"""Regression coverage for the typed, detached workflow scheduler."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.asset_gc import collect_unreferenced_assets
from backend.chat_library import get_default_workspace_id, load_chat_row
from backend.workflow.artifacts import INLINE_LIMIT
from backend.workflow.contracts import (
    InputBinding, PortSet, PortSpec, StepOutputSource, TextValue,
    WorkflowInputSource, WorkflowOutput, WorkflowSpec, WorkflowStepSpec, freeze_config,
)
from backend.workflow.engine import RunLimits, WorkflowEngine
from backend.workflow.planning import StepPolicy
from backend.workflow.registry import StepAdapterRegistry

TEXT = PortSpec("text", "text", True)


def _spec(registry, entries, *, output=None, input_port=TEXT):
    steps = []
    for step_id, adapter_id, config, source in entries:
        config_json = freeze_config(config)
        descriptor = registry.describe(adapter_id, "1", config_json)
        bindings = tuple(InputBinding(port.name, source) for port in descriptor.ports.inputs)
        steps.append(WorkflowStepSpec(step_id, descriptor, config_json, bindings))
    outputs = () if output is None else (WorkflowOutput(input_port, output),)
    return WorkflowSpec(1, "workflow", "revision", "Example", (input_port,), tuple(steps), outputs)


def _entry(step_id, adapter="local", *, source=None, **config):
    return step_id, adapter, config, source or WorkflowInputSource("text")


class Fake:
    def __init__(self, effect="pure"):
        self.side_effect_class = effect
        self.started = []
        self.finished = []
        self.cancelled = []
        self.active = 0
        self.peak = 0
        self.gate = asyncio.Event()

    def ports(self, config_json):
        return PortSet((TEXT,), (TEXT,))

    def prepare(self, spec, inputs):
        return json.loads(spec.config_json), inputs["text"]

    def fingerprint(self, prepared):
        return "a" * 64

    def preview(self, prepared):
        return {}

    async def run(self, prepared, context):
        config, value = prepared
        self.started.append(context.step_id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if config.get("wait"):
                await self.gate.wait()
            if config.get("fail"):
                raise ValueError("secret provider failure")
            if "cost" in config:
                context.report_cost(config["cost"])
            self.finished.append(context.step_id)
            return {"text": TextValue(value.text + config.get("suffix", ""))}
        finally:
            self.active -= 1

    async def cancel(self, context):
        self.cancelled.append(context.step_id)


def _engine(path, *, limits=None, policy=None):
    registry = StepAdapterRegistry()
    local, cloud, writes, executes = Fake(), Fake("cloud"), Fake("writes"), Fake("executes")
    for name, adapter in (("local", local), ("cloud", cloud), ("writes", writes), ("executes", executes)):
        registry.register(name, "1", adapter)
    engine = WorkflowEngine(path, registry=registry, limits=limits, policy_for=policy or (
        lambda spec: StepPolicy("cloud", ("provider", "model"), 0.25)
        if spec.adapter.side_effect_class == "cloud" else StepPolicy()
    ))
    return engine, get_default_workspace_id(path), local, cloud, writes, executes


async def _until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


def test_dag_provider_concurrency_and_frozen_parameters(tmp_path):
    async def scenario():
        engine, ws, local, cloud, *_ = _engine(tmp_path / "chats.db")
        entries = [
            _entry("child", source=StepOutputSource("root", "text"), suffix="!"),
            *[_entry(f"c{i}", "cloud", wait=True) for i in range(5)],
            _entry("root", wait=True, suffix=" root"), _entry("local2", wait=True),
        ]
        spec = _spec(engine.registry, entries, output=StepOutputSource("child", "text"))
        params = {"text": TextValue("hello")}
        preview = engine.preview(spec, params)
        assert preview.requires_approval and preview.estimated_cost_usd == 1.25
        token = engine.approve(ws, preview)
        run_id = engine.start(ws, spec, params, approval_token=token)
        params["text"] = TextValue("changed")
        await _until(lambda: cloud.active == 4 and local.active == 1)
        assert local.started == ["root"]
        assert cloud.peak == 4
        cloud.gate.set()
        local.gate.set()
        record = await engine.wait(ws, run_id)
        assert record["status"] == "completed"
        assert record["outputs"]["text"]["text"] == "hello root!"
        assert local.peak == 1 and local.started.index("root") < local.started.index("child")
        assert cloud.peak == 4 and len(cloud.started) == 5
        assert record["cost_usd"] == 1.25
        assert [e["sequence"] for e in record["events"]] == list(range(len(record["events"])))
    asyncio.run(scenario())


def test_approval_pins_and_single_use(tmp_path):
    async def scenario():
        model = ["m1"]
        engine, ws, _, cloud, writes, executes = _engine(
            tmp_path / "chats.db", policy=lambda spec: (
                StepPolicy("cloud", ("provider", model[0]), 0.1)
                if spec.adapter.side_effect_class == "cloud" else StepPolicy()
            ),
        )
        params = {"text": TextValue("one")}
        for name in ("cloud", "executes", "writes"):
            spec = _spec(engine.registry, [_entry("step", name)])
            preview = engine.preview(spec, params)
            assert preview.requires_approval
            with pytest.raises(PermissionError):
                engine.start(ws, spec, params)
            token = engine.approve(ws, preview)
            with pytest.raises(PermissionError):
                engine.start(ws, spec, {"text": TextValue("two")}, approval_token=token)
            token = engine.approve(ws, preview)
            with pytest.raises(PermissionError):
                engine.start(ws, replace(spec, revision="new"), params, approval_token=token)
            token = engine.approve(ws, preview)
            run_id = engine.start(ws, spec, params, approval_token=token)
            assert (await engine.wait(ws, run_id))["status"] == "completed"
            with pytest.raises(PermissionError):
                engine.start(ws, spec, params, approval_token=token)
        spec = _spec(engine.registry, [_entry("step", "cloud")])
        token = engine.approve(ws, engine.preview(spec, params))
        model[0] = "m2"
        with pytest.raises(PermissionError):
            engine.start(ws, spec, params, approval_token=token)
        assert cloud.started == writes.started == executes.started == ["step"]
    asyncio.run(scenario())


def test_mid_step_cancel_and_preserved_results(tmp_path):
    async def scenario():
        engine, ws, local, *_ = _engine(tmp_path / "chats.db")
        spec = _spec(engine.registry, [
            _entry("first", suffix=" first"),
            _entry("slow", source=StepOutputSource("first", "text"), wait=True),
            _entry("after", source=StepOutputSource("slow", "text")),
        ], output=StepOutputSource("first", "text"))
        run_id = engine.start(ws, spec, {"text": TextValue("ok")})
        await _until(lambda: "slow" in local.started)
        assert engine.cancel(ws + 1, run_id) is False
        assert engine.cancel(ws, run_id)
        record = await asyncio.wait_for(engine.wait(ws, run_id), timeout=3)
        assert record["status"] == "cancelled"
        assert record["error"] == "Cancelled by user."
        assert record["steps"]["first"]["outputs"]["text"]["text"] == "ok first"
        assert record["steps"]["slow"]["status"] == "cancelled"
        assert record["steps"]["after"]["status"] == "cancelled"
        assert local.cancelled == ["slow"] and local.started == ["first", "slow"]
        assert record["outputs"]["text"]["text"] == "ok first"
        local.gate.set()
        assert engine.store.read(ws, run_id) == record
    asyncio.run(scenario())


@pytest.mark.parametrize("limit", ["cost", "steps", "duration"])
def test_run_caps(tmp_path, limit):
    async def scenario():
        limits = {"cost": RunLimits(cost_usd=0.25), "steps": RunLimits(max_steps=1),
                  "duration": RunLimits(wall_time_seconds=0.75)}[limit]
        engine, ws, local, cloud, *_ = _engine(tmp_path / "chats.db", limits=limits)
        adapter = "cloud" if limit == "cost" else "local"
        spec = _spec(engine.registry, [
            _entry("first", adapter),
            _entry("second", adapter, source=StepOutputSource("first", "text"), wait=limit == "duration"),
            _entry("third", adapter, source=StepOutputSource("second", "text")),
        ], output=StepOutputSource("first", "text"))
        params = {"text": TextValue("preserved")}
        preview = engine.preview(spec, params)
        token = engine.approve(ws, preview) if preview.requires_approval else None
        record = await asyncio.wait_for(engine.wait(
            ws, engine.start(ws, spec, params, approval_token=token),
        ), timeout=3)
        assert record["status"] == "failed"
        assert ("step limit" if limit == "steps" else f"{limit} limit") in record["error"]
        assert record["steps"]["first"]["status"] == "completed"
        assert record["outputs"]["text"]["text"] == "preserved"
        assert "third" not in local.started + cloud.started
        if limit == "duration":
            assert local.cancelled == ["second"]
        elif limit == "cost":
            assert cloud.started == ["first"] and record["cost_usd"] == 0.25
        else:
            assert local.started == ["first"]
    asyncio.run(scenario())


def test_invalid_dag_and_failed_dependency(tmp_path):
    async def scenario():
        engine, ws, local, *_ = _engine(tmp_path / "chats.db")
        params = {"text": TextValue("x")}
        bad = _spec(engine.registry, [
            _entry("a", source=StepOutputSource("b", "text")),
            _entry("b", source=StepOutputSource("a", "text")),
        ])
        with pytest.raises(ValueError, match="cycle"):
            engine.preview(bad, params)
        spec = _spec(engine.registry, [
            _entry("child", source=StepOutputSource("parent", "text")),
            _entry("parent", fail=True), _entry("independent"),
        ])
        record = await engine.wait(ws, engine.start(ws, spec, params))
        assert record["status"] == "failed"
        assert record["steps"]["child"]["status"] == "blocked"
        assert record["steps"]["independent"]["status"] == "completed"
        assert "secret provider failure" not in json.dumps(record)
        assert "child" not in local.started
    asyncio.run(scenario())


def test_artifacts_detached_save_retention_and_gc(tmp_path):
    async def scenario():
        path = tmp_path / "chats.db"
        engine = WorkflowEngine(path)
        ws = get_default_workspace_id(path)
        config = freeze_config({"operation": "copy"})
        note = WorkflowStepSpec(
            "note", engine.registry.describe("graph.note", "1", config), config,
            (InputBinding("text", WorkflowInputSource("text")),),
        )
        spec = WorkflowSpec(1, "w", "r", "Detached", (TEXT,), (note,),
                            (WorkflowOutput(TEXT, StepOutputSource("note", "text")),))
        payload = "x" * (INLINE_LIMIT + 1)
        assert "asset_ref" not in engine.artifacts.pack(TextValue("x" * INLINE_LIMIT))
        run_id = engine.start(ws, spec, {"text": TextValue(payload)})
        record = await engine.wait(ws, run_id)
        assert record["status"] == "completed"
        packed = record["outputs"]["text"]
        ref = packed["asset_ref"]
        assert packed == record["inputs"]["text"]
        assert engine.artifacts.unpack(packed) == TextValue(payload)
        assert payload not in json.dumps(record)
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("SELECT count(*) FROM graphs").fetchone()[0] == 0
        assert collect_unreferenced_assets(path, apply=True, min_age_days=0).deleted_assets == 0

        # File parameters traverse as binary refs, without reading them as text.
        file_port = PortSpec("file", "file", True)
        file_value = engine.artifacts.file(b"\x00binary", "data.bin")
        filespec = WorkflowSpec(1, "files", "r", "File", (file_port,), (),
                               (WorkflowOutput(file_port, WorkflowInputSource("file")),))
        file_id = engine.start(ws, filespec, {"file": file_value})
        assert (await engine.wait(ws, file_id))["status"] == "completed"
        graph_id = engine.save_result_as_graph(ws, file_id)
        graph = load_chat_row(path, graph_id)
        assert any(node.get("file_path") == f"asset://{file_value.asset.sha256}"
                   for node in graph["data"]["nodes"])

        # Expired runs are removed, but the saved graph's file survives.
        old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("UPDATE detached_workflow_runs SET created_at = ?", (old,))
        assert engine.store.list(ws) == []
        collect_unreferenced_assets(path, apply=True, min_age_days=0)
        assert not engine.artifacts.store.exists(ref)
        assert engine.artifacts.bytes_for(file_value.asset) == b"\x00binary"
    asyncio.run(scenario())


def test_workspace_retention_and_recovery(tmp_path):
    async def scenario():
        path = tmp_path / "chats.db"
        engine, ws, *_ = _engine(path)
        spec = _spec(engine.registry, [])
        for i in range(52):
            run_id = engine.start(ws, spec, {"text": TextValue(str(i))})
            await engine.wait(ws, run_id)
        assert len(engine.store.list(ws)) == 50
        assert engine.store.list(ws + 1) == []
        record = engine.store.list(ws)[0]
        record["status"] = "running"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("UPDATE detached_workflow_runs SET status='running', payload=? WHERE run_id=?",
                         (json.dumps(record), record["run_id"]))
        engine.store.interrupt_unfinished()
        recovered = engine.store.read(ws, record["run_id"])
        assert recovered["status"] == "interrupted"
        assert recovered["events"][-1]["kind"] == "run-interrupted"
    asyncio.run(scenario())


def test_graph_local_model_approval_identity_and_cancellation(tmp_path, monkeypatch):
    from backend.providers import graph_local_ollama
    from graphlink_task_config import LOCAL_PROVIDER_OLLAMA

    async def scenario():
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
        engine = WorkflowEngine(tmp_path / "chats.db")
        ws = get_default_workspace_id(tmp_path / "chats.db")
        entered = asyncio.Event()
        released = asyncio.Event()
        async def complete(*, prompt, model_id, endpoint):
            assert (prompt, model_id, endpoint) == ("test prompt", "qwen3:8b", "http://127.0.0.1:11434")
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()
        monkeypatch.setattr(graph_local_ollama, "async_local_ollama_complete", complete)
        spec = _spec(engine.registry, [
            _entry("model", "graph.local_model", provider=LOCAL_PROVIDER_OLLAMA,
                   model="qwen3:8b", endpoint="http://127.0.0.1:11434"),
        ])
        params = {"text": TextValue("test prompt")}
        preview = engine.preview(spec, params)
        assert preview.requires_approval
        assert preview.steps[0][2].model_identity == (LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        with pytest.raises(PermissionError):
            engine.start(ws, spec, params)
        run_id = engine.start(ws, spec, params, approval_token=engine.approve(ws, preview))
        await asyncio.wait_for(entered.wait(), 3)
        assert engine.cancel(ws, run_id)
        record = await asyncio.wait_for(engine.wait(ws, run_id), 3)
        assert record["status"] == "cancelled"
        await asyncio.wait_for(released.wait(), 3)
        with closing(sqlite3.connect(tmp_path / "chats.db")) as conn:
            assert conn.execute("SELECT count(*) FROM graphs").fetchone()[0] == 0
    asyncio.run(scenario())


def test_authenticated_detached_file_run_and_save(tmp_path):
    from dataclasses import asdict
    import time
    from fastapi.testclient import TestClient
    from backend.app import create_app

    db = tmp_path / "chats.db"
    ws = get_default_workspace_id(db)
    app = create_app(spa_dir=tmp_path / "missing-spa", auth_token="workflow-admin",
                     chat_db_path=db, settings_state_file=tmp_path / "session.dat")
    prefix = f"/api/workflows/{ws}"
    auth = {"Authorization": "Bearer workflow-admin"}
    with TestClient(app, base_url="http://127.0.0.1", headers={"host": "127.0.0.1"}) as client:
        assert client.get(prefix + "/runs").status_code == 401
        assert client.get(prefix + "/runs", params={"summary": "true"}).status_code == 401
        assert client.get(prefix + "/presets", params={"summary": "true"}).status_code == 401
        upload = client.post(prefix + "/files", params={"filename": "data.bin"},
                             content=b"binary-data", headers=auth)
        assert upload.status_code == 201
        value = upload.json()
        port = PortSpec("file", "file", True)
        spec = WorkflowSpec(1, "files", "r", "File", (port,), (),
                            (WorkflowOutput(port, WorkflowInputSource("file")),))
        request = {"spec": asdict(spec), "parameters": {"file": value}}
        preview = client.post(prefix + "/preview", json=request, headers=auth)
        assert preview.status_code == 200
        assert not preview.json()["requires_approval"]
        start = client.post(prefix + "/runs", json=request, headers=auth)
        assert start.status_code == 202
        run_url = prefix + "/runs/" + start.json()["run_id"]
        deadline = time.monotonic() + 3
        while True:
            record = client.get(run_url, headers=auth).json()
            if record["status"] == "completed":
                break
            assert time.monotonic() < deadline, record
            time.sleep(0.01)
        summaries = client.get(prefix + "/runs", params={"summary": "true"}, headers=auth).json()
        assert summaries == [{
            "run_id": record["run_id"], "status": "completed", "created_at": record["created_at"],
            "preset_id": None, "spec": {"name": "File"}, "started_at": record["started_at"],
            "finished_at": record["finished_at"], "cost_usd": record["cost_usd"], "waiting_for_approval": False,
        }]
        assert client.get(prefix + "/runs", headers=auth).json() == [record]
        assert client.get(prefix + "/presets", params={"summary": "true"}, headers=auth).json() == []
        assert client.get(run_url.replace(prefix, f"/api/workflows/{ws + 1}"), headers=auth).status_code == 404
        ref = record["outputs"]["file"]["asset_ref"]
        assert client.get(run_url + "/assets/" + ref, headers=auth).content == b"binary-data"
        result = client.post(run_url + "/save-as-graph", headers=auth)
        assert result.status_code == 201
        graph_id = result.json()["graph_id"]
        url = f"{prefix}/graphs/{graph_id}/assets/{ref}"
        assert client.get(url, headers=auth).content == b"binary-data"
        assert client.get(url.replace(prefix, f"/api/workflows/{ws + 1}"),
                          headers=auth).status_code == 404


def test_unattended_effects_pause_and_require_one_time_operator_decisions(tmp_path):
    from backend.workflow.engine import UnattendedPolicy

    async def scenario():
        engine, ws, local, cloud, writes, executes = _engine(tmp_path / "chats.db")
        notifications = []
        engine.on_pause = lambda *args: notifications.append(args)
        spec = _spec(engine.registry, [
            _entry("root", suffix="!"),
            _entry("code", "executes", source=StepOutputSource("root", "text")),
            _entry("write", "writes", source=StepOutputSource("code", "text")),
            _entry("remote", "cloud", source=StepOutputSource("write", "text")),
        ])
        params = {"text": TextValue("hello")}
        preview = engine.preview(spec, params)
        policy = UnattendedPolicy(preview.fingerprint, frozenset({"pure", "cloud"}), 0.25)
        with pytest.raises(ValueError, match="Code execution"):
            UnattendedPolicy(preview.fingerprint, frozenset({"writes"}), 1)
        with pytest.raises(PermissionError, match="preview changed"):
            engine.start(ws, spec, {"text": TextValue("different")}, unattended=policy)
        run = engine.start(ws, spec, params, unattended=policy, preset_id="test")
        def pending():
            return engine.store.read(ws, run)["awaiting_approval"]
        await _until(lambda: pending() is not None)
        first = pending()
        assert first["step_id"] == "code"
        assert local.finished == ["root"] and not executes.started and not writes.started
        assert notifications == [(ws, run, "code")]
        # The paused unattended step must reach the app-wide approval feed,
        # without exposing its nonce there.
        gates = [gate for gate in engine.pending_approvals() if gate["run_id"] == run]
        assert [(gate["kind"], gate["step_id"]) for gate in gates] == [("unattended", "code")]
        assert first["nonce"] not in str(gates)
        with pytest.raises(ValueError, match="active run"):
            engine.start(ws, spec, params, unattended=policy)
        with pytest.raises(ValueError, match="no longer awaiting"):
            engine.decide_step(ws + 1, run, "code", first["nonce"], approved=True)
        with pytest.raises(ValueError, match="no longer awaiting"):
            engine.decide_step(ws, run, "code", "forged", approved=True)
        engine.decide_step(ws, run, "code", first["nonce"], approved=True)
        await _until(lambda: pending() is not None and pending()["step_id"] == "write")
        second = pending()
        assert executes.finished == ["code"] and not writes.started and not cloud.started
        with pytest.raises(ValueError, match="no longer awaiting"):
            engine.decide_step(ws, run, "code", first["nonce"], approved=True)
        engine.decide_step(ws, run, "write", second["nonce"], approved=False)
        record = await engine.wait(ws, run)
        assert record["status"] == "cancelled" and record["awaiting_approval"] is None
        assert not writes.started and not cloud.started
        assert len(notifications) == 2
        # Each run requires fresh decisions even after identical parameters.
        again = engine.start(ws, spec, params, unattended=policy)
        await _until(lambda: engine.store.read(ws, again)["awaiting_approval"] is not None)
        await engine.shutdown()
        assert engine.store.read(ws, again)["status"] == "interrupted"

    asyncio.run(scenario())


def test_unattended_cost_cap_is_per_run_and_never_dispatches_over_cap(tmp_path):
    from backend.workflow.engine import UnattendedPolicy

    async def scenario():
        engine, ws, _, cloud, *_ = _engine(tmp_path / "chats.db")
        spec = _spec(engine.registry, [_entry("cloud", "cloud")])
        params = {"text": TextValue("hello")}
        preview = engine.preview(spec, params)
        run = engine.start(ws, spec, params, unattended=UnattendedPolicy(
            preview.fingerprint, frozenset({"cloud"}), 0.1,
        ))
        record = await engine.wait(ws, run)
        assert record["status"] == "failed" and "cost limit" in record["error"]
        assert not cloud.started and record["cost_usd"] == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("fail_from", ["first-checkpoint", "pending-cleanup", "after-dispatch"])
def test_checkpoint_failures_release_the_run_without_replay(tmp_path, monkeypatch, fail_from):
    """#1081: a storage failure must not strand a dead worker as active."""
    async def scenario():
        engine, ws, local, *_ = _engine(tmp_path / "chats.db")
        spec = _spec(engine.registry, [
            _entry("slow", wait=True),
            _entry("after", source=StepOutputSource("slow", "text")),
        ])
        params = {"text": TextValue("x")}
        real = engine.store.checkpoint
        state = {"broken": fail_from == "first-checkpoint", "calls": 0}

        def checkpoint(workspace_id, record):
            state["calls"] += 1
            if state["broken"]:
                raise sqlite3.OperationalError("disk I/O error")
            return real(workspace_id, record)

        monkeypatch.setattr(engine.store, "checkpoint", checkpoint)
        run_id = engine.start(ws, spec, params)
        if fail_from == "pending-cleanup":
            # Break storage just before the worker observes cancellation;
            # every cleanup checkpoint for pending steps then fails too.
            await _until(lambda: "slow" in local.started)
            state["broken"] = True
            assert engine.cancel(ws, run_id)
        elif fail_from == "after-dispatch":
            await _until(lambda: "slow" in local.started)
            state["broken"] = True
            local.gate.set()
        record = await asyncio.wait_for(engine.wait(ws, run_id), timeout=3)
        assert record["status"] in ("failed", "cancelled")
        assert all(step["status"] not in ("pending", "running") for step in record["steps"].values())
        assert run_id not in engine._runs
        assert engine.cancel(ws, run_id) is False
        # Storage recovers: the stale receipt is reconciled on the next
        # admission and a fresh run of the same workflow is admitted.
        state["broken"] = False
        local.gate.set()
        await _until(lambda: not engine._draining)
        started = list(local.started)
        second = engine.start(ws, spec, params)
        assert engine.store.read(ws, run_id)["status"] == record["status"]
        assert (await asyncio.wait_for(engine.wait(ws, second), timeout=3))["status"] == "completed"
        # Nothing from the failed run was replayed: only the new run's steps ran.
        assert local.started[:len(started)] == started
        assert local.started[len(started):] == ["slow", "after"]
    asyncio.run(scenario())
