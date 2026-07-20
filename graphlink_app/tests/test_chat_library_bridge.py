"""Contract tests for the chat-library island bridge (Phase 4 increment 4)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from graphlink_chat_library_bridge import ChatLibraryBridge, _format_timestamp


class _FakeDatabase:
    """Minimal stand-in for ChatDatabase - same method shapes the bridge
    depends on (get_all_chats/delete_chat/rename_chat), so the bridge is
    exercised against its real contract without a real sqlite file."""

    def __init__(self, rows):
        # rows: list of (id, title, created_at, updated_at) tuples, exactly
        # what ChatDatabase.get_all_chats() returns.
        self._rows = list(rows)
        self.deleted = []
        self.renamed = []

    def get_all_chats(self):
        return list(self._rows)

    def delete_chat(self, chat_id):
        self.deleted.append(chat_id)
        self._rows = [r for r in self._rows if r[0] != chat_id]

    def rename_chat(self, chat_id, new_title):
        self.renamed.append((chat_id, new_title))
        self._rows = [
            (r[0], new_title, r[2], r[3]) if r[0] == chat_id else r for r in self._rows
        ]


class _FakeWindow:
    def __init__(self):
        self.title_bar_updates = 0
        self.new_chat_calls = []
        self.new_chat_result = True

    def update_title_bar(self):
        self.title_bar_updates += 1

    def new_chat(self, parent_for_dialog=None):
        self.new_chat_calls.append(parent_for_dialog)
        return self.new_chat_result


class _FakeSessionManager:
    def __init__(self, rows, window=None):
        self.db = _FakeDatabase(rows)
        self.window = window
        self.loaded = []
        self.load_error = None

    def load_chat(self, chat_id):
        if self.load_error is not None:
            raise self.load_error
        self.loaded.append(chat_id)


class _FakeDialog:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


_ROWS = [
    (1, "First chat", "2026-07-01 09:30:00", "2026-07-05 14:00:00"),
    (2, "Second chat", "2026-07-02 10:00:00", "2026-07-06 11:15:00"),
]


def _states(bridge):
    payloads = []
    bridge.stateChanged.connect(lambda p: payloads.append(json.loads(p)))
    return payloads


def _make(rows=_ROWS, window=None, dialog=None):
    session = _FakeSessionManager(rows, window=window)
    bridge = ChatLibraryBridge(session, dialog if dialog is not None else _FakeDialog())
    return bridge, session


class TestFormatTimestamp:
    def test_formats_the_stored_sqlite_format(self):
        assert _format_timestamp("2026-07-05 14:00:00") == "Jul 05, 2026 02:00 PM"

    def test_empty_becomes_unknown(self):
        assert _format_timestamp("") == "Unknown"
        assert _format_timestamp(None) == "Unknown"

    def test_unparseable_echoes_back_unchanged(self):
        assert _format_timestamp("not a timestamp") == "not a timestamp"


class TestReady:
    def test_publishes_rows_with_preformatted_labels(self):
        bridge, _ = _make()
        payloads = _states(bridge)

        bridge.ready()

        rows = payloads[-1]["rows"]
        assert [r["id"] for r in rows] == [1, 2]
        assert rows[0]["title"] == "First chat"
        assert rows[0]["createdLabel"] == "Jul 01, 2026 09:30 AM"
        assert rows[0]["updatedLabel"] == "Jul 05, 2026 02:00 PM"
        assert payloads[-1]["notice"] is None

    def test_publishes_empty_rows_when_the_db_is_empty(self):
        bridge, _ = _make(rows=[])
        payloads = _states(bridge)

        bridge.ready()

        assert payloads[-1]["rows"] == []


class TestListReadFailureBecomesANotice:
    def test_a_broken_get_all_chats_surfaces_as_a_notice_not_a_crash(self):
        bridge, session = _make()
        payloads = _states(bridge)

        def _boom():
            raise RuntimeError("db locked")

        session.db.get_all_chats = _boom

        bridge.ready()

        assert payloads[-1]["rows"] == []
        assert "db locked" in payloads[-1]["notice"]


class TestDelete:
    def test_delete_removes_the_row_and_republishes_without_confirmation(self):
        bridge, session = _make()
        payloads = _states(bridge)

        bridge.deleteChat(1)

        assert session.db.deleted == [1]
        assert [r["id"] for r in payloads[-1]["rows"]] == [2]


class TestRename:
    def test_rename_applies_the_new_title_and_republishes(self):
        bridge, session = _make()
        payloads = _states(bridge)

        bridge.renameChat(1, "Renamed")

        assert session.db.renamed == [(1, "Renamed")]
        assert payloads[-1]["rows"][0]["title"] == "Renamed"

    def test_rename_strips_whitespace(self):
        bridge, session = _make()

        bridge.renameChat(1, "  Trimmed  ")

        assert session.db.renamed == [(1, "Trimmed")]

    def test_empty_or_whitespace_title_is_ignored(self):
        bridge, session = _make()

        bridge.renameChat(1, "   ")
        bridge.renameChat(1, "")

        assert session.db.renamed == []


class TestLoad:
    def test_load_success_loads_updates_title_bar_and_closes_the_dialog(self):
        window = _FakeWindow()
        dialog = _FakeDialog()
        bridge, session = _make(window=window, dialog=dialog)

        bridge.loadChat(2)
        QApplication.processEvents()  # let QTimer.singleShot(0, ...) fire

        assert session.loaded == [2]
        assert window.title_bar_updates == 1
        assert dialog.closed == 1

    def test_load_failure_surfaces_a_notice_and_keeps_the_dialog_open(self):
        window = _FakeWindow()
        dialog = _FakeDialog()
        bridge, session = _make(window=window, dialog=dialog)
        session.load_error = ValueError("corrupt row")
        payloads = _states(bridge)

        bridge.loadChat(1)
        QApplication.processEvents()

        assert "corrupt row" in payloads[-1]["notice"]
        assert dialog.closed == 0


class TestNewChat:
    def test_new_chat_yes_closes_the_dialog(self):
        window = _FakeWindow()
        window.new_chat_result = True
        dialog = _FakeDialog()
        bridge, _ = _make(window=window, dialog=dialog)

        bridge.newChat()
        QApplication.processEvents()

        assert window.new_chat_calls == [dialog]  # parent_for_dialog is the native dialog
        assert dialog.closed == 1

    def test_new_chat_no_keeps_the_dialog_open(self):
        window = _FakeWindow()
        window.new_chat_result = False
        dialog = _FakeDialog()
        bridge, _ = _make(window=window, dialog=dialog)

        bridge.newChat()
        QApplication.processEvents()

        assert dialog.closed == 0


class TestDisposeIsIdempotent:
    def test_publish_is_a_no_op_after_dispose(self):
        bridge, _ = _make()
        payloads = _states(bridge)

        bridge.dispose()
        bridge.ready()

        assert payloads == []
        assert bridge.disposed is True

    def test_a_deferred_load_after_dispose_does_nothing(self):
        window = _FakeWindow()
        dialog = _FakeDialog()
        bridge, session = _make(window=window, dialog=dialog)

        bridge.loadChat(1)
        bridge.dispose()
        QApplication.processEvents()  # the deferred _perform_load_chat should bail on disposed

        assert session.loaded == []
        assert dialog.closed == 0
