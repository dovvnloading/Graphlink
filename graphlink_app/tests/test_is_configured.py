"""Tests for api_provider.is_configured().

Written while investigating doc/ARCHITECTURE_REVIEW_FINDINGS.md #38, which claimed this
function "falls off the end" and returns None for an unrecognized LOCAL_PROVIDER_TYPE.
On re-reading the actual source, that claim was wrong: every branch already returns an
explicit bool, including a catch-all `return False` for an unrecognized local provider.
The finding was a misreading during the original review, not a real bug - corrected in
the doc. This file locks in the (already-correct) behavior with tests, since it had no
coverage at all before (see finding #68's "no tests for api_provider..." note).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api_provider
import graphlink_config as config


def _reset(monkeypatch, **overrides):
    defaults = {
        "USE_API_MODE": False,
        "API_PROVIDER_TYPE": None,
        "API_CLIENT": None,
        "LOCAL_PROVIDER_TYPE": config.LOCAL_PROVIDER_OLLAMA,
    }
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(api_provider, name, value)


class TestIsConfiguredReturnsAnExplicitBoolInEveryBranch:
    def test_unrecognized_local_provider_type_returns_false_not_none(self, monkeypatch):
        _reset(monkeypatch, LOCAL_PROVIDER_TYPE="some-future-provider-not-yet-added")
        result = api_provider.is_configured()
        assert result is False

    def test_ollama_mode_with_no_chat_model_returns_false(self, monkeypatch):
        _reset(monkeypatch, LOCAL_PROVIDER_TYPE=config.LOCAL_PROVIDER_OLLAMA)
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "")
        assert api_provider.is_configured() is False

    def test_ollama_mode_with_a_chat_model_returns_true(self, monkeypatch):
        _reset(monkeypatch, LOCAL_PROVIDER_TYPE=config.LOCAL_PROVIDER_OLLAMA)
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "qwen3:8b")
        assert api_provider.is_configured() is True

    def test_llama_cpp_mode_with_no_model_path_returns_false(self, monkeypatch):
        _reset(monkeypatch, LOCAL_PROVIDER_TYPE=config.LOCAL_PROVIDER_LLAMACPP)
        monkeypatch.setattr(api_provider, "LLAMA_CPP_SETTINGS", {"chat_model_path": "", "title_model_path": ""})
        assert api_provider.is_configured() is False

    def test_api_mode_with_no_client_returns_false(self, monkeypatch):
        _reset(monkeypatch, USE_API_MODE=True, API_PROVIDER_TYPE=config.API_PROVIDER_OPENAI, API_CLIENT=None)
        assert api_provider.is_configured() is False

    def test_api_mode_anthropic_with_missing_required_task_model_returns_false(self, monkeypatch):
        _reset(monkeypatch, USE_API_MODE=True, API_PROVIDER_TYPE=config.API_PROVIDER_ANTHROPIC, API_CLIENT=object())
        monkeypatch.setattr(
            api_provider,
            "API_MODELS",
            {
                config.TASK_TITLE: "claude-x",
                config.TASK_CHAT: "claude-x",
                config.TASK_CHART: "",  # missing
                config.TASK_WEB_VALIDATE: "claude-x",
                config.TASK_WEB_SUMMARIZE: "claude-x",
                config.TASK_IMAGE_GEN: "",  # not required for Anthropic
            },
        )
        assert api_provider.is_configured() is False
