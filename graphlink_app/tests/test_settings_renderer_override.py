"""Tests for SettingsManager's settings_renderer_override field (plan section 3.6).

The support-facing mirror of GRAPHLINK_SETTINGS_RENDERER - lets a user flip
the settings island's renderer without shell access. resolve_renderer_flag()
(tests/test_renderer_flags.py) covers the precedence logic; these tests only
cover SettingsManager's own storage/validation of the value.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_licensing import SettingsManager


class TestSettingsRendererOverride:
    def test_default_is_empty_string(self, tmp_path):
        manager = SettingsManager(tmp_path / "session.dat")

        assert manager.get_settings_renderer_override() == ""

    def test_round_trips_and_persists_across_reload(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)

        manager.set_settings_renderer_override("web")

        reloaded = SettingsManager(state_file)
        assert reloaded.get_settings_renderer_override() == "web"

    def test_value_is_normalized_to_lowercase(self, tmp_path):
        manager = SettingsManager(tmp_path / "session.dat")

        manager.set_settings_renderer_override("  WEB  ")

        assert manager.get_settings_renderer_override() == "web"

    def test_empty_string_clears_the_override(self, tmp_path):
        manager = SettingsManager(tmp_path / "session.dat")
        manager.set_settings_renderer_override("web")

        manager.set_settings_renderer_override("")

        assert manager.get_settings_renderer_override() == ""

    def test_invalid_value_raises_and_does_not_persist(self, tmp_path):
        manager = SettingsManager(tmp_path / "session.dat")

        with pytest.raises(ValueError):
            manager.set_settings_renderer_override("nonsense")

        assert manager.get_settings_renderer_override() == ""

    def test_an_older_state_file_missing_the_key_defaults_to_empty(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)
        del manager.state["settings_renderer_override"]
        manager._save_state()

        reloaded = SettingsManager(state_file)

        assert reloaded.get_settings_renderer_override() == ""
