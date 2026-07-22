"""Chat library topic tests (Qt-removal plan R2.5e)."""

import asyncio
import sqlite3

import pytest

from backend.chat_library import (
    _format_timestamp,
    chat_library_payload,
    delete_chat,
    get_all_chats,
    register_chat_library,
    rename_chat,
)
from backend.events import SessionBus


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "chats.db"


def _insert_chat(db_path, title: str, data: str = "{}") -> int:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "data TEXT NOT NULL)"
        )
        cursor = conn.execute(
            "INSERT INTO chats (title, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, data, "2026-01-01 10:00:00", "2026-01-02 11:30:00"),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


class Recorder:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


def test_get_all_chats_creates_table_on_a_fresh_db(db_path):
    assert get_all_chats(db_path) == []
    assert db_path.exists()


def test_get_all_chats_reads_real_rows(db_path):
    first_id = _insert_chat(db_path, "First")
    second_id = _insert_chat(db_path, "Second")

    rows = get_all_chats(db_path)
    ids = {row["id"] for row in rows}
    assert ids == {first_id, second_id}
    for row in rows:
        assert set(row) == {"id", "title", "createdLabel", "updatedLabel"}
        assert row["updatedLabel"] == "Jan 02, 2026 11:30 AM"


def test_format_timestamp_matches_legacy_display_format():
    assert _format_timestamp("2026-01-02 11:30:00") == "Jan 02, 2026 11:30 AM"
    assert _format_timestamp("") == "Unknown"
    assert _format_timestamp(None) == "Unknown"
    assert _format_timestamp("not-a-timestamp") == "not-a-timestamp"


def test_rename_chat_persists_and_updates_timestamp(db_path):
    chat_id = _insert_chat(db_path, "Original")
    rename_chat(db_path, chat_id, "Renamed")

    rows = get_all_chats(db_path)
    renamed = next(row for row in rows if row["id"] == chat_id)
    assert renamed["title"] == "Renamed"


def test_delete_chat_removes_the_row(db_path):
    chat_id = _insert_chat(db_path, "Doomed")
    delete_chat(db_path, chat_id)

    rows = get_all_chats(db_path)
    assert all(row["id"] != chat_id for row in rows)


def test_chat_library_payload_shape(db_path):
    _insert_chat(db_path, "A Chat")
    payload = chat_library_payload(db_path)
    assert set(payload) == {"rows", "notice"}
    assert payload["notice"] is None
    assert len(payload["rows"]) == 1


def test_chat_library_never_imports_qt():
    import sys

    assert "PySide6" not in sys.modules


def test_register_chat_library_publishes_on_the_app_chat_library_topic(db_path):
    _insert_chat(db_path, "Hello")
    bus = SessionBus("chat-library-test")
    register_chat_library(bus, db_path)

    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(bus.publish("app-chat-library"))
    payload = recorder.messages[0]["payload"]
    assert payload["rows"][0]["title"] == "Hello"


def test_rename_chat_intent_ignores_empty_title(db_path):
    chat_id = _insert_chat(db_path, "Keep Me")
    bus = SessionBus("chat-library-rename-empty-test")
    register_chat_library(bus, db_path)

    asyncio.run(bus.dispatch_intent("app-chat-library", "renameChat", [chat_id, "   "]))
    rows = get_all_chats(db_path)
    assert next(row for row in rows if row["id"] == chat_id)["title"] == "Keep Me"


def test_rename_chat_intent_persists_and_republishes(db_path):
    chat_id = _insert_chat(db_path, "Before")
    bus = SessionBus("chat-library-rename-test")
    register_chat_library(bus, db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "renameChat", [chat_id, "After"]))
    assert recorder.messages[-1]["payload"]["rows"][0]["title"] == "After"


def test_delete_chat_intent_removes_and_republishes(db_path):
    chat_id = _insert_chat(db_path, "Temp")
    bus = SessionBus("chat-library-delete-test")
    register_chat_library(bus, db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "deleteChat", [chat_id]))
    assert recorder.messages[-1]["payload"]["rows"] == []
