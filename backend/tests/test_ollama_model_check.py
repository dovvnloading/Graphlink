"""Report-only Ollama checks: mocked async daemon, no network or saved-setting writes."""

import asyncio

import pytest

from backend import ollama_model_check
from backend.api import intents_ollama_model_check, intents_settings_ollama
from backend.events import SessionBus
from backend.settings import register_settings
from graphlink_settings_store import SettingsManager


def test_probe_reports_daemon_load_duration_speed_and_real_tool_call(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] > 0

        async def close(self):
            calls.append("closed")

        async def chat(self, **kwargs):
            calls.append(kwargs)
            if "tools" in kwargs:
                return {"message": {"tool_calls": [{"function": {"name": "graphlink_model_check"}}]}}
            return {"load_duration": 2_250_000_000, "eval_count": 40, "eval_duration": 1_000_000_000}

    monkeypatch.setattr(ollama_model_check.ollama, "AsyncClient", FakeClient)
    result = asyncio.run(ollama_model_check.probe_ollama_model("installed:latest"))
    assert result == {
        "loadSeconds": 2.25, "tokensPerSecond": 40.0, "toolCallingWorks": True, "message": "",
    }
    assert len(calls) == 3
    assert calls[-1] == "closed"
    assert all(call["model"] == "installed:latest" and call["keep_alive"] == 0 for call in calls[:2])
    assert calls[0]["options"]["num_predict"] <= 32
    assert calls[1]["tools"][0]["function"]["name"] == "graphlink_model_check"


def test_probe_preserves_timings_if_tools_unsupported_and_handles_missing_counters(monkeypatch):
    closed = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def close(self):
            closed.append(True)

        async def chat(self, **kwargs):
            if "tools" in kwargs:
                raise RuntimeError("model does not support tools")
            return {"load_duration": 0, "eval_count": 12, "eval_duration": 0}

    monkeypatch.setattr(ollama_model_check.ollama, "AsyncClient", FakeClient)
    result = asyncio.run(ollama_model_check.probe_ollama_model("older:latest"))
    assert result["loadSeconds"] == 0
    assert result["tokensPerSecond"] is None
    assert result["toolCallingWorks"] is False
    assert "does not support tools" in result["message"]
    assert closed == [True]


