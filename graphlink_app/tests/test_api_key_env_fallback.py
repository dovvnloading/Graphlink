"""Tests for environment-variable API key fallback in api_provider.initialize_api().

Regression coverage for the OpenAI env-var gap: Anthropic and Gemini both fall back to a
GRAPHLINK_<PROVIDER>_API_KEY / vendor-standard env var when no key is passed in, but the
OpenAI-compatible branch didn't - a user with OPENAI_API_KEY set in their environment (a
very standard thing to have) but nothing saved in Graphlink's own Settings would get
"API key not configured" instead of it just working, unlike every other provider.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api_provider
import graphlink_config as config


def _reset_api_provider_state(monkeypatch):
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", None)
    monkeypatch.setattr(api_provider, "API_CLIENT", None)
    monkeypatch.setattr(api_provider, "API_KEY", None)
    monkeypatch.setattr(api_provider, "API_BASE_URL", None)


class TestOpenAiApiKeyEnvFallback:
    def test_falls_back_to_graphlink_prefixed_env_var(self, monkeypatch):
        _reset_api_provider_state(monkeypatch)
        monkeypatch.setenv("GRAPHLINK_OPENAI_API_KEY", "from-graphlink-env")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        fake_openai_cls = MagicMock()

        with patch("openai.OpenAI", fake_openai_cls):
            api_provider.initialize_api(config.API_PROVIDER_OPENAI, "", "https://api.example.com/v1")

        fake_openai_cls.assert_called_once_with(api_key="from-graphlink-env", base_url="https://api.example.com/v1")

    def test_falls_back_to_vendor_standard_env_var(self, monkeypatch):
        _reset_api_provider_state(monkeypatch)
        monkeypatch.delenv("GRAPHLINK_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "from-vendor-env")
        fake_openai_cls = MagicMock()

        with patch("openai.OpenAI", fake_openai_cls):
            api_provider.initialize_api(config.API_PROVIDER_OPENAI, "", "https://api.example.com/v1")

        fake_openai_cls.assert_called_once_with(api_key="from-vendor-env", base_url="https://api.example.com/v1")

    def test_explicitly_passed_key_wins_over_env_vars(self, monkeypatch):
        _reset_api_provider_state(monkeypatch)
        monkeypatch.setenv("GRAPHLINK_OPENAI_API_KEY", "from-env")
        monkeypatch.setenv("OPENAI_API_KEY", "from-env-2")
        fake_openai_cls = MagicMock()

        with patch("openai.OpenAI", fake_openai_cls):
            api_provider.initialize_api(config.API_PROVIDER_OPENAI, "from-settings", "https://api.example.com/v1")

        fake_openai_cls.assert_called_once_with(api_key="from-settings", base_url="https://api.example.com/v1")

    def test_still_raises_when_no_key_anywhere_and_base_url_is_remote(self, monkeypatch):
        import pytest

        _reset_api_provider_state(monkeypatch)
        monkeypatch.delenv("GRAPHLINK_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with pytest.raises(RuntimeError, match="API key not configured"):
            api_provider.initialize_api(config.API_PROVIDER_OPENAI, "", "https://api.example.com/v1")

    def test_local_base_url_still_uses_dummy_key_when_nothing_configured(self, monkeypatch):
        _reset_api_provider_state(monkeypatch)
        monkeypatch.delenv("GRAPHLINK_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        fake_openai_cls = MagicMock()

        with patch("openai.OpenAI", fake_openai_cls):
            api_provider.initialize_api(config.API_PROVIDER_OPENAI, "", "http://localhost:11434/v1")

        fake_openai_cls.assert_called_once_with(api_key="dummy-key-for-local", base_url="http://localhost:11434/v1")


class TestLegacyGraphiteEnvVarStillWorks:
    """The app was renamed from Graphite to Graphlink; GRAPHITE_*_API_KEY env vars set
    before the rename must keep working so existing power-user shell configs don't
    silently break. GRAPHLINK_* always takes priority when both are set."""

    def test_openai_falls_back_to_legacy_graphite_prefixed_env_var(self, monkeypatch):
        _reset_api_provider_state(monkeypatch)
        monkeypatch.delenv("GRAPHLINK_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("GRAPHITE_OPENAI_API_KEY", "from-legacy-graphite-env")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        fake_openai_cls = MagicMock()

        with patch("openai.OpenAI", fake_openai_cls):
            api_provider.initialize_api(config.API_PROVIDER_OPENAI, "", "https://api.example.com/v1")

        fake_openai_cls.assert_called_once_with(api_key="from-legacy-graphite-env", base_url="https://api.example.com/v1")

    def test_new_prefixed_env_var_wins_over_legacy_one(self, monkeypatch):
        _reset_api_provider_state(monkeypatch)
        monkeypatch.setenv("GRAPHLINK_OPENAI_API_KEY", "from-new-env")
        monkeypatch.setenv("GRAPHITE_OPENAI_API_KEY", "from-legacy-env")
        fake_openai_cls = MagicMock()

        with patch("openai.OpenAI", fake_openai_cls):
            api_provider.initialize_api(config.API_PROVIDER_OPENAI, "", "https://api.example.com/v1")

        fake_openai_cls.assert_called_once_with(api_key="from-new-env", base_url="https://api.example.com/v1")

    def test_anthropic_falls_back_to_legacy_graphite_prefixed_env_var(self, monkeypatch):
        monkeypatch.delenv("GRAPHLINK_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("GRAPHITE_ANTHROPIC_API_KEY", "legacy-ant-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        assert api_provider._get_anthropic_api_key() == "legacy-ant-key"

    def test_gemini_falls_back_to_legacy_graphite_prefixed_env_var(self, monkeypatch):
        monkeypatch.delenv("GRAPHLINK_GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GRAPHITE_GEMINI_API_KEY", "legacy-gem-key")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        assert api_provider._get_gemini_api_key() == "legacy-gem-key"
