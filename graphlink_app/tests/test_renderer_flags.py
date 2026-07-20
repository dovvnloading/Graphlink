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


class TestSettingsRendererDefaultFlip:
    """Phase 3 increment 9: the settings island became the DEFAULT renderer.
    These guard the flip durably so an accidental refactor can't silently
    revert it during the one-release observation window before increment 10
    deletes the legacy dialog and this flag entirely."""

    def test_settings_default_constant_is_web(self):
        from graphlink_settings_web import SETTINGS_RENDERER_DEFAULT

        assert SETTINGS_RENDERER_DEFAULT == "web"

    def test_settings_default_resolves_to_web_with_no_env_and_no_override(self, monkeypatch):
        # The exact call ChatWindow.show_settings() makes when the user has
        # set neither the env var nor the mirrored settings key: the new
        # default now yields the web island.
        from graphlink_settings_web import SETTINGS_RENDERER_DEFAULT

        monkeypatch.delenv("GRAPHLINK_SETTINGS_RENDERER", raising=False)

        assert resolve_renderer_flag("settings", SETTINGS_RENDERER_DEFAULT, settings_override="") == "web"

    def test_legacy_escape_hatch_still_wins_over_the_web_default(self, monkeypatch):
        # The escape hatch the observation window depends on: a user who
        # opts back into legacy via either tier still gets it.
        from graphlink_settings_web import SETTINGS_RENDERER_DEFAULT

        monkeypatch.setenv("GRAPHLINK_SETTINGS_RENDERER", "legacy")
        assert resolve_renderer_flag("settings", SETTINGS_RENDERER_DEFAULT) == "legacy"

        monkeypatch.delenv("GRAPHLINK_SETTINGS_RENDERER", raising=False)
        assert resolve_renderer_flag("settings", SETTINGS_RENDERER_DEFAULT, settings_override="legacy") == "legacy"
