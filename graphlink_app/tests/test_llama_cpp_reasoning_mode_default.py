"""Tests for the llama.cpp reasoning_mode default consistency.

Regression coverage for the reasoning_mode default drift: api_provider.py had two
internal "Quick" placeholder defaults for reasoning_mode (the module-level
LLAMA_CPP_SETTINGS dict, and _normalize_llama_cpp_settings()'s fallback for a missing
key), while SettingsManager - the actual source of truth once a real settings dict flows
through initialize_local_provider() - has always defaulted to "Thinking" everywhere
(_load_state's migration backfill, _create_initial_state, and
get_llama_cpp_reasoning_mode()'s own fallback). Traced that this mismatch has no
observable effect in the current codebase (the "Quick" defaults were always overwritten
before a real llama.cpp request could read them), but it's still a real internal
inconsistency worth aligning - a genuine, if very low-impact, fix.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api_provider
import graphlink_licensing


class TestModuleLevelPlaceholderMatchesSettingsManagerDefault:
    def test_llama_cpp_settings_module_default_is_thinking(self):
        assert api_provider.LLAMA_CPP_SETTINGS["reasoning_mode"] == "Thinking"

    def test_settings_manager_default_is_also_thinking(self, tmp_path):
        manager = graphlink_licensing.SettingsManager(tmp_path / "session.dat")
        assert manager.get_llama_cpp_reasoning_mode() == "Thinking"


class TestNormalizeLlamaCppSettingsMissingKeyFallback:
    def test_a_settings_dict_missing_reasoning_mode_normalizes_to_thinking(self):
        normalized = api_provider._normalize_llama_cpp_settings({"chat_model_path": "model.gguf"})
        assert normalized["reasoning_mode"] == "Thinking"

    def test_no_settings_at_all_normalizes_to_thinking(self):
        normalized = api_provider._normalize_llama_cpp_settings(None)
        assert normalized["reasoning_mode"] == "Thinking"

    def test_an_explicit_quick_value_is_still_honored(self):
        normalized = api_provider._normalize_llama_cpp_settings({"reasoning_mode": "Quick"})
        assert normalized["reasoning_mode"] == "Quick"

    def test_an_explicit_thinking_value_is_still_honored(self):
        normalized = api_provider._normalize_llama_cpp_settings({"reasoning_mode": "Thinking"})
        assert normalized["reasoning_mode"] == "Thinking"
