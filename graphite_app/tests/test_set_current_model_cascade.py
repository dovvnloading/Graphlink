"""Tests that set_current_model() cascades to Graphlink-Web's Ollama tasks.

graphite_agents_web.py calls api_provider.chat(task=config.TASK_WEB_VALIDATE /
TASK_WEB_SUMMARIZE, ...) directly, and api_provider.chat() resolves the Ollama model
for a task via a plain config.OLLAMA_MODELS.get(task) lookup - no fallback chain like
TitleGenerator's. Before this fix, switching your Ollama chat model in Settings only
ever updated OLLAMA_MODELS[TASK_CHAT], so TASK_WEB_VALIDATE/TASK_WEB_SUMMARIZE stayed
pinned to the hardcoded 'qwen3:8b' default forever - failing for anyone who switched to
a different model and doesn't have qwen3:8b pulled, with no settings UI in Ollama
(Local) mode to work around it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphite_config as config


def test_set_current_model_updates_web_validate_and_summarize(monkeypatch):
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "qwen3:8b")
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_WEB_VALIDATE, "qwen3:8b")
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_WEB_SUMMARIZE, "qwen3:8b")

    config.set_current_model("llama3.1:8b")

    assert config.OLLAMA_MODELS[config.TASK_CHAT] == "llama3.1:8b"
    assert config.OLLAMA_MODELS[config.TASK_WEB_VALIDATE] == "llama3.1:8b"
    assert config.OLLAMA_MODELS[config.TASK_WEB_SUMMARIZE] == "llama3.1:8b"


def test_set_current_model_does_not_touch_chart_model(monkeypatch):
    # TASK_CHART's deepseek-coder default is deliberately code-specialized, not just
    # an unwired copy of the chat model - it should not get overwritten.
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHART, "deepseek-coder:6.7b")

    config.set_current_model("llama3.1:8b")

    assert config.OLLAMA_MODELS[config.TASK_CHART] == "deepseek-coder:6.7b"


def test_set_current_model_ignores_empty_model_name(monkeypatch):
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_WEB_VALIDATE, "existing-model")

    config.set_current_model("")

    assert config.OLLAMA_MODELS[config.TASK_WEB_VALIDATE] == "existing-model"
