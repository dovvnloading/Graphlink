"""Tests for SettingsManager's atomic writes and corrupt-file recovery.

Regression coverage for non-atomic settings writes: _save_state used to write directly
to session.dat, so a crash mid-write left a truncated/corrupt file, and _load_state's
JSONDecodeError handler then silently replaced it with defaults - destroying every
saved API key and preference with no backup and no warning.

_save_state now writes to a temp file (fsync'd) and atomically renames it into place, so
state_file itself is always either the fully-old or fully-new version, never a partial
write. _load_state now backs up an unreadable file before resetting to defaults, instead
of silently discarding it.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_licensing import SettingsManager


class TestAtomicSave:
    def test_save_round_trips_correctly(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)
        manager.set_theme("mono")

        reloaded = SettingsManager(state_file)

        assert reloaded.get_theme() == "mono"

    def test_no_leftover_temp_file_after_a_successful_save(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)

        manager.set_theme("muted")

        tmp_file = tmp_path / "session.dat.tmp"
        assert not tmp_file.exists()
        assert state_file.exists()

    def test_uses_os_replace_for_the_final_rename(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)

        with patch("graphlink_licensing.os.replace") as mock_replace:
            manager.set_theme("dark")

        mock_replace.assert_called_once()
        src, dst = mock_replace.call_args[0]
        assert Path(dst) == state_file
        assert str(src).endswith("session.dat.tmp")

    def test_a_failure_partway_through_the_write_leaves_the_original_file_untouched(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)
        manager.set_theme("mono")  # establish a known-good on-disk state
        original_bytes = state_file.read_bytes()

        with patch("graphlink_licensing.os.fsync", side_effect=OSError("simulated disk failure")):
            manager.set_theme("muted")  # this save should fail cleanly

        assert state_file.read_bytes() == original_bytes  # untouched, not truncated
        assert manager.state["theme"] == "muted"  # in-memory state still updated
        assert not (tmp_path / "session.dat.tmp").exists()  # temp file cleaned up

    def test_temp_file_lives_next_to_the_real_file_so_the_rename_is_same_volume(self, tmp_path):
        state_file = tmp_path / "nested" / "session.dat"
        manager = SettingsManager(state_file)

        with patch("graphlink_licensing.os.replace") as mock_replace:
            manager.set_theme("mono")

        src, dst = mock_replace.call_args[0]
        assert Path(src).parent == Path(dst).parent


class TestCorruptStateFileRecovery:
    def test_invalid_json_is_backed_up_before_resetting_to_defaults(self, tmp_path):
        state_file = tmp_path / "session.dat"
        state_file.write_text("{not valid json at all", encoding="utf-8")

        manager = SettingsManager(state_file)

        backups = list(tmp_path.glob("session.dat.corrupted-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "{not valid json at all"

    def test_manager_falls_back_to_defaults_after_a_corrupt_file(self, tmp_path):
        state_file = tmp_path / "session.dat"
        state_file.write_text("{not valid json at all", encoding="utf-8")

        manager = SettingsManager(state_file)

        assert manager.get_theme() == "dark"  # default theme

    def test_state_file_itself_is_valid_json_again_after_recovery(self, tmp_path):
        state_file = tmp_path / "session.dat"
        state_file.write_text("{not valid json at all", encoding="utf-8")

        SettingsManager(state_file)

        # _create_initial_state() saves the fresh defaults back out - state_file should
        # be parseable again, not left corrupt.
        json.loads(state_file.read_text(encoding="utf-8"))

    def test_a_valid_but_incomplete_state_file_is_not_treated_as_corrupt(self, tmp_path):
        # Partial/older-schema state (missing newer keys) is a normal migration case,
        # not corruption - must not trigger a backup.
        state_file = tmp_path / "session.dat"
        state_file.write_text(json.dumps({"theme": "mono"}), encoding="utf-8")

        manager = SettingsManager(state_file)

        assert list(tmp_path.glob("session.dat.corrupted-*")) == []
        assert manager.get_theme() == "mono"
        assert manager.get_show_token_counter() is True  # backfilled default
