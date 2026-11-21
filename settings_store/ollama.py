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

from backend.ollama_model_names import valid_ollama_model_name

from graphlink_model_catalog import (
    AUTO_MODEL,
    INHERIT_MODEL,
    ModelAssignment,
    assignment_values,
    normalize_model_id,
)


class OllamaSettingsOps(SettingsManagerParts):

    @staticmethod
    def _safe_assignments(values: dict) -> dict:
        """Scrub stale/legacy explicit IDs before they reach runtime selection."""
        normalized = assignment_values(values)
        for task, entry in normalized.items():
            if entry.get("mode") == "explicit" and not valid_ollama_model_name(entry.get("model_id")):
                normalized[task] = {
                    "mode": AUTO_MODEL if task == "task_chat" else INHERIT_MODEL,
                    "model_id": "",
                }
        return normalized

    def get_ollama_model_assignments(self):
        assignments = self.state.get("ollama_model_assignments", {})
        if not isinstance(assignments, dict):
            return {}
        return self._safe_assignments(assignments)

    def set_ollama_model_assignments(self, assignments: dict):
        normalized = self._safe_assignments(assignments)
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
        if not valid_ollama_model_name(model_id):
            model_id = ""
        assignments = self.get_ollama_model_assignments()
        if model_id and model_id.lower() not in self.LEGACY_PRODUCT_MODEL_IDS:
            assignments[task] = ModelAssignment("explicit", model_id).to_dict()
        else:
            mode = AUTO_MODEL if task == "task_chat" else INHERIT_MODEL
            assignments[task] = ModelAssignment(mode).to_dict()
        self.state[legacy_key] = model_id if model_id.lower() not in self.LEGACY_PRODUCT_MODEL_IDS else ""
        self.state["ollama_model_assignments"] = assignment_values(assignments)
        self._save_state()

    def get_ollama_context_windows(self) -> dict[str, int]:
        """Saved per-model num_ctx overrides, indexed by case-insensitive model ID."""
        from backend.ollama_context import CONTEXT_CHOICES

        raw = self.state.get("ollama_context_windows", {})
        if not isinstance(raw, dict):
            return {}
        return {
            str(model).strip().casefold(): value
            for model, value in raw.items()
            if isinstance(model, str) and valid_ollama_model_name(model.strip())
            and type(value) is int and value in CONTEXT_CHOICES
        }

    def set_ollama_context_window(self, model_name: str, num_ctx: int) -> None:
        """Save one model's served window; reject malformed or unbounded writes."""
        from backend.ollama_context import CONTEXT_CHOICES

        model = model_name.strip().casefold() if isinstance(model_name, str) else ""
        if not valid_ollama_model_name(model) or type(num_ctx) is not int or num_ctx not in CONTEXT_CHOICES:
            raise ValueError("Invalid Ollama model or context window.")
        windows = self.get_ollama_context_windows()
        windows[model] = num_ctx
        self.state["ollama_context_windows"] = windows
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
        return [model.strip() for model in models
                if isinstance(model, str) and valid_ollama_model_name(model.strip())]

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
            {model.strip() for model in (models or [])
             if isinstance(model, str) and valid_ollama_model_name(model.strip())},
            key=str.lower,
        )
        self.state["ollama_model_scan_mode"] = str(scan_mode or "").strip()
        self.state["ollama_model_scan_path"] = str(scan_path or "").strip()
        self.state["ollama_model_scan_locations"] = sorted(
            {str(location).strip() for location in (locations or []) if str(location).strip()},
            key=str.lower,
        )
        self._save_state()
