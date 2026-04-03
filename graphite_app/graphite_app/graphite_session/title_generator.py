from datetime import datetime

import ollama

import api_provider
import graphite_config as config


class TitleGenerator:
    """Generate concise titles for new chat sessions."""

    def __init__(self, settings_manager=None):
        self.settings_manager = settings_manager
        self.system_prompt = """You are a title generation assistant. Your only job is to create short,
        2-3 word titles based on conversation content. Rules:
        - ONLY output the title, nothing else
        - Keep it between 2-3 words
        - Use title case
        - Make it descriptive but concise
        - NO punctuation
        - NO explanations
        - NO additional text"""

    def _get_setting(self, getter_name, default=""):
        if self.settings_manager and hasattr(self.settings_manager, getter_name):
            try:
                value = getattr(self.settings_manager, getter_name)()
            except Exception:
                value = default
            if value is not None:
                return str(value).strip()
        return str(default).strip()

    def _get_installed_ollama_models(self):
        list_fn = getattr(ollama, "list", None)
        if not callable(list_fn):
            return []

        try:
            response = list_fn()
        except Exception as exc:
            print(f"Warning: Could not inspect installed Ollama models: {exc}")
            return []

        raw_models = []
        if isinstance(response, dict):
            raw_models = response.get("models", []) or []
        else:
            raw_models = getattr(response, "models", None) or []

        installed_models = []
        for item in raw_models:
            if isinstance(item, str):
                model_name = item
            elif isinstance(item, dict):
                model_name = item.get("name") or item.get("model") or item.get("id")
            else:
                model_name = getattr(item, "name", None) or getattr(item, "model", None) or getattr(item, "id", None)

            model_name = str(model_name or "").strip()
            if model_name and model_name not in installed_models:
                installed_models.append(model_name)

        return installed_models

    def _collect_ollama_model_candidates(self):
        candidates = []
        for model_name in (
            self._get_setting("get_ollama_title_model"),
            self._get_setting("get_ollama_chat_model"),
            str(config.OLLAMA_MODELS.get(config.TASK_TITLE, "")).strip(),
            str(config.OLLAMA_MODELS.get(config.TASK_CHAT, "")).strip(),
        ):
            if model_name and model_name not in candidates:
                candidates.append(model_name)

        installed_models = self._get_installed_ollama_models()
        if installed_models:
            installed_candidates = [model_name for model_name in candidates if model_name in installed_models]
            fallback_installed = [model_name for model_name in installed_models if model_name not in installed_candidates]
            if installed_candidates:
                return installed_candidates + fallback_installed
            return fallback_installed or candidates

        return candidates

    def _is_missing_model_error(self, exc):
        message = str(exc).lower()
        return "not found" in message or "status code: 404" in message or ("404" in message and "model" in message)

    def _generate_ollama_title(self, message):
        prompt = f"Create a 2-3 word title for this message: {message}"
        last_error = None

        for model_name in self._collect_ollama_model_candidates():
            try:
                response = ollama.generate(
                    model=model_name,
                    system=self.system_prompt,
                    prompt=prompt,
                )
                title = ""
                if isinstance(response, dict):
                    title = response.get("response", "")
                else:
                    title = getattr(response, "response", "")

                title = str(title).strip()
                if title:
                    return title
            except Exception as exc:
                if self._is_missing_model_error(exc):
                    last_error = exc
                    continue
                raise

        if last_error:
            raise last_error

        raise ValueError("No Ollama model configured for chat naming.")

    def generate_title(self, message):
        try:
            if api_provider.USE_API_MODE:
                messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"Create a 2-3 word title for this message: {message}"},
                ]
                response = api_provider.chat(task=config.TASK_TITLE, messages=messages)
                title = response["message"]["content"].strip()
            else:
                title = self._generate_ollama_title(message)

            return " ".join(title.split()[:3])
        except Exception as exc:
            print(f"Title generation failed: {exc}")
            return f"Chat {datetime.now().strftime('%Y%m%d_%H%M')}"
