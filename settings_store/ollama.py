"""OllamaSettingsOps - Ollama (local) model assignments and scan cache for
SettingsManager: per-task model assignments (chat/title/chart/web-validate/
web-summarize), reasoning level, and the model-scan cache the Settings UI's
"Scan for models" action populates.

A MIXIN, not a standalone class: every method operates on the composing
class's own state (self.state) and shared constants (self.REASONING_LEVELS,
self.LEGACY_PRODUCT_MODEL_IDS) - it is composed exactly once, by
graphlink_settings_store.py's `class SettingsManager(PersistenceOps, ...,
OllamaSettingsOps, ...)`.

Method bodies are relocated VERBATIM from graphlink_settings_store.py; only
the class wrapper is new.
"""

from __future__ import annotations

from settings_store._composed import SettingsManagerParts

from graphlink_model_catalog import (
    AUTO_MODEL,
    INHERIT_MODEL,
    ModelAssignment,
    assignment_values,
    normalize_model_id,
)


class OllamaSettingsOps(SettingsManagerParts):

    def get_ollama_model_assignments(self):
        assignments = self.state.get("ollama_model_assignments", {})
        if not isinstance(assignments, dict):
            return {}
        return assignment_values(assignments)

    def set_ollama_model_assignments(self, assignments: dict):
        normalized = assignment_values(assignments)
        self.state["ollama_model_assignments"] = normalized
        for task, key in {
            "task_title": "ollama_title_model",
            "task_chat": "ollama_chat_model",
            "task_chart": "ollama_chart_model",
            "task_web_validate": "ollama_web_validate_model",
            "task_web_summarize": "ollama_web_summarize_model",
        }.items():
            assignment = ModelAssignment.from_value(normalized.get(task))
            self.state[key] = assignment.model_id if assignment.mode == "explicit" else ""
        self._save_state()

    def _get_ollama_model(self, task: str) -> str:
        assignment = ModelAssignment.from_value(
            self.get_ollama_model_assignments().get(task, {})
        )
        return assignment.model_id if assignment.mode == "explicit" else ""

    def _set_ollama_model(self, task: str, legacy_key: str, model_name: str):
        model_id = normalize_model_id(model_name)
        assignments = self.get_ollama_model_assignments()
        if model_id and model_id.lower() not in self.LEGACY_PRODUCT_MODEL_IDS:
            assignments[task] = ModelAssignment("explicit", model_id).to_dict()
        else:
            mode = AUTO_MODEL if task == "task_chat" else INHERIT_MODEL
            assignments[task] = ModelAssignment(mode).to_dict()
        self.state[legacy_key] = model_id if model_id.lower() not in self.LEGACY_PRODUCT_MODEL_IDS else ""
        self.state["ollama_model_assignments"] = assignment_values(assignments)
        self._save_state()

    def get_ollama_chat_model(self):
        return self._get_ollama_model("task_chat")

    def set_ollama_chat_model(self, model_name: str):
        self._set_ollama_model("task_chat", "ollama_chat_model", model_name)

    def get_ollama_title_model(self):
        return self._get_ollama_model("task_title")

    def set_ollama_title_model(self, model_name: str):
        self._set_ollama_model("task_title", "ollama_title_model", model_name)

    def get_ollama_chart_model(self):
        return self._get_ollama_model("task_chart")

    def set_ollama_chart_model(self, model_name: str):
        self._set_ollama_model("task_chart", "ollama_chart_model", model_name)

    def get_ollama_web_validate_model(self):
        return self._get_ollama_model("task_web_validate")

    def set_ollama_web_validate_model(self, model_name: str):
        self._set_ollama_model("task_web_validate", "ollama_web_validate_model", model_name)

    def get_ollama_web_summarize_model(self):
        return self._get_ollama_model("task_web_summarize")

    def set_ollama_web_summarize_model(self, model_name: str):
        self._set_ollama_model("task_web_summarize", "ollama_web_summarize_model", model_name)

    def get_ollama_reasoning_level(self):
        return self.state.get("ollama_reasoning_level", "high")

    def set_ollama_reasoning_level(self, level: str):
        if level in self.REASONING_LEVELS:
            self.state['ollama_reasoning_level'] = level
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
