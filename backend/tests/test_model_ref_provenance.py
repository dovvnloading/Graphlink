"""ADR-018: a pinned dispatch's provenance names the pinned model.

describe_active_model is the (provider, model) pair stamped on reply nodes
(the chat-node model badge) and handed to the token counter for cost
estimation. Unpinned, it reads the task's configured model. A pinned
dispatch runs exactly the ModelRef's model instead (_provider_for_model_ref),
so given that ref it must report the ref - in the SAME label convention the
unpinned branches use, so a pin to the model the session would have picked
anyway produces byte-identical provenance and the same cost lookup.
"""

from __future__ import annotations

import pytest

import api_provider
import graphlink_task_config as config
from graphlink_model_catalog import ModelRef


def _configure_ollama(monkeypatch, chat_model: str) -> None:
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, chat_model)


def _configure_llama_cpp(monkeypatch, chat_path: str) -> None:
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_LLAMACPP)
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "chat_model_path", chat_path)
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "title_model_path", "")


def _configure_cloud(provider: str):
    def configure(monkeypatch, chat_model: str) -> None:
        monkeypatch.setattr(api_provider, "USE_API_MODE", True)
        monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", provider)
        monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, chat_model)

    return configure


# (configure the session, the model it is configured with, a ref naming that
# same model, a ref naming a DIFFERENT model of the same provider)
_CASES = [
    pytest.param(
        _configure_ollama, "llama3:8b",
        ModelRef(config.LOCAL_PROVIDER_OLLAMA, "llama3:8b"), ModelRef(config.LOCAL_PROVIDER_OLLAMA, "qwen3:14b"),
        id="ollama",
    ),
    pytest.param(
        _configure_llama_cpp, "/models/qwen3-8b.gguf",
        ModelRef(config.LOCAL_PROVIDER_LLAMACPP, "qwen3-8b.gguf"),
        ModelRef(config.LOCAL_PROVIDER_LLAMACPP, "tinyllama.gguf"),
        id="llama.cpp",
    ),
    *[
        pytest.param(
            _configure_cloud(provider), "model-a", ModelRef(provider, "model-a"), ModelRef(provider, "model-b"),
            id=provider,
        )
        for provider in (config.API_PROVIDER_OPENAI, config.API_PROVIDER_ANTHROPIC, config.API_PROVIDER_GEMINI)
    ],
]


@pytest.mark.parametrize("configure, configured_model, same_ref, other_ref", _CASES)
def test_a_pin_to_the_configured_model_reports_exactly_the_unpinned_provenance(
    monkeypatch, configure, configured_model, same_ref, other_ref,
):
    configure(monkeypatch, configured_model)
    unpinned = api_provider.describe_active_model(config.TASK_CHAT)

    assert api_provider.describe_active_model(config.TASK_CHAT, model_ref=same_ref) == unpinned


@pytest.mark.parametrize("configure, configured_model, same_ref, other_ref", _CASES)
def test_a_pin_to_another_model_reports_the_pinned_model_not_the_tasks(
    monkeypatch, configure, configured_model, same_ref, other_ref,
):
    configure(monkeypatch, configured_model)
    unpinned_provider, _unpinned_model = api_provider.describe_active_model(config.TASK_CHAT)

    assert api_provider.describe_active_model(config.TASK_CHAT, model_ref=other_ref) == (
        unpinned_provider, other_ref.model_id,
    )


def test_no_ref_keeps_the_task_keyed_lookup(monkeypatch):
    _configure_ollama(monkeypatch, "llama3:8b")
    assert api_provider.describe_active_model(config.TASK_CHAT, model_ref=None) == ("ollama", "llama3:8b")
