"""Tests for ChatDatabase.get_chat_title().

Regression coverage for doc/ARCHITECTURE_REVIEW_FINDINGS.md #45: update_title_bar()
used to call db.load_chat(), which SELECTs both title and data and json.loads()-decodes
the full chat payload (every node, connection, and base64-inlined image - see #50) just
to read the title string, on every single save completion. get_chat_title() only SELECTs
the title column and never touches `data` at all.

Uses ChatDatabase(tmp_path / ...) - the constructor's db_path parameter (added by the
#55 fix) - instead of the real constructor's hardcoded Path.home()/.graphlink/chats.db,
so this never touches the developer's real chat database.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import graphlink_window
from graphlink_session.database import ChatDatabase


def _make_test_db(tmp_path):
    return ChatDatabase(tmp_path / "test_chats.db")


class TestGetChatTitle:
    def test_returns_the_title_for_an_existing_chat(self, tmp_path):
        db = _make_test_db(tmp_path)
        chat_id = db.save_chat("My Chat Title", {"nodes": []})

        assert db.get_chat_title(chat_id) == "My Chat Title"

    def test_returns_none_for_a_nonexistent_chat_id(self, tmp_path):
        db = _make_test_db(tmp_path)

        assert db.get_chat_title(999999) is None

    def test_reflects_a_rename_without_needing_load_chat(self, tmp_path):
        db = _make_test_db(tmp_path)
        chat_id = db.save_chat("Original Title", {"nodes": []})

        db.rename_chat(chat_id, "Renamed Title")

        assert db.get_chat_title(chat_id) == "Renamed Title"

    def test_does_not_require_the_data_column_to_be_valid_json_it_never_reads(self, tmp_path):
        # get_chat_title() must never touch/decode the `data` column - if it did, this
        # would raise a JSONDecodeError instead of returning the title.
        import sqlite3

        db = _make_test_db(tmp_path)
        with sqlite3.connect(db.db_path) as conn:
            conn.execute(
                "INSERT INTO chats (title, data) VALUES (?, ?)",
                ("Corrupted Payload Chat", "{not valid json"),
            )
            chat_id = conn.execute("SELECT id FROM chats WHERE title = ?", ("Corrupted Payload Chat",)).fetchone()[0]

        assert db.get_chat_title(chat_id) == "Corrupted Payload Chat"


class TestUpdateTitleBarUsesGetChatTitleNotLoadChat:
    def _make_fake_window(self, current_chat_id):
        window = MagicMock()
        window.session_manager.current_chat_id = current_chat_id
        return window

    def test_calls_get_chat_title_and_never_load_chat(self):
        window = self._make_fake_window(current_chat_id=7)
        window.session_manager.db.get_chat_title.return_value = "Saved Chat Title"

        graphlink_window.ChatWindow.update_title_bar(window)

        window.session_manager.db.get_chat_title.assert_called_once_with(7)
        window.session_manager.db.load_chat.assert_not_called()
        window.setWindowTitle.assert_called_once_with("Graphlink - Saved Chat Title")

    def test_falls_back_to_plain_title_when_there_is_no_current_chat(self):
        window = self._make_fake_window(current_chat_id=None)

        graphlink_window.ChatWindow.update_title_bar(window)

        window.session_manager.db.get_chat_title.assert_not_called()
        window.setWindowTitle.assert_called_once_with("Graphlink")
