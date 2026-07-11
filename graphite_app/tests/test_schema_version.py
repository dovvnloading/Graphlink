"""Tests for the schema_version field added to session.dat and saved chat payloads.

Regression coverage for doc/ARCHITECTURE_REVIEW_FINDINGS.md #49: neither file carried a
version field at all, so every future format change had no marker to branch a migration
on. This adds the version field itself (SettingsManager.CURRENT_SCHEMA_VERSION,
graphite_session.scene_index.CURRENT_CHAT_SCHEMA_VERSION) - not a migration framework,
just the groundwork one would need.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphite_licensing import SettingsManager
from graphite_scene import ChatScene
from graphite_session.scene_index import CURRENT_CHAT_SCHEMA_VERSION
from graphite_session.serializers import SceneSerializer


class TestSettingsManagerSchemaVersion:
    def test_a_fresh_settings_file_has_the_current_schema_version(self, tmp_path):
        manager = SettingsManager(tmp_path / "session.dat")
        assert manager.get_schema_version() == SettingsManager.CURRENT_SCHEMA_VERSION

    def test_an_old_settings_file_without_schema_version_is_backfilled(self, tmp_path):
        import json

        state_file = tmp_path / "session.dat"
        state_file.write_text(json.dumps({"theme": "mono"}), encoding="utf-8")

        manager = SettingsManager(state_file)

        # Backfilled in memory immediately, same as every other pre-existing key here
        # (theme, show_token_counter, ...) - none of those write back to disk until the
        # next explicit set_*() call either, so schema_version matching that existing
        # pattern is correct, not a gap.
        assert manager.get_schema_version() == SettingsManager.CURRENT_SCHEMA_VERSION

        manager.set_theme("mono")  # any setter call persists the whole (now-backfilled) state
        assert json.loads(state_file.read_text(encoding="utf-8"))["schema_version"] == SettingsManager.CURRENT_SCHEMA_VERSION


class TestChatPayloadSchemaVersion:
    def _make_serializer(self):
        window = MagicMock()
        scene = ChatScene(window=window)
        window.chat_view.scene.return_value = scene
        window.total_session_tokens = 0
        window.chat_view._zoom_factor = 1.0
        window.chat_view.horizontalScrollBar.return_value.value.return_value = 0
        window.chat_view.verticalScrollBar.return_value.value.return_value = 0
        return SceneSerializer(window), scene

    def test_serialized_chat_data_includes_the_current_schema_version(self):
        serializer, _scene = self._make_serializer()

        chat_data = serializer.serialize_chat_data()

        assert chat_data["schema_version"] == CURRENT_CHAT_SCHEMA_VERSION

    def test_current_chat_schema_version_is_1(self):
        # Pins the actual value, not just "some value" - a future intentional bump
        # should update this test deliberately, not by accident.
        assert CURRENT_CHAT_SCHEMA_VERSION == 1
