import json
from datetime import datetime, timezone
from pathlib import Path


def _is_llama_cpp_gguf_path(path_value) -> bool:
    normalized = str(path_value or "").strip()
    return bool(normalized) and normalized.lower().endswith(".gguf")


class SettingsManager:
    NOTIFICATION_TYPES = ("info", "success", "warning", "error")

    """
    Manages all persistent application state and user settings.

    This class reads from and writes to a local state file (`session.dat`) to
    persist data across application launches.
    """
    def __init__(self):
        self.state_file = Path.home() / '.graphlink' / 'session.dat'
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self):
        if not self.state_file.exists():
            return self._create_initial_state()
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                if 'theme' not in state:
                    state['theme'] = 'dark'
                if 'show_welcome_screen' not in state:
                    state['show_welcome_screen'] = True
                if 'show_token_counter' not in state:
                    state['show_token_counter'] = True
                if 'ollama_chat_model' not in state:
                    state['ollama_chat_model'] = 'qwen3:8b'
                if 'ollama_title_model' not in state:
                    state['ollama_title_model'] = ''
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
                return state
        except (json.JSONDecodeError, IOError):
            return self._create_initial_state()

    def _create_initial_state(self):
        state = {
            "theme": "dark",
            "show_welcome_screen": True,
            "show_token_counter": True,
            "ollama_chat_model": "qwen3:8b",
            "ollama_title_model": "",
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
        data_to_save = state_data if state_data else self.state
        try:
            with open(self.state_file, 'w') as f:
                json.dump(data_to_save, f, indent=4)
        except IOError as e:
            print(f"Error: Could not save session state to {self.state_file}. Reason: {e}")

    def get_theme(self):
        return self.state.get("theme", "dark")

    def set_theme(self, theme_name):
        self.state['theme'] = theme_name
        self._save_state()

    def get_show_welcome_screen(self):
        return self.state.get("show_welcome_screen", True)

    def set_show_welcome_screen(self, show: bool):
        self.state['show_welcome_screen'] = show
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
        return self.state.get("openai_api_key", "")

    def get_anthropic_key(self):
        return self.state.get("anthropic_api_key", "")
        
    def get_gemini_key(self):
        return self.state.get("gemini_api_key", "")

    def get_github_token(self):
        return self.state.get("github_access_token", "")
        
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
        self.state["openai_api_key"] = openai_key
        self.state["anthropic_api_key"] = anthropic_key
        self.state["gemini_api_key"] = gemini_key
        self._save_state()

    def set_api_models(self, models_dict: dict):
        self.state["api_models"] = models_dict
        self._save_state()

    def set_github_token(self, token: str):
        self.state["github_access_token"] = token
        self._save_state()

    def reset_api_settings(self):
        self.state["api_provider"] = "OpenAI-Compatible"
        self.state["api_base_url"] = "https://api.openai.com/v1"
        self.state["openai_api_key"] = ""
        self.state["anthropic_api_key"] = ""
        self.state["gemini_api_key"] = ""
        self.state["api_models"] = {}
        self._save_state()
