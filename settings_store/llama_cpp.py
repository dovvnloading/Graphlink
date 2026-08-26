"""LlamaCppSettingsOps - Llama.cpp (local GGUF) model paths and runtime
settings for SettingsManager: chat/title model paths, reasoning level, chat
format, context/GPU-layer/thread runtime knobs, and the model-scan cache.

A MIXIN, not a standalone class: every method operates on the composing
class's own state (self.state) and shared constants (self.REASONING_LEVELS)
- it is composed exactly once, by graphlink_settings_store.py's `class
SettingsManager(PersistenceOps, ..., LlamaCppSettingsOps, ...)`.

_is_llama_cpp_gguf_path is a module-level helper, not a method, since it's
only ever called from within this module's own getters/setters.

Method bodies are relocated VERBATIM from graphlink_settings_store.py; only
the class wrapper is new.
"""

from __future__ import annotations


def _is_llama_cpp_gguf_path(path_value) -> bool:
    normalized = str(path_value or "").strip()
    return bool(normalized) and normalized.lower().endswith(".gguf")


class LlamaCppSettingsOps:

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

    def get_llama_cpp_reasoning_level(self):
        return self.state.get("llama_cpp_reasoning_level", "high")

    def set_llama_cpp_reasoning_level(self, level: str):
        if level in self.REASONING_LEVELS:
            self.state['llama_cpp_reasoning_level'] = level
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
            "reasoning_level": self.get_llama_cpp_reasoning_level(),
            "chat_format": self.get_llama_cpp_chat_format(),
            "n_ctx": self.get_llama_cpp_n_ctx(),
            "n_gpu_layers": self.get_llama_cpp_n_gpu_layers(),
            "n_threads": self.get_llama_cpp_n_threads(),
        }
