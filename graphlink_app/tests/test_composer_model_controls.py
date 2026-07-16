"""Regression tests for the in-composer model and reasoning controls."""

import json
from pathlib import Path
from unittest.mock import patch

import api_provider
import graphlink_config as config
from graphlink_composer import ComposerController
from graphlink_composer_bridge import ComposerBridge


class _Settings:
    def __init__(self, mode="Ollama (Local)"):
        self.mode = mode
        self.chat_model = ""
        self.reasoning_mode = "Thinking"
        self.api_models = {config.TASK_CHAT: "cloud-active"}
        self.api_catalog = [
            {"model_id": "cloud-active", "provider": "OpenAI-Compatible"},
            {"model_id": "cloud-fast", "provider": "OpenAI-Compatible"},
        ]
        self.scanned_models = ["llama3.2:latest", "qwen3:8b"]
        self.assignments = {
            config.TASK_CHAT: {"mode": "auto", "model_id": ""},
            config.TASK_TITLE: {"mode": "inherit", "model_id": ""},
            config.TASK_CHART: {"mode": "inherit", "model_id": ""},
            config.TASK_WEB_VALIDATE: {"mode": "inherit", "model_id": ""},
            config.TASK_WEB_SUMMARIZE: {"mode": "inherit", "model_id": ""},
        }
        self.changed = 0

    def get_current_mode(self):
        return self.mode

    def get_api_provider(self):
        return "OpenAI-Compatible"

    def get_api_models(self, provider=None):
        return dict(self.api_models)

    def get_api_model_catalog(self, provider=None):
        return list(self.api_catalog)

    def set_api_models(self, models, provider=None):
        self.api_models = dict(models)

    def get_ollama_chat_model(self):
        return self.chat_model

    def set_ollama_chat_model(self, model):
        self.chat_model = str(model)
        self.assignments[config.TASK_CHAT] = {
            "mode": "explicit" if self.chat_model else "auto",
            "model_id": self.chat_model,
        }

    def get_ollama_reasoning_mode(self):
        return self.reasoning_mode

    def set_ollama_reasoning_mode(self, mode):
        self.reasoning_mode = str(mode)

    def get_ollama_scanned_models(self):
        return list(self.scanned_models)

    def get_ollama_model_assignments(self):
        return dict(self.assignments)

    def get_ollama_title_model(self):
        return ""

    def get_ollama_chart_model(self):
        return ""

    def get_ollama_web_validate_model(self):
        return ""

    def get_ollama_web_summarize_model(self):
        return ""


class _Window:
    def __init__(self, settings):
        self.settings_manager = settings
        self.current_node = None
        self.pending_attachments = []
        self.settings_changed = 0

    def on_settings_changed(self):
        self.settings_changed += 1


def test_local_route_exposes_installed_models_and_reasoning_state(monkeypatch):
    settings = _Settings()
    window = _Window(settings)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "llama3.2:latest")
    bridge = ComposerBridge(window, ComposerController())
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.ready()
    route = json.loads(states[-1])["route"]

    assert [item["id"] for item in route["modelOptions"]] == ["llama3.2:latest", "qwen3:8b"]
    assert route["modelLabel"] == "llama3.2:latest"
    assert route["reasoning"]["level"] == "Thinking"
    assert route["canChange"] is True


def test_select_model_and_reasoning_level_update_runtime_state(monkeypatch):
    settings = _Settings()
    window = _Window(settings)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "llama3.2:latest")
    bridge = ComposerBridge(window, ComposerController())

    bridge.selectModel("qwen3:8b")
    bridge.setReasoningLevel("Quick")

    assert settings.chat_model == "qwen3:8b"
    assert settings.reasoning_mode == "Quick"
    assert config.OLLAMA_MODELS[config.TASK_CHAT] == "qwen3:8b"
    assert window.settings_changed == 2


def test_cloud_route_uses_persisted_catalog_and_selection_updates_chat_task():
    settings = _Settings(mode=config.MODE_API_ENDPOINT)
    window = _Window(settings)
    bridge = ComposerBridge(window, ComposerController())
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.ready()
    route = json.loads(states[-1])["route"]
    assert [item["id"] for item in route["modelOptions"]] == ["cloud-active", "cloud-fast"]
    assert route["reasoning"]["label"] == "Provider managed"
    assert json.loads(states[-1])["capabilities"]["reasoningSelection"] is False

    bridge.selectModel("cloud-fast")

    assert settings.api_models[config.TASK_CHAT] == "cloud-fast"


def test_composer_footer_has_stable_model_width_and_no_status_dot():
    source_root = Path(__file__).resolve().parents[2] / "composer_ui" / "src"
    composer_source = (source_root / "ComposerApp.tsx").read_text(encoding="utf-8")
    styles_source = (source_root / "styles.css").read_text(encoding="utf-8")

    assert "status-dot" not in composer_source
    assert ".status-dot" not in styles_source
    assert "width: 190px;" in styles_source
    assert "flex: 0 0 190px;" in styles_source
    assert "text-overflow: ellipsis;" in styles_source


def test_quick_reasoning_disables_ollama_thinking_for_reasoning_models(monkeypatch):
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setattr(api_provider, "OLLAMA_REASONING_MODE", "Quick")
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "qwen3:8b")

    with patch(
        "api_provider.ollama.chat",
        return_value={"message": {"content": "answer", "role": "assistant"}},
    ) as ollama_chat:
        api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}])

    assert ollama_chat.call_args.kwargs["think"] is False
