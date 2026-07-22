"""Settings dialog topic tests (Qt-removal plan R2.5d)."""

import asyncio

import pytest
from graphlink_licensing import SettingsManager

from backend.events import SessionBus
from backend.settings import register_settings, settings_payload


@pytest.fixture
def manager(tmp_path):
    return SettingsManager(tmp_path / "session.dat")


class Recorder:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


def test_settings_payload_shape_matches_generated_validator_shape(manager):
    payload = settings_payload(manager)
    assert set(payload) == {
        "theme",
        "showTokenCounter",
        "enableSystemPrompt",
        "notificationPreferences",
        "githubTokenConfigured",
    }


def test_settings_payload_reflects_real_manager_defaults(manager):
    payload = settings_payload(manager)
    assert payload["theme"] == "dark"
    assert payload["showTokenCounter"] is True
    assert payload["enableSystemPrompt"] is True
    assert payload["githubTokenConfigured"] is False
    assert set(payload["notificationPreferences"]) == set(SettingsManager.NOTIFICATION_TYPES)


def test_settings_never_imports_qt():
    import sys

    assert "PySide6" not in sys.modules


def test_register_settings_publishes_active_section_alongside_manager_state(manager):
    bus = SessionBus("settings-test")
    register_settings(bus, manager)

    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(bus.publish("app-settings"))
    payload = recorder.messages[0]["payload"]
    assert payload["activeSection"] == "general"
    assert payload["theme"] == "dark"


def test_set_active_section_intent_updates_only_local_ui_state(manager):
    bus = SessionBus("settings-active-section-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setActiveSection", ["integrations"]))
    payload = recorder.messages[-1]["payload"]
    assert payload["activeSection"] == "integrations"


def test_set_theme_intent_persists_to_the_real_settings_manager(manager):
    bus = SessionBus("settings-theme-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setTheme", ["light"]))
    assert manager.get_theme() == "light"
    # A fresh manager reading the same file proves it was actually persisted,
    # not just mutated in memory.
    reloaded = SettingsManager(manager.state_file)
    assert reloaded.get_theme() == "light"


def test_set_show_token_counter_intent(manager):
    bus = SessionBus("settings-token-counter-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setShowTokenCounter", [False]))
    assert manager.get_show_token_counter() is False


def test_set_enable_system_prompt_intent(manager):
    bus = SessionBus("settings-system-prompt-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setEnableSystemPrompt", [False]))
    assert manager.get_enable_system_prompt() is False


def test_set_notification_preference_intent_updates_a_single_type(manager):
    bus = SessionBus("settings-notification-pref-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setNotificationPreference", ["warning", False]))
    prefs = manager.get_notification_preferences()
    assert prefs["warning"] is False
    # Untouched types keep their default - a single-field update, not a
    # wholesale replace.
    assert prefs["info"] is True


def test_set_github_token_intent_persists_and_reports_configured(manager):
    bus = SessionBus("settings-github-token-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setGithubToken", ["ghp_abc123"]))
    assert manager.get_github_token() == "ghp_abc123"
    payload = settings_payload(manager)
    assert payload["githubTokenConfigured"] is True
    # Write-only: the payload never carries the raw token value.
    assert "ghp_abc123" not in payload.values()


def test_clear_github_token_intent(manager):
    bus = SessionBus("settings-github-token-clear-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setGithubToken", ["ghp_abc123"]))
    asyncio.run(bus.dispatch_intent("app-settings", "clearGithubToken", []))
    assert manager.get_github_token() == ""
    assert settings_payload(manager)["githubTokenConfigured"] is False
