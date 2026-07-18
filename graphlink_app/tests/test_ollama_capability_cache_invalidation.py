"""Tests for the Ollama capability cache and its invalidation.

Regression coverage for the never-invalidated capability cache: _OLLAMA_CAPABILITY_CACHE
never expired or got cleared, so a model pulled/updated mid-session (e.g. gaining audio
support in a newer build) kept whatever capability answer was cached the first time it
was seen that session. invalidate_ollama_capability_cache() now exists, and
ModelPullWorkerThread calls it (for the specific model) right after a successful pull.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import api_provider
from graphlink_agents_tools import ModelPullWorkerThread


class TestInvalidateOllamaCapabilityCache:
    def test_clears_a_specific_model_entry(self, monkeypatch):
        monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {"model-a": {"vision"}, "model-b": {"audio"}})

        api_provider.invalidate_ollama_capability_cache("model-a")

        assert "model-a" not in api_provider._OLLAMA_CAPABILITY_CACHE
        assert "model-b" in api_provider._OLLAMA_CAPABILITY_CACHE

    def test_model_name_lookup_is_case_and_whitespace_insensitive(self, monkeypatch):
        monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {"gemma4:e4b": {"audio"}})

        api_provider.invalidate_ollama_capability_cache("  Gemma4:E4B  ")

        assert api_provider._OLLAMA_CAPABILITY_CACHE == {}

    def test_clearing_a_model_not_in_the_cache_is_a_safe_no_op(self, monkeypatch):
        monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {"model-a": {"vision"}})

        api_provider.invalidate_ollama_capability_cache("never-cached-model")

        assert api_provider._OLLAMA_CAPABILITY_CACHE == {"model-a": {"vision"}}

    def test_no_argument_clears_the_entire_cache(self, monkeypatch):
        monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {"model-a": {"vision"}, "model-b": {"audio"}})

        api_provider.invalidate_ollama_capability_cache()

        assert api_provider._OLLAMA_CAPABILITY_CACHE == {}

    def test_next_lookup_after_invalidation_re_fetches_from_ollama_show(self, monkeypatch):
        monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {"model-a": {"vision"}})

        with patch("api_provider.ollama.show", return_value={"capabilities": ["audio"]}) as mock_show:
            api_provider.invalidate_ollama_capability_cache("model-a")
            result = api_provider._get_ollama_capabilities("model-a")

        mock_show.assert_called_once()
        assert result == {"audio"}


class TestModelPullWorkerThreadInvalidatesCacheOnSuccess:
    def test_successful_pull_invalidates_that_models_cache_entry(self, monkeypatch):
        invalidate_calls = []
        monkeypatch.setattr(
            api_provider, "invalidate_ollama_capability_cache", lambda model_name=None: invalidate_calls.append(model_name)
        )

        with patch("graphlink_agents_tools.ollama.pull", return_value=None):
            worker = ModelPullWorkerThread("gemma4:e4b")
            worker.run()

        assert invalidate_calls == ["gemma4:e4b"]

    def test_failed_pull_does_not_invalidate_the_cache(self, monkeypatch):
        invalidate_calls = []
        monkeypatch.setattr(
            api_provider, "invalidate_ollama_capability_cache", lambda model_name=None: invalidate_calls.append(model_name)
        )

        with patch("graphlink_agents_tools.ollama.pull", side_effect=RuntimeError("connection refused")):
            worker = ModelPullWorkerThread("gemma4:e4b")
            worker.run()

        assert invalidate_calls == []
