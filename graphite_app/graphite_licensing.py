import json
import os
from datetime import datetime, timezone
from pathlib import Path

import graphite_secrets


def _is_llama_cpp_gguf_path(path_value) -> bool:
    normalized = str(path_value or "").strip()
    return bool(normalized) and normalized.lower().endswith(".gguf")


class SettingsManager:
    NOTIFICATION_TYPES = ("info", "success", "warning", "error")
    # Bumped whenever session.dat's shape changes in a way future code needs to branch
    # on. No migration logic reads this yet (see doc/ARCHITECTURE_REVIEW_FINDINGS.md #49)
    # - it's the version marker itself, not a migration framework. Existing pre-this-field
    # files backfill to 1 on next load, same as every other key here.
    CURRENT_SCHEMA_VERSION = 1

    """
    Manages all persistent application state and user settings.

    This class reads from and writes to a local state file (`session.dat`) to
    persist data across application launches.
    """
    # Settings fields that hold secrets - encrypted at rest via graphite_secrets
    # (Windows DPAPI, "dpapi:"-prefixed values; see that module for the tradeoffs).
    SECRET_KEYS = ("openai_api_key", "anthropic_api_key", "gemini_api_key", "github_access_token")

    def __init__(self, state_file: Path | str | None = None):
        self.state_file = Path(state_file) if state_file is not None else Path.home() / '.graphlink' / 'session.dat'
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()
        self._migrate_plaintext_secrets()

    def _migrate_plaintext_secrets(self):
        """Encrypt any legacy plaintext secret still on disk from before #14 was fixed.

        Runs once per launch: if DPAPI is available and a secret field holds a
        plaintext value, re-protect it and persist immediately so the plaintext leaves
        disk on the first launch after upgrading, not whenever the user next happens
        to touch a setting. Where DPAPI is unavailable, protect() returns the value
        unchanged, so nothing is rewritten and nothing regresses."""
        migrated = False
        for key in self.SECRET_KEYS:
            current_value = str(self.state.get(key, "") or "")
            protected_value = graphite_secrets.protect(current_value)
            if protected_value != current_value:
                self.state[key] = protected_value
                migrated = True
        if migrated:
            self._save_state()

    def _load_state(self):
        if not self.state_file.exists():
            return self._create_initial_state()
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                if 'theme' not in state:
                    state['theme'] = 'dark'
                if 'show_token_counter' not in state:
                    state['show_token_counter'] = True
                if 'ollama_chat_model' not in state:
                    state['ollama_chat_model'] = 'qwen3:8b'
                if 'ollama_title_model' not in state:
                    state['ollama_title_model'] = ''
                if 'ollama_chart_model' not in state:
                    state['ollama_chart_model'] = ''
                if 'ollama_web_validate_model' not in state:
                    state['ollama_web_validate_model'] = ''
                if 'ollama_web_summarize_model' not in state:
                    state['ollama_web_summarize_model'] = ''
                if 'ollama_reasoning_mode' not in state:
                    state['ollama_reasoning_mode'] = 'Thinking'
                if 'ollama_scanned_models' not in state:
                    state['ollama_scanned_models'] = []
                if 'ollama_model_scan_mode' not in state:
                    state['ollama_model_scan_mode'] = ''
                if 'ollama_model_scan_path' not in state:
                    state['ollama_model_scan_path'] = ''
                if 'ollama_model_scan_locations' not in state:
                    state['ollama_model_scan_locations'] = []
                if 'llama_cpp_chat_model_path' not in state:
                    state['llama_cpp_chat_model_path'] = ''
                if 'llama_cpp_title_model_path' not in state:
                    state['llama_cpp_title_model_path'] = ''
                if 'llama_cpp_reasoning_mode' not in state:
                    state['llama_cpp_reasoning_mode'] = 'Thinking'
                if 'llama_cpp_chat_format' not in state:
                    state['llama_cpp_chat_format'] = ''
                if 'llama_cpp_n_ctx' not in state:
                    state['llama_cpp_n_ctx'] = 4096
                if 'llama_cpp_n_gpu_layers' not in state:
                    state['llama_cpp_n_gpu_layers'] = 0
                if 'llama_cpp_n_threads' not in state:
                    state['llama_cpp_n_threads'] = 0
                if 'llama_cpp_scanned_models' not in state:
                    state['llama_cpp_scanned_models'] = []
                if 'llama_cpp_model_scan_mode' not in state:
                    state['llama_cpp_model_scan_mode'] = ''
                if 'llama_cpp_model_scan_path' not in state:
                    state['llama_cpp_model_scan_path'] = ''
                if 'llama_cpp_model_scan_locations' not in state:
                    state['llama_cpp_model_scan_locations'] = []
                if 'current_mode' not in state:
                    state['current_mode'] = 'Ollama (Local)'
                if 'api_provider' not in state:
                    state['api_provider'] = 'OpenAI-Compatible'
                if 'api_base_url' not in state:
                    state['api_base_url'] = 'https://api.openai.com/v1'
                if 'openai_api_key' not in state:
                    state['openai_api_key'] = ''
                if 'anthropic_api_key' not in state:
                    state['anthropic_api_key'] = ''
                if 'gemini_api_key' not in state:
                    state['gemini_api_key'] = ''
                if 'github_access_token' not in state:
                    state['github_access_token'] = ''
                if 'api_models' not in state:
                    state['api_models'] = {}
                if 'enable_system_prompt' not in state:
                    state['enable_system_prompt'] = True
                if 'update_notifications_enabled' not in state:
                    state['update_notifications_enabled'] = False
                if 'notification_preferences' not in state or not isinstance(state.get('notification_preferences'), dict):
                    state['notification_preferences'] = {}
                for notification_type in self.NOTIFICATION_TYPES:
                    if notification_type not in state['notification_preferences']:
                        state['notification_preferences'][notification_type] = True
                if 'update_status_message' not in state:
                    state['update_status_message'] = 'Automatic update checks are off.'
                if 'update_status_level' not in state:
                    state['update_status_level'] = 'info'
                if 'update_last_checked_at' not in state:
                    state['update_last_checked_at'] = ''
                if 'update_latest_version' not in state:
                    state['update_latest_version'] = ''
                if 'update_available' not in state:
                    state['update_available'] = False
                if 'schema_version' not in state:
                    state['schema_version'] = self.CURRENT_SCHEMA_VERSION
                return state
        except (json.JSONDecodeError, IOError) as e:
            self._backup_corrupt_state_file(e)
            return self._create_initial_state()

    def _backup_corrupt_state_file(self, error):
        # Preserve the unreadable file for forensic recovery instead of silently
        # overwriting it with defaults - previously a corrupt session.dat (which,
        # pre-atomic-write, could happen from a crash mid-save) was destroyed with no
        # trace and no warning the moment it failed to parse.
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = self.state_file.with_name(f"{self.state_file.name}.corrupted-{timestamp}")
            self.state_file.replace(backup_path)
            print(
                f"Warning: {self.state_file} could not be read ({error}). "
                f"Backed it up to {backup_path} and reset settings to defaults."
            )
        except OSError as backup_error:
            print(
                f"Warning: {self.state_file} could not be read ({error}) and could not "
                f"be backed up ({backup_error}). Resetting settings to defaults."
            )

    def _create_initial_state(self):
        state = {
            "schema_version": self.CURRENT_SCHEMA_VERSION,
            "theme": "dark",
            "show_token_counter": True,
            "ollama_chat_model": "qwen3:8b",
            "ollama_title_model": "",
            "ollama_chart_model": "",
            "ollama_web_validate_model": "",
            "ollama_web_summarize_model": "",
            "ollama_reasoning_mode": "Thinking",
            "ollama_scanned_models": [],
            "ollama_model_scan_mode": "",
            "ollama_model_scan_path": "",
            "ollama_model_scan_locations": [],
            "llama_cpp_chat_model_path": "",
            "llama_cpp_title_model_path": "",
            "llama_cpp_reasoning_mode": "Thinking",
            "llama_cpp_chat_format": "",
            "llama_cpp_n_ctx": 4096,
            "llama_cpp_n_gpu_layers": 0,
            "llama_cpp_n_threads": 0,
            "llama_cpp_scanned_models": [],
            "llama_cpp_model_scan_mode": "",
            "llama_cpp_model_scan_path": "",
            "llama_cpp_model_scan_locations": [],
            "current_mode": "Ollama (Local)",
            "api_provider": "OpenAI-Compatible",
            "api_base_url": "https://api.openai.com/v1",
            "openai_api_key": "",
            "anthropic_api_key": "",
            "gemini_api_key": "",
            "github_access_token": "",
            "api_models": {},
            "enable_system_prompt": True,
            "update_notifications_enabled": False,
            "notification_preferences": {notification_type: True for notification_type in self.NOTIFICATION_TYPES},
            "update_status_message": "Automatic update checks are off.",
            "update_status_level": "info",
            "update_last_checked_at": "",
            "update_latest_version": "",
            "update_available": False,
        }
        self._save_state(state)
        return state

    def _save_state(self, state_data=None):
        # Write to a temp file and atomically rename it into place (os.replace is
        # atomic on both Windows and POSIX when source/dest are on the same volume,
        # guaranteed here since the temp file lives next to state_file). Previously
        # this wrote directly to state_file - a crash or power loss mid-write left a
        # truncated/corrupt file, which _load_state's JSONDecodeError handler then
        # silently replaced with defaults, destroying every saved API key and
        # preference with no warning. Now a crash can only ever leave the *temp* file
        # incomplete; state_file itself is always either the old complete version or
        # the new complete version, never something in between.
        data_to_save = state_data if state_data else self.state
        tmp_path = self.state_file.with_name(self.state_file.name + ".tmp")
        try:
            with open(tmp_path, 'w') as f:
                json.dump(data_to_save, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.state_file)
        except IOError as e:
            print(f"Error: Could not save session state to {self.state_file}. Reason: {e}")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def get_schema_version(self):
        return self.state.get("schema_version", self.CURRENT_SCHEMA_VERSION)

    def get_theme(self):
        return self.state.get("theme", "dark")

    def set_theme(self, theme_name):
        self.state['theme'] = theme_name
        self._save_state()

    def get_show_token_counter(self):
        return self.state.get("show_token_counter", True)

    def set_show_token_counter(self, show: bool):
        self.state['show_token_counter'] = show
        self._save_state()

    def get_enable_system_prompt(self):
        return self.state.get("enable_system_prompt", True)

    def set_enable_system_prompt(self, enabled: bool):
        self.state["enable_system_prompt"] = bool(enabled)
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

    def get_ollama_chat_model(self):
        return self.state.get("ollama_chat_model", "qwen3:8b")

    def set_ollama_chat_model(self, model_name: str):
        self.state['ollama_chat_model'] = model_name
        self._save_state()

    def get_ollama_title_model(self):
        title_model = str(self.state.get("ollama_title_model", "")).strip()
        if title_model:
            return title_model
        return self.get_ollama_chat_model()

    def set_ollama_title_model(self, model_name: str):
        self.state["ollama_title_model"] = str(model_name or "").strip()
        self._save_state()

    def get_ollama_chart_model(self):
        # Chart generation defaults to a code-specialized model, not the chat model -
        # deepseek-coder:6.7b is a deliberately different default from qwen3:8b, not an
        # unwired copy of it. Still fully overridable via OllamaSettingsWidget.
        chart_model = str(self.state.get("ollama_chart_model", "")).strip()
        if chart_model:
            return chart_model
        return "deepseek-coder:6.7b"

    def set_ollama_chart_model(self, model_name: str):
        self.state["ollama_chart_model"] = str(model_name or "").strip()
        self._save_state()

    def get_ollama_web_validate_model(self):
        web_model = str(self.state.get("ollama_web_validate_model", "")).strip()
        if web_model:
            return web_model
        return self.get_ollama_chat_model()

    def set_ollama_web_validate_model(self, model_name: str):
        self.state["ollama_web_validate_model"] = str(model_name or "").strip()
        self._save_state()

    def get_ollama_web_summarize_model(self):
        web_model = str(self.state.get("ollama_web_summarize_model", "")).strip()
        if web_model:
            return web_model
        return self.get_ollama_chat_model()

    def set_ollama_web_summarize_model(self, model_name: str):
        self.state["ollama_web_summarize_model"] = str(model_name or "").strip()
        self._save_state()

    def get_ollama_reasoning_mode(self):
        return self.state.get("ollama_reasoning_mode", "Thinking")

    def set_ollama_reasoning_mode(self, mode: str):
        if mode in ['Thinking', 'Quick']:
            self.state['ollama_reasoning_mode'] = mode
            self._save_state()

    def get_ollama_scanned_models(self):
        models = self.state.get("ollama_scanned_models", [])
        if not isinstance(models, list):
            return []
        return [str(model).strip() for model in models if str(model).strip()]

    def get_ollama_model_scan_mode(self):
        return str(self.state.get("ollama_model_scan_mode", "")).strip()

    def get_ollama_model_scan_path(self):
        return str(self.state.get("ollama_model_scan_path", "")).strip()

    def get_ollama_model_scan_locations(self):
        locations = self.state.get("ollama_model_scan_locations", [])
        if not isinstance(locations, list):
            return []
        return [str(location).strip() for location in locations if str(location).strip()]

    def set_ollama_model_scan_cache(self, models: list[str], scan_mode: str = "", scan_path: str = "", locations: list[str] | None = None):
        self.state["ollama_scanned_models"] = sorted(
            {str(model).strip() for model in (models or []) if str(model).strip()},
            key=str.lower,
        )
        self.state["ollama_model_scan_mode"] = str(scan_mode or "").strip()
        self.state["ollama_model_scan_path"] = str(scan_path or "").strip()
        self.state["ollama_model_scan_locations"] = sorted(
            {str(location).strip() for location in (locations or []) if str(location).strip()},
            key=str.lower,
        )
        self._save_state()

    def get_llama_cpp_chat_model_path(self):
        return str(self.state.get("llama_cpp_chat_model_path", "")).strip()

    def set_llama_cpp_chat_model_path(self, model_path: str):
        self.state["llama_cpp_chat_model_path"] = str(model_path or "").strip()
        self._save_state()

    def get_llama_cpp_title_model_path(self):
        title_model = str(self.state.get("llama_cpp_title_model_path", "")).strip()
        if title_model:
            return title_model
        return self.get_llama_cpp_chat_model_path()

    def get_llama_cpp_title_model_override_path(self):
        return str(self.state.get("llama_cpp_title_model_path", "")).strip()

    def set_llama_cpp_title_model_path(self, model_path: str):
        self.state["llama_cpp_title_model_path"] = str(model_path or "").strip()
        self._save_state()

    def get_llama_cpp_reasoning_mode(self):
        return self.state.get("llama_cpp_reasoning_mode", "Thinking")

    def set_llama_cpp_reasoning_mode(self, mode: str):
        if mode in ['Thinking', 'Quick']:
            self.state['llama_cpp_reasoning_mode'] = mode
            self._save_state()

    def get_llama_cpp_chat_format(self):
        return str(self.state.get("llama_cpp_chat_format", "")).strip()

    def set_llama_cpp_chat_format(self, chat_format: str):
        self.state["llama_cpp_chat_format"] = str(chat_format or "").strip()
        self._save_state()

    def get_llama_cpp_n_ctx(self):
        try:
            return int(self.state.get("llama_cpp_n_ctx", 4096))
        except (TypeError, ValueError):
            return 4096

    def get_llama_cpp_n_gpu_layers(self):
        try:
            return int(self.state.get("llama_cpp_n_gpu_layers", 0))
        except (TypeError, ValueError):
            return 0

    def get_llama_cpp_n_threads(self):
        try:
            return int(self.state.get("llama_cpp_n_threads", 0))
        except (TypeError, ValueError):
            return 0

    def get_llama_cpp_scanned_models(self):
        models = self.state.get("llama_cpp_scanned_models", [])
        if not isinstance(models, list):
            return []
        return [
            str(model).strip()
            for model in models
            if _is_llama_cpp_gguf_path(model)
        ]

    def get_llama_cpp_model_scan_mode(self):
        return str(self.state.get("llama_cpp_model_scan_mode", "")).strip()

    def get_llama_cpp_model_scan_path(self):
        return str(self.state.get("llama_cpp_model_scan_path", "")).strip()

    def get_llama_cpp_model_scan_locations(self):
        locations = self.state.get("llama_cpp_model_scan_locations", [])
        if not isinstance(locations, list):
            return []
        return [str(location).strip() for location in locations if str(location).strip()]

    def set_llama_cpp_runtime(self, *, n_ctx: int, n_gpu_layers: int, n_threads: int, chat_format: str):
        self.state["llama_cpp_n_ctx"] = max(256, int(n_ctx))
        self.state["llama_cpp_n_gpu_layers"] = int(n_gpu_layers)
        self.state["llama_cpp_n_threads"] = max(0, int(n_threads))
        self.state["llama_cpp_chat_format"] = str(chat_format or "").strip()
        self._save_state()

    def set_llama_cpp_model_scan_cache(self, models: list[str], scan_mode: str = "", scan_path: str = "", locations: list[str] | None = None):
        self.state["llama_cpp_scanned_models"] = sorted(
            {
                str(model).strip()
                for model in (models or [])
                if _is_llama_cpp_gguf_path(model)
            },
            key=str.lower,
        )
        self.state["llama_cpp_model_scan_mode"] = str(scan_mode or "").strip()
        self.state["llama_cpp_model_scan_path"] = str(scan_path or "").strip()
        self.state["llama_cpp_model_scan_locations"] = sorted(
            {str(location).strip() for location in (locations or []) if str(location).strip()},
            key=str.lower,
        )
        self._save_state()

    def get_llama_cpp_settings(self):
        return {
            "chat_model_path": self.get_llama_cpp_chat_model_path(),
            "title_model_path": self.get_llama_cpp_title_model_override_path(),
            "reasoning_mode": self.get_llama_cpp_reasoning_mode(),
            "chat_format": self.get_llama_cpp_chat_format(),
            "n_ctx": self.get_llama_cpp_n_ctx(),
            "n_gpu_layers": self.get_llama_cpp_n_gpu_layers(),
            "n_threads": self.get_llama_cpp_n_threads(),
        }
            
    def get_current_mode(self):
        return self.state.get("current_mode", "Ollama (Local)")

    def set_current_mode(self, mode: str):
        self.state["current_mode"] = mode
        self._save_state()

    def get_api_provider(self):
        return self.state.get("api_provider", "OpenAI-Compatible")
        
    def get_api_base_url(self):
        return self.state.get("api_base_url", "https://api.openai.com/v1")
        
    def get_openai_key(self):
        return graphite_secrets.unprotect(self.state.get("openai_api_key", ""))

    def get_anthropic_key(self):
        return graphite_secrets.unprotect(self.state.get("anthropic_api_key", ""))

    def get_gemini_key(self):
        return graphite_secrets.unprotect(self.state.get("gemini_api_key", ""))

    def get_github_token(self):
        return graphite_secrets.unprotect(self.state.get("github_access_token", ""))
        
    def get_api_models(self):
        return self.state.get("api_models", {})

    def set_api_settings(
        self,
        provider: str,
        base_url: str,
        openai_key: str,
        anthropic_key: str,
        gemini_key: str,
    ):
        self.state["api_provider"] = provider
        self.state["api_base_url"] = base_url
        self.state["openai_api_key"] = graphite_secrets.protect(openai_key)
        self.state["anthropic_api_key"] = graphite_secrets.protect(anthropic_key)
        self.state["gemini_api_key"] = graphite_secrets.protect(gemini_key)
        self._save_state()

    def set_api_models(self, models_dict: dict):
        self.state["api_models"] = models_dict
        self._save_state()

    def set_github_token(self, token: str):
        self.state["github_access_token"] = graphite_secrets.protect(token)
        self._save_state()

    def reset_api_settings(self):
        self.state["api_provider"] = "OpenAI-Compatible"
        self.state["api_base_url"] = "https://api.openai.com/v1"
        self.state["openai_api_key"] = ""
        self.state["anthropic_api_key"] = ""
        self.state["gemini_api_key"] = ""
        self.state["api_models"] = {}
        self._save_state()
