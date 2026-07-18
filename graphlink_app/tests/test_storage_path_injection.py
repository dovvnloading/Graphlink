"""Tests for optional path injection on ChatDatabase and SettingsManager.

Both classes used to hardcode Path.home()/.graphlink/... with no way to redirect them,
forcing tests that needed an isolated instance to bypass __init__ entirely via __new__
(see test_chat_database_get_title.py's original _make_test_db helper). Both
constructors now accept an optional path parameter; omitting it preserves the exact
original behavior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_licensing import SettingsManager
from graphlink_session.database import ChatDatabase


class TestChatDatabasePathInjection:
    def test_default_constructor_still_resolves_to_the_original_hardcoded_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        db = ChatDatabase()

        assert db.db_path == tmp_path / ".graphlink" / "chats.db"
        assert db.db_path.parent.is_dir()

    def test_explicit_path_is_honored_and_never_touches_home(self, tmp_path, monkeypatch):
        def _fail_if_called(cls):
            raise AssertionError("Path.home() should not be called when db_path is passed explicitly")

        monkeypatch.setattr(Path, "home", classmethod(_fail_if_called))
        custom_path = tmp_path / "custom" / "my_chats.db"

        db = ChatDatabase(custom_path)

        assert db.db_path == custom_path
        assert db.db_path.parent.is_dir()

    def test_explicit_path_accepts_a_plain_string_too(self, tmp_path):
        custom_path = tmp_path / "string_path" / "chats.db"

        db = ChatDatabase(str(custom_path))

        assert db.db_path == custom_path

    def test_an_injected_database_is_fully_usable(self, tmp_path):
        db = ChatDatabase(tmp_path / "isolated.db")
        chat_id = db.save_chat("Test Chat", {"nodes": []})

        assert db.get_chat_title(chat_id) == "Test Chat"


class TestSettingsManagerPathInjection:
    def test_default_constructor_still_resolves_to_the_original_hardcoded_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        manager = SettingsManager()

        assert manager.state_file == tmp_path / ".graphlink" / "session.dat"
        assert manager.state_file.parent.is_dir()

    def test_explicit_path_is_honored_and_never_touches_home(self, tmp_path, monkeypatch):
        def _fail_if_called(cls):
            raise AssertionError("Path.home() should not be called when state_file is passed explicitly")

        monkeypatch.setattr(Path, "home", classmethod(_fail_if_called))
        custom_path = tmp_path / "custom" / "my_session.dat"

        manager = SettingsManager(custom_path)

        assert manager.state_file == custom_path
        assert manager.state_file.parent.is_dir()

    def test_an_injected_settings_manager_persists_independently_of_the_real_one(self, tmp_path):
        manager = SettingsManager(tmp_path / "isolated_session.dat")
        manager.set_theme("mono")

        reloaded = SettingsManager(tmp_path / "isolated_session.dat")

        assert reloaded.get_theme() == "mono"
