"""Contract tests for the settings island bridge.

Increment 2: activeSection navigation. Increment 3: the General/Appearance
page's real, persisted fields - the only page with no secrets and no
background workers.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_config as config
from graphlink_licensing import SettingsManager
from graphlink_settings_bridge import SECTION_NAMES, SettingsBridge
from graphlink_update import UPDATE_REPOSITORY_URL


def _bridge(tmp_path) -> SettingsBridge:
    return SettingsBridge(SettingsManager(tmp_path / "session.dat"))


def _last_payload(bridge: SettingsBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


class TestActiveSectionNavigation:
    def test_section_names_match_settings_dialogs_own_vocabulary(self):
        assert SECTION_NAMES == (
            "General",
            config.MODE_OLLAMA_LOCAL,
            config.MODE_LLAMACPP_LOCAL,
            config.MODE_API_ENDPOINT,
            "Integrations",
        )

    def test_ready_publishes_general_as_the_initial_section(self, tmp_path):
        bridge = _bridge(tmp_path)

        payload = _last_payload(bridge)

        assert payload["activeSection"] == "General"
        assert payload["schemaVersion"] == SettingsBridge.SCHEMA_VERSION
        assert payload["revision"] == 1

    def test_set_active_section_navigates_and_publishes(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setActiveSection(config.MODE_OLLAMA_LOCAL)

        payload = json.loads(states[-1])
        assert payload["activeSection"] == config.MODE_OLLAMA_LOCAL

    def test_set_active_section_to_the_same_section_is_a_no_op(self, tmp_path):
        bridge = _bridge(tmp_path)
        bridge.setActiveSection("Integrations")
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setActiveSection("Integrations")

        assert states == []

    def test_set_active_section_ignores_an_unrecognized_name(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setActiveSection("Not A Real Section")

        assert states == []

    def test_python_side_set_active_section_is_equivalent_to_the_slot(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.set_active_section(config.MODE_API_ENDPOINT)

        payload = json.loads(states[-1])
        assert payload["activeSection"] == config.MODE_API_ENDPOINT


class TestGeneralAppearancePage:
    def test_ready_publishes_the_persisted_defaults(self, tmp_path):
        bridge = _bridge(tmp_path)

        payload = _last_payload(bridge)

        assert payload["theme"] == "dark"
        assert payload["showTokenCounter"] is True
        assert payload["enableSystemPrompt"] is True
        assert payload["notificationPreferences"] == {
            "info": True,
            "success": True,
            "warning": True,
            "error": True,
        }
        assert payload["updateNotificationsEnabled"] is False
        assert payload["updateStatusMessage"] == "Automatic update checks are off."
        assert payload["updateStatusLevel"] == "info"
        assert payload["updateLastCheckedAt"] == ""
        assert payload["updateAvailable"] is False
        assert payload["githubTokenConfigured"] is False

    def test_set_theme_persists_applies_and_republishes(self, tmp_path):
        # apply_theme() mutates the process-global config.CURRENT_THEME -
        # same save/restore convention test_theme_tokens.py already uses for
        # every test that calls it, so this test doesn't leak state into
        # whatever runs after it.
        original_theme = config.CURRENT_THEME
        try:
            settings_manager = SettingsManager(tmp_path / "session.dat")
            bridge = SettingsBridge(settings_manager)
            states = []
            bridge.stateChanged.connect(states.append)

            bridge.setTheme("muted")

            assert settings_manager.get_theme() == "muted"
            assert config.CURRENT_THEME == "muted"
            payload = json.loads(states[-1])
            assert payload["theme"] == "muted"
        finally:
            config.CURRENT_THEME = original_theme

    def test_set_theme_ignores_an_unrecognized_name(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setTheme("not-a-real-theme")

        assert states == []

    def test_set_show_token_counter_persists_and_publishes(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setShowTokenCounter(False)

        assert settings_manager.get_show_token_counter() is False
        payload = _last_payload(bridge)
        assert payload["showTokenCounter"] is False

    def test_set_enable_system_prompt_persists_and_publishes(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setEnableSystemPrompt(False)

        assert settings_manager.get_enable_system_prompt() is False
        payload = _last_payload(bridge)
        assert payload["enableSystemPrompt"] is False

    def test_set_notification_preference_is_a_partial_update(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setNotificationPreference("warning", False)

        prefs = settings_manager.get_notification_preferences()
        assert prefs["warning"] is False
        assert prefs["info"] is True  # untouched
        payload = _last_payload(bridge)
        assert payload["notificationPreferences"]["warning"] is False

    def test_set_notification_preference_ignores_an_unrecognized_type(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setNotificationPreference("not-a-real-type", False)

        assert states == []

    def test_set_update_notifications_enabled_persists_and_publishes(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setUpdateNotificationsEnabled(True)

        assert settings_manager.get_update_notifications_enabled() is True
        payload = _last_payload(bridge)
        assert payload["updateNotificationsEnabled"] is True
        # set_update_notifications_enabled's own side effect (SettingsManager
        # updates the status message when toggled on) must be reflected too.
        assert payload["updateStatusMessage"] == "Automatic update checks are enabled."


class TestIntegrationsPage:
    def test_set_github_token_persists_and_reports_configured_true(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setGithubToken("ghp_realtoken123")

        assert settings_manager.get_github_token() == "ghp_realtoken123"
        payload = _last_payload(bridge)
        assert payload["githubTokenConfigured"] is True
        assert "ghp_realtoken123" not in json.dumps(payload)

    def test_set_github_token_strips_whitespace(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setGithubToken("  ghp_realtoken123  ")

        assert settings_manager.get_github_token() == "ghp_realtoken123"

    def test_whitespace_only_token_is_reported_as_not_configured(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setGithubToken("   ")

        payload = _last_payload(bridge)
        assert payload["githubTokenConfigured"] is False

    def test_clear_github_token_reports_configured_false(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        bridge.setGithubToken("ghp_realtoken123")

        bridge.clearGithubToken()

        assert settings_manager.get_github_token() == ""
        payload = _last_payload(bridge)
        assert payload["githubTokenConfigured"] is False


class _FakeMainWindow:
    """Duck-typed stand-in for ChatWindow, matching exactly the surface
    _notify_main_window_settings_changed()/checkForUpdates() call: an
    on_settings_changed() call counter and a check_for_updates(manual,
    status_target) that drives status_target the same way the real
    ChatWindow.check_for_updates() does (see graphlink_window.py), without
    a real UpdateCheckWorker QThread."""

    def __init__(self):
        self.on_settings_changed_calls = 0
        self.check_for_updates_calls = []

    def on_settings_changed(self):
        self.on_settings_changed_calls += 1

    def check_for_updates(self, manual=False, status_target=None):
        self.check_for_updates_calls.append(manual)
        if status_target is not None and hasattr(status_target, "set_update_check_in_progress"):
            status_target.set_update_check_in_progress(True)


class TestRealShellWiring:
    def test_general_appearance_intents_notify_the_main_window_when_present(self, tmp_path):
        main_window = _FakeMainWindow()
        bridge = SettingsBridge(SettingsManager(tmp_path / "session.dat"), main_window=main_window)

        bridge.setShowTokenCounter(False)
        bridge.setEnableSystemPrompt(False)
        bridge.setNotificationPreference("warning", False)
        bridge.setUpdateNotificationsEnabled(True)
        bridge.setTheme("muted")

        assert main_window.on_settings_changed_calls == 5

    def test_general_appearance_intents_are_a_no_op_without_a_main_window(self, tmp_path):
        bridge = _bridge(tmp_path)

        # No main_window was passed - this must not raise.
        bridge.setShowTokenCounter(False)

        assert _last_payload(bridge)["showTokenCounter"] is False

    def test_check_for_updates_delegates_to_the_main_window_and_sets_in_progress(self, tmp_path):
        main_window = _FakeMainWindow()
        bridge = SettingsBridge(SettingsManager(tmp_path / "session.dat"), main_window=main_window)

        bridge.checkForUpdates()

        assert main_window.check_for_updates_calls == [True]
        assert _last_payload(bridge)["updateCheckInProgress"] is True

    def test_check_for_updates_without_a_main_window_sets_a_notice(self, tmp_path):
        bridge = _bridge(tmp_path)

        bridge.checkForUpdates()

        assert _last_payload(bridge)["notice"] == "The main window is not available for update checks."

    def test_refresh_update_status_republishes_the_persisted_result(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        settings_manager.record_update_check_result({
            "success": True, "message": "You're up to date.", "level": "success",
            "update_available": False, "remote_version": "1.2.3",
        })

        bridge.refresh_update_status()

        payload = _last_payload(bridge)
        assert payload["updateStatusMessage"] == "You're up to date."
        assert payload["updateLatestVersion"] == "1.2.3"

    def test_set_update_check_in_progress_updates_the_payload(self, tmp_path):
        bridge = _bridge(tmp_path)

        bridge.set_update_check_in_progress(True)
        assert _last_payload(bridge)["updateCheckInProgress"] is True

        bridge.set_update_check_in_progress(False)
        assert _last_payload(bridge)["updateCheckInProgress"] is False

    def test_open_repository_opens_the_real_repository_url(self, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr(
            "graphlink_settings_bridge.webbrowser.open",
            lambda url: opened.append(url),
        )
        bridge = _bridge(tmp_path)

        bridge.openRepository()

        assert opened == [UPDATE_REPOSITORY_URL]


class TestLifecycle:
    def test_publish_is_a_no_op_after_dispose(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.dispose()
        bridge.setActiveSection(config.MODE_OLLAMA_LOCAL)

        assert states == []
        assert bridge.disposed is True

    def test_revision_increments_monotonically_across_calls(self, tmp_path):
        bridge = _bridge(tmp_path)
        revisions = []
        bridge.stateChanged.connect(lambda payload: revisions.append(json.loads(payload)["revision"]))

        bridge.ready()
        bridge.setActiveSection(config.MODE_OLLAMA_LOCAL)
        bridge.setActiveSection("Integrations")

        assert revisions == [1, 2, 3]
