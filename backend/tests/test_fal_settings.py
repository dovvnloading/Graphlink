"""SessionBus / write-only settings regression tests for Fal.ai.

Exercises the real app-settings wire contract and backend intents, with
only the external HTTP client faked. No paid requests or network.
"""
from __future__ import annotations

import asyncio
import json

from backend import settings as settings_module
from backend.api import intents_settings_fal
from backend.events import SessionBus
from graphlink_settings_store import SettingsManager

REQUEST_ID = "764cabcf-b745-4b3e-ae38-1200304cf45b"


class Recorder:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


def make_settings(tmp_path):
    manager = SettingsManager(tmp_path / "session.dat")
    bus = SessionBus("fal-settings-test")
    settings_module.register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)
    return manager, bus, recorder


def invoke(bus, name, *arguments):
    asyncio.run(bus.dispatch_intent("app-settings", name, list(arguments)))


def last_settings(recorder):
    return [m["payload"] for m in recorder.messages if m.get("topic") == "app-settings"][-1]


def test_fal_key_is_write_only_and_survives_provider_reset(tmp_path, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("GRAPHLINK_FAL_API_KEY", raising=False)
    manager, bus, recorder = make_settings(tmp_path)

    asyncio.run(bus.publish("app-settings"))
    assert last_settings(recorder)["falApiKeySource"] == "none"

    invoke(bus, "setFalApiKey", "key-id:secret")
    payload = last_settings(recorder)
    assert payload["falApiKeySource"] == "stored"
    assert "key-id:secret" not in json.dumps(payload)
    assert manager.get_fal_api_key() == "key-id:secret"

    manager.reset_api_settings()  # resets chat keys, not separate fal integration
    assert manager.get_fal_api_key() == "key-id:secret"
    invoke(bus, "clearFalApiKey")
    assert manager.get_fal_api_key() == ""
    assert last_settings(recorder)["falApiKeySource"] == "none"


def test_fal_invalid_key_never_overwrites_existing_credential(tmp_path):
    manager, bus, recorder = make_settings(tmp_path)
    manager.set_fal_api_key("valid-saved-key")
    invoke(bus, "setFalApiKey", "\r\nmalicious")
    assert manager.get_fal_api_key() == "valid-saved-key"
    assert last_settings(recorder)["falNotice"] == "Enter a valid Fal.ai API key."


def test_submit_sync_refresh_and_cancel_flow(tmp_path, monkeypatch):
    manager, bus, recorder = make_settings(tmp_path)
    manager.set_fal_api_key("test-credential")
    calls = []

    class MockFal:
        def __init__(self, key):
            assert key == "test-credential"

        def submit(self, model_id, arguments, *, options=None):
            calls.append(("submit", model_id, arguments, options))
            return {"request_id": REQUEST_ID, "status": "IN_QUEUE"}

        def status(self, model_id, request_id, *, logs=False):
            calls.append(("status", model_id, request_id))
            return {"status": "COMPLETED", "request_id": request_id}

        def result(self, model_id, request_id):
            calls.append(("result", model_id, request_id))
            return {"images": [{"url": "https://v3.fal.media/files/output.png"}]}

        def run(self, model_id, arguments, *, options=None):
            calls.append(("run", model_id, arguments))
            return {"audio_url": "https://v3.fal.media/files/sound.wav"}

        def cancel(self, model_id, request_id):
            calls.append(("cancel", model_id, request_id))
            return {"status": "ALREADY_COMPLETED"}

    monkeypatch.setattr(intents_settings_fal, "FalClient", MockFal)

    invoke(bus, "submitFalJob", "fal-ai/flux/schnell", '{"prompt":"a mountain"}', '{"store_io":false}')
    assert manager.get_fal_jobs()[0]["status"] == "IN_QUEUE"
    assert calls[0] == ("submit", "fal-ai/flux/schnell", {"prompt": "a mountain"}, {"store_io": False})

    invoke(bus, "refreshFalJob", "fal-ai/flux/schnell", REQUEST_ID, True)
    job = manager.get_fal_jobs()[0]
    assert job["status"] == "COMPLETED"
    assert job["mediaUrls"] == ["https://v3.fal.media/files/output.png"]
    assert "output.png" in job["resultJson"]

    invoke(bus, "runFalSync", "fal-ai/speech-to-text", '{"audio_url":"https://test.invalid/sample.wav"}')
    assert "sound.wav" in last_settings(recorder)["falSyncResultJson"]
    invoke(bus, "cancelFalJob", "fal-ai/flux/schnell", REQUEST_ID)
    assert manager.get_fal_jobs()[0]["status"] == "ALREADY_COMPLETED"

    assert "test-credential" not in json.dumps(last_settings(recorder))


def test_broken_json_never_reaches_paid_client(tmp_path, monkeypatch):
    manager, bus, recorder = make_settings(tmp_path)
    manager.set_fal_api_key("some-key")
    class MustNotInvoke:
        def __init__(self, key):
            raise AssertionError("Invalid JSON must fail before client construction")
    monkeypatch.setattr(intents_settings_fal, "FalClient", MustNotInvoke)

    invoke(bus, "submitFalJob", "fal-ai/model", '{"prompt": invalid}', "{}")
    assert "valid JSON" in last_settings(recorder)["falNotice"]
    assert manager.get_fal_jobs() == []


def test_fal_history_is_bounded_and_does_not_persist_inputs(tmp_path):
    manager = SettingsManager(tmp_path / "session.dat")
    for i in range(45):
        request_id = f"00000000-0000-4000-8000-{i:012d}"
        manager.upsert_fal_job("fal-ai/flux/schnell", request_id, "IN_QUEUE")
    assert len(manager.get_fal_jobs()) == 40
    assert "prompt" not in json.dumps(manager.get_fal_jobs())
