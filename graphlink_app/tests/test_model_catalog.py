"""Tests for provider-neutral model selection and legacy migration."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_model_catalog import (
    AUTO_MODEL,
    INHERIT_MODEL,
    ModelAssignment,
    ModelDescriptor,
    choose_auto_model,
    resolve_task_model,
)
from graphlink_licensing import SettingsManager
import api_provider


def test_auto_selection_uses_detected_models_and_is_deterministic():
    catalog = [
        ModelDescriptor("zeta:latest", provider="Ollama"),
        ModelDescriptor("alpha:latest", provider="Ollama"),
    ]
    assert choose_auto_model("task_chat", catalog) == "alpha:latest"


def test_explicit_assignment_wins_over_auto_catalog():
    assignments = {"task_chat": ModelAssignment("explicit", "user-model").to_dict()}
    catalog = [ModelDescriptor("detected-model", provider="Ollama")]
    assert resolve_task_model("task_chat", assignments, catalog) == "user-model"


def test_inherit_assignment_resolves_to_chat_model():
    assignments = {
        "task_chat": ModelAssignment("explicit", "chat-model").to_dict(),
        "task_chart": ModelAssignment(INHERIT_MODEL).to_dict(),
    }
    assert resolve_task_model("task_chart", assignments) == "chat-model"


def test_legacy_product_defaults_migrate_to_auto_and_inherit(tmp_path):
    state_file = tmp_path / "session.dat"
    state_file.write_text(
        json.dumps({
            "ollama_chat_model": "qwen3:8b",
            "ollama_chart_model": "deepseek-coder:6.7b",
            "ollama_title_model": "",
            "ollama_web_validate_model": "",
            "ollama_web_summarize_model": "",
        }),
        encoding="utf-8",
    )

    manager = SettingsManager(state_file)

    assert manager.get_ollama_chat_model() == ""
    assert manager.get_ollama_chart_model() == ""
    assignments = manager.get_ollama_model_assignments()
    assert assignments["task_chat"]["mode"] == AUTO_MODEL
    assert assignments["task_chart"]["mode"] == INHERIT_MODEL
    assert json.loads(state_file.read_text(encoding="utf-8"))["schema_version"] == 2


def test_provider_model_profiles_are_isolated(tmp_path):
    manager = SettingsManager(tmp_path / "session.dat")
    manager.set_api_models({"task_chat": "openai-model"}, "OpenAI-Compatible")
    manager.set_api_models({"task_chat": "claude-model"}, "Anthropic Claude")

    assert manager.get_api_models("OpenAI-Compatible")["task_chat"] == "openai-model"
    assert manager.get_api_models("Anthropic Claude")["task_chat"] == "claude-model"


def test_ollama_scan_returns_health_and_normalized_descriptors(monkeypatch):
    monkeypatch.setattr(
        api_provider.ollama,
        "list",
        lambda: {"models": [{"name": "llama3:latest", "size": 1234}]},
    )
    monkeypatch.setattr(api_provider, "_iter_existing_ollama_manifest_roots", lambda: [])

    result = api_provider.scan_local_ollama_models()

    assert result["server_reachable"] is True
    assert result["models"] == ["llama3:latest"]
    assert result["descriptors"][0]["model_id"] == "llama3:latest"
    assert result["descriptors"][0]["size_bytes"] == 1234