def _registered_bus(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    models = [
        {"name": name, "capabilities": ["chat"], "sizeBytes": None, "family": "", "parameterSize": "",
         "quantization": "", "trainedContextLength": None, "loaded": None, "memoryBytes": None,
         "vramBytes": None, "source": "server"}
        for name in ("first:latest", "second:latest")
    ]
    monkeypatch.setattr(
        intents_settings_ollama, "build_ollama_inventory",
        lambda _: {"models": [row["name"] for row in models], "inventory": models,
                   "source": "server", "server_reachable": True, "scan_mode": "server",
                   "scan_path": "", "locations": []},
    )
    bus = SessionBus("ollama-check-test")
    register_settings(bus, manager)
    return bus, manager


def test_check_uses_only_inventory_and_never_persists_settings(tmp_path, monkeypatch):
    bus, manager = _registered_bus(tmp_path, monkeypatch)
    calls = []

    async def fake_probe(model):
        calls.append(model)
        return {"loadSeconds": 0.4, "tokensPerSecond": 24.0, "toolCallingWorks": True, "message": ""}

    monkeypatch.setattr(intents_ollama_model_check, "probe_ollama_model", fake_probe)

    async def scenario():
        await bus.dispatch_intent("app-settings", "scanOllamaSystem", [])
        # The scan intentionally saves its cache; Check must not write anything after that.
        assignments_before_check = manager.get_ollama_model_assignments()
        settings_before_check = manager.state_file.read_bytes()
        monkeypatch.setattr(manager, "_save_state",
                            lambda *_args, **_kwargs: pytest.fail("check attempted to save settings"))
        monkeypatch.setattr(manager, "set_ollama_model_assignments",
                            lambda *_: pytest.fail("check attempted to change model settings"))
        monkeypatch.setattr(manager, "set_ollama_model_scan_cache",
                            lambda *_: pytest.fail("check attempted to write scan settings"))
        await bus.dispatch_intent("app-settings", "checkOllamaModel", ["other:not-installed"])
        await bus.dispatch_intent("app-settings", "checkOllamaModel", ["first:latest"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await bus.publish("app-settings")
        assert manager.get_ollama_model_assignments() == assignments_before_check
        assert manager.state_file.read_bytes() == settings_before_check

    class Recorder:
        messages = []

        async def send_json(self, data):
            self.messages.append(data)

    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(scenario())
    assert calls == ["first:latest"]
    rows = recorder.messages[-1]["payload"]["ollamaModelChecks"]
    assert rows == [{"name": "first:latest", "status": "complete", "loadSeconds": 0.4,
                     "tokensPerSecond": 24.0, "toolCallingWorks": True, "message": ""}]


def test_cancel_interrupts_running_check_and_a_new_check_can_start(tmp_path, monkeypatch):
    bus, manager = _registered_bus(tmp_path, monkeypatch)
    calls = []
    cancelled = []

    async def scenario():
        await bus.dispatch_intent("app-settings", "scanOllamaSystem", [])
        assignments_before_check = manager.get_ollama_model_assignments()
        settings_before_check = manager.state_file.read_bytes()
        monkeypatch.setattr(manager, "_save_state",
                            lambda *_args, **_kwargs: pytest.fail("check/cancel attempted to save settings"))
        started = asyncio.Event()

        async def slow_probe(model):
            calls.append(model)
            if model == "first:latest":
                started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    cancelled.append(model)
                    raise
            return {"loadSeconds": 0.1, "tokensPerSecond": 10.0,
                    "toolCallingWorks": False, "message": "No tool call."}

        monkeypatch.setattr(intents_ollama_model_check, "probe_ollama_model", slow_probe)
        await bus.dispatch_intent("app-settings", "checkOllamaModel", ["first:latest"])
        await asyncio.wait_for(started.wait(), 1)
        assert manager.state_file.read_bytes() == settings_before_check
        await bus.dispatch_intent("app-settings", "checkOllamaModel", ["second:latest"])
        await bus.dispatch_intent("app-settings", "cancelOllamaModelCheck", ["second:latest"])
        await bus.dispatch_intent("app-settings", "cancelOllamaModelCheck", ["first:latest"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await bus.dispatch_intent("app-settings", "checkOllamaModel", ["second:latest"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await bus.publish("app-settings")
        assert manager.get_ollama_model_assignments() == assignments_before_check
        assert manager.state_file.read_bytes() == settings_before_check

    class Recorder:
        messages = []

        async def send_json(self, data):
            self.messages.append(data)

    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(scenario())
    assert calls == ["first:latest", "second:latest"]
    assert cancelled == ["first:latest"]
    rows = {row["name"]: row for row in recorder.messages[-1]["payload"]["ollamaModelChecks"]}
    assert rows["first:latest"]["status"] == "cancelled"
    assert rows["second:latest"]["status"] == "complete"


def test_daemon_error_is_reported_and_next_check_is_allowed(tmp_path, monkeypatch):
    bus, _manager = _registered_bus(tmp_path, monkeypatch)

    async def scenario():
        await bus.dispatch_intent("app-settings", "scanOllamaSystem", [])

        async def boom(_model):
            raise ConnectionError("daemon unreachable")

        monkeypatch.setattr(intents_ollama_model_check, "probe_ollama_model", boom)
        await bus.dispatch_intent("app-settings", "checkOllamaModel", ["first:latest"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await bus.publish("app-settings")

    class Recorder:
        messages = []

        async def send_json(self, data):
            self.messages.append(data)

    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(scenario())
    row = recorder.messages[-1]["payload"]["ollamaModelChecks"][0]
    assert row["status"] == "error"
    assert "daemon unreachable" in row["message"]


def test_probe_cancellation_closes_async_client_and_does_not_execute_tools(monkeypatch):
    started = asyncio.Event()
    closed = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def chat(self, **_kwargs):
            started.set()
            await asyncio.Future()

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(ollama_model_check.ollama, "AsyncClient", FakeClient)

    async def exercise():
        task = asyncio.create_task(ollama_model_check.probe_ollama_model("installed:latest"))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert closed == [True]


def test_probe_ignores_malformed_counters_and_tool_calls(monkeypatch):
    closed = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def chat(self, **kwargs):
            if "tools" in kwargs:
                return {"message": {"tool_calls": {"function": {"name": "graphlink_model_check"}}}}
            return {"load_duration": float("inf"), "eval_count": 1e308, "eval_duration": 1e-308}

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(ollama_model_check.ollama, "AsyncClient", FakeClient)
    report = asyncio.run(ollama_model_check.probe_ollama_model("installed:latest"))
    assert report["loadSeconds"] is None
    assert report["tokensPerSecond"] is None
    assert report["toolCallingWorks"] is False
    assert closed == [True]
