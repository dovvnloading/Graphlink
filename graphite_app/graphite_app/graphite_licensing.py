import json
from pathlib import Path

class SettingsManager:
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
                if 'ollama_reasoning_mode' not in state:
                    state['ollama_reasoning_mode'] = 'Thinking'
                if 'current_mode' not in state:
                    state['current_mode'] = 'Ollama (Local)'
                if 'api_provider' not in state:
                    state['api_provider'] = 'OpenAI-Compatible'
                if 'api_base_url' not in state:
                    state['api_base_url'] = 'https://api.openai.com/v1'
                if 'openai_api_key' not in state:
                    state['openai_api_key'] = ''
                if 'gemini_api_key' not in state:
                    state['gemini_api_key'] = ''
                if 'github_access_token' not in state:
                    state['github_access_token'] = ''
                if 'api_models' not in state:
                    state['api_models'] = {}
                if 'enable_system_prompt' not in state:
                    state['enable_system_prompt'] = True
                return state
        except (json.JSONDecodeError, IOError):
            return self._create_initial_state()

    def _create_initial_state(self):
        state = {
            "theme": "dark",
            "show_welcome_screen": True,
            "show_token_counter": True,
            "ollama_chat_model": "qwen3:8b",
            "ollama_reasoning_mode": "Thinking",
            "current_mode": "Ollama (Local)",
            "api_provider": "OpenAI-Compatible",
            "api_base_url": "https://api.openai.com/v1",
            "openai_api_key": "",
            "gemini_api_key": "",
            "github_access_token": "",
            "api_models": {},
            "enable_system_prompt": True,
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

    def get_ollama_chat_model(self):
        return self.state.get("ollama_chat_model", "qwen3:8b")

    def set_ollama_chat_model(self, model_name: str):
        self.state['ollama_chat_model'] = model_name
        self._save_state()

    def get_ollama_reasoning_mode(self):
        return self.state.get("ollama_reasoning_mode", "Thinking")

    def set_ollama_reasoning_mode(self, mode: str):
        if mode in ['Thinking', 'Quick']:
            self.state['ollama_reasoning_mode'] = mode
            self._save_state()
            
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
        
    def get_gemini_key(self):
        return self.state.get("gemini_api_key", "")

    def get_github_token(self):
        return self.state.get("github_access_token", "")
        
    def get_api_models(self):
        return self.state.get("api_models", {})

    def set_api_settings(self, provider: str, base_url: str, openai_key: str, gemini_key: str):
        self.state["api_provider"] = provider
        self.state["api_base_url"] = base_url
        self.state["openai_api_key"] = openai_key
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
        self.state["gemini_api_key"] = ""
        self.state["api_models"] = {}
        self._save_state()
