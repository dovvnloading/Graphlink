"""Tests for per-task Ollama model settings (Chart, Web Validation, Web Summarization).

Earlier, config.OLLAMA_MODELS hardcoded a literal default for every Ollama task, and
set_current_model() only ever updated TASK_CHAT. TASK_WEB_VALIDATE/TASK_WEB_SUMMARIZE
had no settings UI in Ollama (Local) mode at all, so they stayed pinned to the
hardcoded 'qwen3:8b' default forever - failing for anyone who switched chat models and
didn't have qwen3:8b pulled.

The fix is not to silently cascade the chat model into those tasks (that's still not
user control, just a different hardcoded assumption) - it's to give Chart Generation,
Web Content Validation, and Web Content Summarization their own independent,
persisted settings fields (see OllamaSettingsWidget), each resolved through
SettingsManager with its own sensible fallback, and to read those into
config.OLLAMA_MODELS via sync_ollama_task_models() rather than copying TASK_CHAT.

These tests cover:
1. set_current_model() only touches TASK_CHAT - it must not silently overwrite the
   other tasks' explicit settings.
2. sync_ollama_task_models() correctly pulls each task's model from the settings
   manager.
3. SettingsManager's new get/set methods round-trip correctly and fall back
   sensibly when nothing has been explicitly set.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_config as config
from graphlink_licensing import SettingsManager


def _make_settings_manager(tmp_path):
    # Bypasses SettingsManager.__init__ (which points state_file at the real
    # ~/.graphlink/session.dat) so these tests never touch real user data.
    manager = SettingsManager.__new__(SettingsManager)
    manager.state_file = tmp_path / "session.dat"
    manager.state = {}
    return manager


class TestSetCurrentModelScope:
    def test_set_current_model_only_touches_task_chat(self, monkeypatch):
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "qwen3:8b")
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHART, "deepseek-coder:6.7b")
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_WEB_VALIDATE, "qwen3:8b")
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_WEB_SUMMARIZE, "qwen3:8b")

        config.set_current_model("llama3.1:8b")

        assert config.OLLAMA_MODELS[config.TASK_CHAT] == "llama3.1:8b"
        assert config.OLLAMA_MODELS[config.TASK_CHART] == "deepseek-coder:6.7b"
        assert config.OLLAMA_MODELS[config.TASK_WEB_VALIDATE] == "qwen3:8b"
        assert config.OLLAMA_MODELS[config.TASK_WEB_SUMMARIZE] == "qwen3:8b"

    def test_set_current_model_ignores_empty_model_name(self, monkeypatch):
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "existing-model")

        config.set_current_model("")

        assert config.OLLAMA_MODELS[config.TASK_CHAT] == "existing-model"


class TestSyncOllamaTaskModels:
    def test_reads_each_task_from_the_settings_manager(self, monkeypatch, tmp_path):
        manager = _make_settings_manager(tmp_path)
        manager.set_ollama_chart_model("qwen2.5-coder:7b")
        manager.set_ollama_web_validate_model("phi4:14b")
        manager.set_ollama_web_summarize_model("mistral:7b")

        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHART, "stale")
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_WEB_VALIDATE, "stale")
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_WEB_SUMMARIZE, "stale")

        config.sync_ollama_task_models(manager)

        assert config.OLLAMA_MODELS[config.TASK_CHART] == "qwen2.5-coder:7b"
        assert config.OLLAMA_MODELS[config.TASK_WEB_VALIDATE] == "phi4:14b"
        assert config.OLLAMA_MODELS[config.TASK_WEB_SUMMARIZE] == "mistral:7b"

    def test_falls_back_when_nothing_explicitly_set(self, monkeypatch, tmp_path):
        manager = _make_settings_manager(tmp_path)
        manager.set_ollama_chat_model("llama3.1:8b")

        config.sync_ollama_task_models(manager)

        assert config.OLLAMA_MODELS[config.TASK_CHART] == "deepseek-coder:6.7b"
        assert config.OLLAMA_MODELS[config.TASK_WEB_VALIDATE] == "llama3.1:8b"
        assert config.OLLAMA_MODELS[config.TASK_WEB_SUMMARIZE] == "llama3.1:8b"


class TestSettingsManagerOllamaTaskModelMethods:
    def test_chart_model_round_trips_and_defaults_to_code_specialized_model(self, tmp_path):
        manager = _make_settings_manager(tmp_path)
        assert manager.get_ollama_chart_model() == "deepseek-coder:6.7b"

        manager.set_ollama_chart_model("qwen2.5-coder:7b")
        assert manager.get_ollama_chart_model() == "qwen2.5-coder:7b"

    def test_web_validate_model_round_trips_and_falls_back_to_chat_model(self, tmp_path):
        manager = _make_settings_manager(tmp_path)
        manager.set_ollama_chat_model("llama3.1:8b")
        assert manager.get_ollama_web_validate_model() == "llama3.1:8b"

        manager.set_ollama_web_validate_model("phi4:14b")
        assert manager.get_ollama_web_validate_model() == "phi4:14b"

    def test_web_summarize_model_round_trips_and_falls_back_to_chat_model(self, tmp_path):
        manager = _make_settings_manager(tmp_path)
        manager.set_ollama_chat_model("llama3.1:8b")
        assert manager.get_ollama_web_summarize_model() == "llama3.1:8b"

        manager.set_ollama_web_summarize_model("mistral:7b")
        assert manager.get_ollama_web_summarize_model() == "mistral:7b"

    def test_settings_persist_across_a_reload_of_the_same_state_file(self, tmp_path):
        manager = _make_settings_manager(tmp_path)
        manager.set_ollama_chart_model("qwen2.5-coder:7b")
        manager.set_ollama_web_validate_model("phi4:14b")
        manager.set_ollama_web_summarize_model("mistral:7b")

        reloaded = SettingsManager.__new__(SettingsManager)
        reloaded.state_file = manager.state_file
        reloaded.state = reloaded._load_state()

        assert reloaded.get_ollama_chart_model() == "qwen2.5-coder:7b"
        assert reloaded.get_ollama_web_validate_model() == "phi4:14b"
        assert reloaded.get_ollama_web_summarize_model() == "mistral:7b"
