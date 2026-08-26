"""GeneralSettingsOps - general app preferences for SettingsManager: the
token-counter overlay, system prompt toggle, onboarding flag, auto-model
routing policy, log level, theme, notification preferences, and the
update-check status fields.

A MIXIN, not a standalone class: every method operates on the composing
class's own state (self.state) and shared constants (self.NOTIFICATION_TYPES,
self.LOG_LEVELS, self.THEMES) - it is composed exactly once, by
graphlink_settings_store.py's `class SettingsManager(PersistenceOps,
GeneralSettingsOps, ...)`.

Method bodies are relocated VERBATIM from graphlink_settings_store.py; only
the class wrapper is new.
"""

from __future__ import annotations

from datetime import datetime, timezone


class GeneralSettingsOps:

    def get_show_token_counter(self):
        # R8a: off by default - the overlay is opt-in now, not opt-out.
        return self.state.get("show_token_counter", False)

    def set_show_token_counter(self, show: bool):
        self.state['show_token_counter'] = show
        self._save_state()

    def get_enable_system_prompt(self):
        return self.state.get("enable_system_prompt", True)

    def set_enable_system_prompt(self, enabled: bool):
        self.state["enable_system_prompt"] = bool(enabled)
        self._save_state()

    def get_has_completed_onboarding(self):
        # ADR-012 stage 12.6: False by default so a fresh machine's first
        # launch auto-opens the onboarding wizard (OnboardingDialog.tsx) -
        # the wizard sets this True itself on dismiss/completion so it never
        # auto-shows again, mirroring show_token_counter's own default-False/
        # explicit-opt-in shape just above.
        return self.state.get("has_completed_onboarding", False)

    def set_has_completed_onboarding(self, completed: bool):
        self.state["has_completed_onboarding"] = bool(completed)
        self._save_state()

    def get_auto_model_policy(self):
        # ADR-018 stage 18.4. "cheapest-capable" by default - matching the
        # ADR's own framing (cost-aware routing is the headline feature;
        # "fastest"/"best-quality" are deliberate opt-ins).
        from graphlink_model_catalog import AUTO_POLICY_CHEAPEST_CAPABLE

        return self.state.get("auto_model_policy", AUTO_POLICY_CHEAPEST_CAPABLE)

    def set_auto_model_policy(self, policy: str):
        from graphlink_model_catalog import AUTO_POLICIES

        if policy in AUTO_POLICIES:
            self.state["auto_model_policy"] = policy
            self._save_state()

    def get_log_level(self):
        # ADR-016 stage 16.1. INFO by default - matches
        # backend/crash_recovery.py's own pre-existing default so a fresh
        # install's boot-time behavior is unchanged until a user opts into
        # more or less verbosity.
        return self.state.get("log_level", "INFO")

    def set_log_level(self, level: str):
        if level in self.LOG_LEVELS:
            self.state["log_level"] = level
            self._save_state()

    def get_theme(self):
        # ADR-012 stage 12.2. "system" by default - the app never forces a
        # theme choice on a user who hasn't made one.
        return self.state.get("theme", "system")

    def set_theme(self, theme: str):
        if theme in self.THEMES:
            self.state["theme"] = theme
            self._save_state()

    def get_notification_preferences(self):
        saved_preferences = self.state.get("notification_preferences", {}) or {}
        return {
            notification_type: bool(saved_preferences.get(notification_type, True))
            for notification_type in self.NOTIFICATION_TYPES
        }

    def get_notification_type_enabled(self, notification_type: str):
        normalized_type = str(notification_type or "info").strip().lower()
        return self.get_notification_preferences().get(normalized_type, True)

    def set_notification_preferences(self, preferences: dict):
        current_preferences = self.get_notification_preferences()
        for notification_type in self.NOTIFICATION_TYPES:
            if notification_type in preferences:
                current_preferences[notification_type] = bool(preferences[notification_type])
        self.state["notification_preferences"] = current_preferences
        self._save_state()

    def get_update_notifications_enabled(self):
        return self.state.get("update_notifications_enabled", False)

    def set_update_notifications_enabled(self, enabled: bool):
        self.state["update_notifications_enabled"] = bool(enabled)
        if enabled and self.state.get("update_status_message") == "Automatic update checks are off.":
            self.state["update_status_message"] = "Automatic update checks are enabled."
        elif not enabled:
            self.state["update_status_message"] = "Automatic update checks are off."
            self.state["update_status_level"] = "info"
        self._save_state()

    def get_update_status_message(self):
        return self.state.get("update_status_message", "Automatic update checks are off.")

    def get_update_status_level(self):
        return self.state.get("update_status_level", "info")

    def get_update_last_checked_at(self):
        return self.state.get("update_last_checked_at", "")

    def get_update_latest_version(self):
        return self.state.get("update_latest_version", "")

    def get_update_available(self):
        return self.state.get("update_available", False)

    def record_update_check_result(self, result: dict):
        result = result or {}
        self.state["update_status_message"] = str(result.get("message", "Update check finished.")).strip()
        self.state["update_status_level"] = str(result.get("level", "info")).strip() or "info"
        self.state["update_last_checked_at"] = str(
            result.get("checked_at") or datetime.now(timezone.utc).isoformat()
        )
        self.state["update_latest_version"] = str(result.get("remote_version", "")).strip()
        self.state["update_available"] = bool(result.get("update_available", False))
        self._save_state()
