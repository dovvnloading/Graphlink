"""Fault-injection regressions for persisted general preferences."""

import asyncio
from copy import deepcopy

import pytest

import backend.api.intents_settings_general as general_intents
from backend.domain.model import UserFacingError
from backend.events import SessionBus
from backend.settings import register_settings, settings_payload
from graphlink_settings_store import SettingsManager
from settings_store import persistence


class Recorder:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


@pytest.fixture
def manager(tmp_path):
    manager = SettingsManager(tmp_path / "session.dat")
    manager._save_state(raise_on_error=True)
    return manager


@pytest.fixture(params=["write", "fsync", "replace"])
def fail_save(request, manager):
    """Fail inside the actual staged save, not in a mock of _save_state."""
    previous = deepcopy(manager.state)

    def fail(*args, **kwargs):
        # Lock-free readers must still see the old committed state even
        # while the candidate is being written.
        assert manager.state == previous
        raise OSError("injected filesystem failure at private/path")

    target, name = {
        "write": (persistence.json, "dump"),
        "fsync": (persistence.os, "fsync"),
        "replace": (persistence.os, "replace"),
    }[request.param]

    return target, name, fail


@pytest.mark.parametrize(("intent", "args", "field", "expected"), [
    ("setShowTokenCounter", [True], "showTokenCounter", True),
    ("setEnableSystemPrompt", [False], "enableSystemPrompt", False),
    ("setLogLevel", ["DEBUG"], "logLevel", "DEBUG"),
    ("setAutoModelPolicy", ["fastest"], "autoModelPolicy", "fastest"),
    ("setTheme", ["dark"], "theme", "dark"),
    ("setHasCompletedOnboarding", [True], "hasCompletedOnboarding", True),
    ("setNotificationPreference", ["warning", False], "notificationPreferences",
     {"info": True, "success": True, "warning": False, "error": True}),
])
def test_general_intent_save_failure_is_not_published_and_retry_persists(
    manager, fail_save, monkeypatch, intent, args, field, expected,
):
    bus = SessionBus("general-persistence")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)
    before_state = deepcopy(manager.state)
    before_file = manager.state_file.read_bytes()
    before_payload = settings_payload(manager)
    assert before_payload[field] != expected

    live_levels = []
    monkeypatch.setattr(general_intents, "apply_log_level", live_levels.append)
    target, name, fail = fail_save
    with monkeypatch.context() as patch:
        patch.setattr(target, name, fail)
        with pytest.raises(UserFacingError, match="Could not save settings") as error:
            asyncio.run(bus.dispatch_intent("app-settings", intent, args))
    assert "private/path" not in str(error.value)
    assert manager.state == before_state
    assert manager.state_file.read_bytes() == before_file
    assert not manager.state_file.with_name("session.dat.tmp").exists()
    assert recorder.messages == []
    assert live_levels == []
    assert settings_payload(manager) == before_payload
    assert settings_payload(SettingsManager(manager.state_file)) == before_payload

    asyncio.run(bus.dispatch_intent("app-settings", intent, args))
    assert recorder.messages[-1]["payload"][field] == expected
    assert settings_payload(manager)[field] == expected
    assert settings_payload(SettingsManager(manager.state_file))[field] == expected
    assert live_levels == (["DEBUG"] if intent == "setLogLevel" else [])


@pytest.mark.parametrize(("setter", "args", "expected"), [
    ("set_update_notifications_enabled", [True], {
        "update_notifications_enabled": True,
        "update_status_message": "Automatic update checks are enabled.",
    }),
    ("record_update_check_result", [{
        "message": "Update available.", "level": "warning",
        "checked_at": "2026-01-01T00:00:00Z", "remote_version": "2.0",
        "update_available": True,
    }], {
        "update_status_message": "Update available.",
        "update_status_level": "warning",
        "update_last_checked_at": "2026-01-01T00:00:00Z",
        "update_latest_version": "2.0",
        "update_available": True,
    }),
])
def test_update_preferences_and_status_commit_together(manager, fail_save, monkeypatch, setter, args, expected):
    before_state = deepcopy(manager.state)
    before_file = manager.state_file.read_bytes()
    target, name, fail = fail_save
    with monkeypatch.context() as patch:
        patch.setattr(target, name, fail)
        with pytest.raises(OSError, match="injected filesystem failure"):
            getattr(manager, setter)(*args)
    assert manager.state == before_state
    assert manager.state_file.read_bytes() == before_file
    assert not manager.state_file.with_name("session.dat.tmp").exists()

    getattr(manager, setter)(*args)
    restarted = SettingsManager(manager.state_file)
    for key, value in expected.items():
        assert manager.state[key] == value
        assert restarted.state[key] == value
