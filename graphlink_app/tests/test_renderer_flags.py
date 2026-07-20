"""Tests for resolve_renderer_flag (migration plan section 3.6).

Precedence: explicit GRAPHLINK_<SURFACE>_RENDERER env var, then a settings
override, then the caller's default. An unrecognized value at any tier is
treated as absent (falls through), never raises.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_renderer_flags import resolve_renderer_flag


class TestResolveRendererFlag:
    def test_unset_env_and_no_override_returns_default(self, monkeypatch):
        monkeypatch.delenv("GRAPHLINK_SETTINGS_RENDERER", raising=False)

        assert resolve_renderer_flag("settings", "legacy") == "legacy"

    def test_explicit_env_value_wins(self, monkeypatch):
        monkeypatch.setenv("GRAPHLINK_SETTINGS_RENDERER", "web")

        assert resolve_renderer_flag("settings", "legacy") == "web"

    def test_env_value_is_case_and_whitespace_insensitive(self, monkeypatch):
        monkeypatch.setenv("GRAPHLINK_SETTINGS_RENDERER", "  WEB  ")

        assert resolve_renderer_flag("settings", "legacy") == "web"

    def test_garbage_env_value_falls_through_to_default(self, monkeypatch):
        monkeypatch.setenv("GRAPHLINK_SETTINGS_RENDERER", "nonsense")

        assert resolve_renderer_flag("settings", "legacy") == "legacy"

    def test_garbage_env_value_falls_through_to_settings_override(self, monkeypatch):
        monkeypatch.setenv("GRAPHLINK_SETTINGS_RENDERER", "nonsense")

        assert resolve_renderer_flag("settings", "legacy", settings_override="web") == "web"

    def test_settings_override_used_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("GRAPHLINK_SETTINGS_RENDERER", raising=False)

        assert resolve_renderer_flag("settings", "legacy", settings_override="web") == "web"

    def test_env_wins_over_settings_override(self, monkeypatch):
        monkeypatch.setenv("GRAPHLINK_SETTINGS_RENDERER", "legacy")

        assert resolve_renderer_flag("settings", "legacy", settings_override="web") == "legacy"

    def test_empty_settings_override_falls_through_to_default(self, monkeypatch):
        monkeypatch.delenv("GRAPHLINK_SETTINGS_RENDERER", raising=False)

        assert resolve_renderer_flag("settings", "legacy", settings_override="") == "legacy"

    def test_garbage_settings_override_falls_through_to_default(self, monkeypatch):
        monkeypatch.delenv("GRAPHLINK_SETTINGS_RENDERER", raising=False)

        assert resolve_renderer_flag("settings", "legacy", settings_override="nonsense") == "legacy"

    def test_surface_name_maps_to_uppercase_env_var(self, monkeypatch):
        monkeypatch.setenv("GRAPHLINK_COMPOSER_RENDERER", "web")

        assert resolve_renderer_flag("composer", "legacy") == "web"

    def test_invalid_default_raises(self):
        with pytest.raises(ValueError):
            resolve_renderer_flag("settings", "not-a-real-renderer")
