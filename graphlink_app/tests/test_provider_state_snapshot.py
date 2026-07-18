"""Tests for the per-request provider-state snapshot in api_provider.

Regression coverage for the mid-request provider swap - the half of that race a UI-level
mode-switch guard can't close, since background plugin requests aren't gated by the UI:
the provider runtime lives in module-level globals mutated by initialize_*() on the UI
thread while chat()/generate_image() read them from worker threads. Both functions now
capture one consistent _ProviderSnapshot at entry (under _PROVIDER_STATE_LOCK, which all
mutators also take) and route the ENTIRE request through it - branch selection, client,
key, task models, llama.cpp settings, and, critically, the error-classification handler
at the bottom of chat().

The decisive tests simulate the race directly: a stubbed network call flips the global
provider state mid-request (exactly what a settings save on the UI thread does) and the
assertions prove the in-flight request keeps behaving as the provider it started as.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import api_provider
import graphlink_config as config


def _set_gemini_api_mode(monkeypatch, chat_model="gemini-2.5-flash"):
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_GEMINI)
    monkeypatch.setattr(api_provider, "API_CLIENT", {"provider": config.API_PROVIDER_GEMINI})
    monkeypatch.setattr(api_provider, "API_KEY", "gemini-key")
    monkeypatch.setattr(api_provider, "API_MODELS", {**api_provider.API_MODELS, config.TASK_CHAT: chat_model})


def _flip_to_openai_mode():
    # Simulates the UI thread re-running initialize_api mid-request. Direct global
    # writes (not initialize_api) so no openai import/client construction is needed.
    api_provider.USE_API_MODE = True
    api_provider.API_PROVIDER_TYPE = config.API_PROVIDER_OPENAI
    api_provider.API_CLIENT = object()
    api_provider.API_KEY = "openai-key"


class TestErrorClassificationUsesTheSnapshot:
    def test_quota_error_is_reported_for_the_provider_the_request_started_as(self, monkeypatch):
        # THE regression case: the request starts as Gemini; mid-request the provider
        # flips to OpenAI; the network call then fails with a quota error. The old
        # code re-read the *global* API_PROVIDER_TYPE in the exception handler and
        # emitted the OpenAI quota message for a Gemini request.
        _set_gemini_api_mode(monkeypatch)

        def _flip_then_quota(*args, **kwargs):
            _flip_to_openai_mode()
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")

        with patch("api_provider._gemini_post_json", side_effect=_flip_then_quota):
            with pytest.raises(RuntimeError) as excinfo:
                api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

        assert "Gemini" in str(excinfo.value)
        assert "OpenAI" not in str(excinfo.value)


class TestRoutingUsesTheSnapshot:
    def test_ollama_request_completes_on_the_ollama_path_despite_a_mid_request_flip(self, monkeypatch):
        monkeypatch.setattr(api_provider, "USE_API_MODE", False)
        monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "test-model")

        def _flip_then_answer(*args, **kwargs):
            _flip_to_openai_mode()
            return {"message": {"content": "local answer", "role": "assistant"}}

        with patch("api_provider.ollama.chat", side_effect=_flip_then_answer):
            result = api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

        assert result["message"]["content"] == "local answer"

    def test_gemini_request_carries_its_snapshot_key_not_the_swapped_global(self, monkeypatch):
        _set_gemini_api_mode(monkeypatch)
        seen_keys = []

        def _capture_key(endpoint, body, timeout=120, cancel_event=None, api_key=None):
            # By the time the HTTP helper resolves the key, the global has already
            # been swapped to the OpenAI key - the explicit api_key parameter from
            # the snapshot must win.
            _flip_to_openai_mode()
            seen_keys.append(api_provider._get_gemini_api_key(api_key))
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

        with patch("api_provider._gemini_post_json", side_effect=_capture_key):
            api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

        assert seen_keys == ["gemini-key"]


class TestSnapshotMechanics:
    def test_snapshot_copies_are_isolated_from_later_global_mutation(self, monkeypatch):
        monkeypatch.setattr(api_provider, "LLAMA_CPP_SETTINGS", {"chat_model_path": "a.gguf", "title_model_path": ""})
        monkeypatch.setattr(api_provider, "API_MODELS", {config.TASK_CHAT: "model-one"})

        snapshot = api_provider._snapshot_provider_state()
        api_provider.LLAMA_CPP_SETTINGS["chat_model_path"] = "b.gguf"
        api_provider.API_MODELS[config.TASK_CHAT] = "model-two"

        assert snapshot.llama_cpp_settings["chat_model_path"] == "a.gguf"
        assert snapshot.api_models[config.TASK_CHAT] == "model-one"

    def test_llama_helpers_honor_an_explicit_settings_dict_over_the_global(self, monkeypatch):
        monkeypatch.setattr(api_provider, "LLAMA_CPP_SETTINGS", {"chat_model_path": "global.gguf", "title_model_path": ""})
        explicit = {"chat_model_path": "snapshot.gguf", "title_model_path": ""}

        assert api_provider._get_llama_cpp_model_path(config.TASK_CHAT, explicit) == "snapshot.gguf"
        assert api_provider._get_llama_cpp_model_path(config.TASK_CHAT) == "global.gguf"

    def test_mutators_and_snapshot_share_the_state_lock(self):
        # Behavioral lock check: holding the lock must block a snapshot until release.
        import threading

        acquired = api_provider._PROVIDER_STATE_LOCK.acquire()
        assert acquired
        try:
            result = {}

            def _try_snapshot():
                result["snapshot"] = api_provider._snapshot_provider_state()

            worker = threading.Thread(target=_try_snapshot)
            worker.start()
            worker.join(timeout=0.2)
            assert "snapshot" not in result  # blocked while the lock is held
        finally:
            api_provider._PROVIDER_STATE_LOCK.release()
        worker.join(timeout=2)
        assert "snapshot" in result  # completes once released
