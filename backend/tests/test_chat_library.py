"""Chat library topic tests (Qt-removal plan R2.5e + R6.4 loadChat + R6.5
saveChat/newChat)."""

import asyncio
import json
import sqlite3

import pytest

from backend.canvas import SceneDocument
from backend.chat_library import (
    _fallback_title,
    _format_timestamp,
    _resolve_seed_message,
    chat_library_payload,
    delete_chat,
    get_all_chats,
    load_chat_row,
    load_notes_rows,
    load_pins_rows,
    register_chat_library,
    rename_chat,
    save_chat_atomically_row,
)
from backend.events import SessionBus
from backend.notifications import NotificationState


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
    # A plain `assert "PySide6" not in sys.modules` is only meaningful in a
    # process where nothing else has imported PySide6 - running under the
    # full repo-wide pytest suite (alongside graphlink_app/tests' real Qt
    # widget tests), sys.modules is already contaminated regardless of what
    # this module itself imports. Only a fresh subprocess importing ONLY
    # backend.chat_library actually answers "does this transitively pull in
    # Qt" - exactly the graphlink_session/__init__.py hazard this module's
    # own docstring exists to route around.
    import subprocess
    import sys as _sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [_sys.executable, "-c", "import backend.chat_library, sys; assert 'PySide6' not in sys.modules"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


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


# -- R6.4: load_chat_row / load_notes_rows / load_pins_rows -----------------


def _insert_note(db_path, chat_id: int, **overrides) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "content TEXT NOT NULL, position_x REAL NOT NULL, position_y REAL NOT NULL, width REAL NOT NULL, "
            "height REAL NOT NULL, color TEXT NOT NULL, header_color TEXT, "
            "is_system_prompt INTEGER DEFAULT 0, is_summary_note INTEGER DEFAULT 0)"
        )
        row = {
            "content": "hello note", "position_x": 1.0, "position_y": 2.0,
            "width": 100.0, "height": 50.0, "color": "#111111", "header_color": None,
            "is_system_prompt": 0, "is_summary_note": 0,
        }
        row.update(overrides)
        conn.execute(
            "INSERT INTO notes (chat_id, content, position_x, position_y, width, height, color, "
            "header_color, is_system_prompt, is_summary_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, row["content"], row["position_x"], row["position_y"], row["width"], row["height"],
             row["color"], row["header_color"], row["is_system_prompt"], row["is_summary_note"]),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_pin(db_path, chat_id: int, **overrides) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pins (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "title TEXT NOT NULL, note TEXT, position_x REAL NOT NULL, position_y REAL NOT NULL, "
            "pin_id TEXT, sort_order INTEGER DEFAULT 0, anchor_item_id TEXT, created_at TEXT)"
        )
        row = {
            "title": "My Pin", "note": "", "position_x": 5.0, "position_y": 6.0,
            "pin_id": "pin-1", "sort_order": 0, "anchor_item_id": None, "created_at": "2026-01-01 00:00:00",
        }
        row.update(overrides)
        conn.execute(
            "INSERT INTO pins (chat_id, title, note, position_x, position_y, pin_id, sort_order, "
            "anchor_item_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, row["title"], row["note"], row["position_x"], row["position_y"],
             row["pin_id"], row["sort_order"], row["anchor_item_id"], row["created_at"]),
        )
        conn.commit()
    finally:
        conn.close()


def test_load_chat_row_returns_title_and_parsed_data(db_path):
    chat_id = _insert_chat(db_path, "Loadable", data=json.dumps({"nodes": [{"node_type": "chat"}]}))
    row = load_chat_row(db_path, chat_id)
    assert row == {"title": "Loadable", "data": {"nodes": [{"node_type": "chat"}]}}


def test_load_chat_row_returns_none_for_missing_id(db_path):
    assert load_chat_row(db_path, 999) is None


def test_load_notes_rows_shape_matches_session_load_expectations(db_path):
    chat_id = _insert_chat(db_path, "WithNotes")
    _insert_note(db_path, chat_id, content="A note", is_system_prompt=1)

    rows = load_notes_rows(db_path, chat_id)
    assert len(rows) == 1
    assert rows[0] == {
        "content": "A note", "position": {"x": 1.0, "y": 2.0}, "size": {"width": 100.0, "height": 50.0},
        "color": "#111111", "header_color": None, "is_system_prompt": True, "is_summary_note": False,
    }


def test_load_pins_rows_orders_by_sort_order_and_shape(db_path):
    chat_id = _insert_chat(db_path, "WithPins")
    _insert_pin(db_path, chat_id, title="Second", sort_order=1, pin_id="pin-b")
    _insert_pin(db_path, chat_id, title="First", sort_order=0, pin_id="pin-a")

    rows = load_pins_rows(db_path, chat_id)
    assert [row["title"] for row in rows] == ["First", "Second"]
    assert rows[0]["position"] == {"x": 5.0, "y": 6.0}
    assert rows[0]["pin_id"] == "pin-a"


# -- R6.4: the loadChat intent -----------------------------------------------


def _bus_with_canvas(db_path):
    """Mirrors backend/app.py's own registration order: canvas's "scene"
    topic must exist before register_chat_library's loadChat intent can
    publish to it - production guarantees this via _configure_session's own
    ordering; this test harness replicates it directly rather than pulling
    in the full register_canvas (which itself needs an agent dispatcher/
    composer document unrelated to what's under test here).

    autosave_interval_seconds=None disables R6.6's own background timer
    loop here - every test in this file runs in milliseconds, so a real
    30s-sleeping asyncio task would still be "pending" when asyncio.run()
    returns and the loop closes, leaking a task and spamming "Task was
    destroyed but it is pending" warnings across ~30 unrelated tests.
    backend/tests/test_autosave.py exercises the actual tick logic directly
    (no timer involved) instead."""
    bus = SessionBus("chat-library-load-test")
    document = SceneDocument()
    bus.register_topic("scene", document.scene_payload)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_chat_library(bus, db_path, document, notifications, autosave_interval_seconds=None)
    return bus, document, notifications


def test_load_chat_intent_restores_a_real_node_into_the_canvas_document(db_path):
    chat_data = {
        "nodes": [
            {"node_type": "chat", "id": "n1", "raw_content": "Hi", "is_user": True, "position": {"x": 0, "y": 0}},
        ],
    }
    chat_id = _insert_chat(db_path, "Real Session", data=json.dumps(chat_data))
    bus, document, notifications = _bus_with_canvas(db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "loadChat", [chat_id]))

    assert len(document.nodes) == 1
    node = next(iter(document.nodes.values()))
    assert node.kind == "chat" and node.content == "Hi" and node.is_user is True
    assert notifications.visible and notifications.msg_type == "success"
    scene_messages = [m for m in recorder.messages if m["topic"] == "scene"]
    assert scene_messages, "loadChat must publish a fresh scene snapshot"


def test_load_chat_intent_shows_an_error_notification_for_a_missing_chat(db_path):
    bus, document, notifications = _bus_with_canvas(db_path)

    asyncio.run(bus.dispatch_intent("app-chat-library", "loadChat", [999]))

    assert document.nodes == {}
    assert notifications.visible and notifications.msg_type == "error"


def test_load_chat_intent_restores_notes_and_pins_too(db_path):
    chat_data = {"nodes": []}
    chat_id = _insert_chat(db_path, "Notes And Pins", data=json.dumps(chat_data))
    _insert_note(db_path, chat_id, content="A restored note")
    _insert_pin(db_path, chat_id, title="A restored pin")
    bus, document, _ = _bus_with_canvas(db_path)

    asyncio.run(bus.dispatch_intent("app-chat-library", "loadChat", [chat_id]))

    notes = [n for n in document.nodes.values() if n.kind == "note"]
    assert len(notes) == 1 and notes[0].content == "A restored note"
    assert len(document.pins.records) == 1


# -- R6.5: save_chat_atomically_row / title helpers --------------------------


def test_save_chat_atomically_row_inserts_when_chat_id_is_none(db_path):
    new_id = save_chat_atomically_row(db_path, None, "New Title", {"nodes": []}, [], [])
    row = load_chat_row(db_path, new_id)
    assert row == {"title": "New Title", "data": {"nodes": []}}


def test_save_chat_atomically_row_updates_the_same_row_when_chat_id_given(db_path):
    first_id = save_chat_atomically_row(db_path, None, "First", {"nodes": [1]}, [], [])
    second_id = save_chat_atomically_row(db_path, first_id, "First", {"nodes": [1, 2]}, [], [])
    assert second_id == first_id
    assert len(get_all_chats(db_path)) == 1
    row = load_chat_row(db_path, first_id)
    assert row["data"] == {"nodes": [1, 2]}


def test_save_chat_atomically_row_replaces_notes_and_pins_wholesale(db_path):
    chat_id = save_chat_atomically_row(
        db_path, None, "T", {"nodes": []},
        [{"content": "note A", "position": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1},
          "color": "#fff", "header_color": None, "is_system_prompt": False, "is_summary_note": False}],
        [{"title": "pin A", "note": "", "position": {"x": 0, "y": 0}, "pin_id": "p1",
          "sort_order": 0, "anchor_item_id": None, "created_at": None}],
    )
    assert len(load_notes_rows(db_path, chat_id)) == 1
    assert len(load_pins_rows(db_path, chat_id)) == 1

    # Resaving with EMPTY notes/pins must wholesale-replace, not append to,
    # the previous set - mirrors ChatDatabase._write_notes/_write_pins's own
    # DELETE-then-reinsert-all contract exactly.
    save_chat_atomically_row(db_path, chat_id, "T", {"nodes": []}, [], [])
    assert load_notes_rows(db_path, chat_id) == []
    assert load_pins_rows(db_path, chat_id) == []


def test_fallback_title_matches_legacy_regex_and_truncation():
    assert _fallback_title("Hello, world! This is a test message.") == "Hello world This is a"
    assert _fallback_title("") .startswith("Chat 20")
    assert _fallback_title("...") .startswith("Chat 20")
    long_word_title = _fallback_title("a" * 200)
    assert len(long_word_title) == 80


def test_resolve_seed_message_uses_last_chat_node_content():
    document = SceneDocument()
    document.add_chat_node(0, 0, "first message", is_user=True)
    ai = document.add_chat_node(0, 100, "second message", is_user=False)
    assert _resolve_seed_message(document) == "second message"


def test_resolve_seed_message_falls_back_to_new_chat_when_no_chat_nodes():
    document = SceneDocument()
    document.add_note(0, 0)
    assert _resolve_seed_message(document) == "New Chat"


# -- R6.5: the saveChat / newChat intents ------------------------------------


def test_save_chat_intent_warns_and_skips_write_for_a_never_saved_empty_canvas(db_path):
    bus, document, notifications = _bus_with_canvas(db_path)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    assert get_all_chats(db_path) == []
    assert notifications.visible and notifications.msg_type == "warning"


def test_save_chat_intent_inserts_a_new_row_and_adopts_the_id(db_path):
    bus, document, notifications = _bus_with_canvas(db_path)
    document.add_chat_node(0, 0, "hello world", is_user=True)

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))

    rows = get_all_chats(db_path)
    assert len(rows) == 1
    assert document.current_chat_id == rows[0]["id"]
    assert notifications.visible and notifications.msg_type == "success"


def test_save_chat_intent_resave_updates_same_row_and_keeps_existing_title(db_path):
    bus, document, notifications = _bus_with_canvas(db_path)
    document.add_chat_node(0, 0, "hello world", is_user=True)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    first_id = document.current_chat_id
    first_title = get_all_chats(db_path)[0]["title"]

    document.add_code_node(100, 0, "x = 1", "python")
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))

    rows = get_all_chats(db_path)
    assert len(rows) == 1
    assert rows[0]["id"] == first_id
    assert rows[0]["title"] == first_title
    row = load_chat_row(db_path, first_id)
    assert len(row["data"]["nodes"]) == 2


def test_save_chat_intent_falls_back_to_insert_when_current_row_was_deleted_elsewhere(db_path):
    bus, document, notifications = _bus_with_canvas(db_path)
    document.current_chat_id = 999  # a row that never existed / was deleted
    document.add_chat_node(0, 0, "hello world", is_user=True)

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))

    assert notifications.visible and notifications.msg_type == "success"
    rows = get_all_chats(db_path)
    assert len(rows) == 1
    assert document.current_chat_id == rows[0]["id"] != 999


def test_new_chat_intent_clears_canvas_and_resets_current_chat_id(db_path):
    bus, document, _ = _bus_with_canvas(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)
    document.current_chat_id = 5

    asyncio.run(bus.dispatch_intent("app-chat-library", "newChat", []))

    assert document.nodes == {}
    assert document.current_chat_id is None


def test_two_concurrent_save_chat_calls_do_not_race_only_one_row_is_written(db_path):
    # Adversarial review finding: loadChat/saveChat/newChat share ONE
    # canvas_document, and a session can have MULTIPLE attached WS
    # connections (every tab that doesn't pass its own ?session= shares
    # session="default") - without a reentrancy guard, two tabs racing Save
    # could interleave mid-await (asyncio.to_thread yields control back to
    # the loop) and silently corrupt or double-write. asyncio.gather here
    # genuinely interleaves both coroutines on the same event loop, exactly
    # the scenario two real WS connections would create.
    bus, document, notifications = _bus_with_canvas(db_path)
    document.add_chat_node(0, 0, "hello world", is_user=True)
    recorder = Recorder()
    bus.attach(recorder)

    async def _race():
        await asyncio.gather(
            bus.dispatch_intent("app-chat-library", "saveChat", []),
            bus.dispatch_intent("app-chat-library", "saveChat", []),
        )

    asyncio.run(_race())

    # Exactly one row, regardless of which of the two calls "won" - the
    # loser must have been rejected by the guard, not raced to a second
    # INSERT.
    assert len(get_all_chats(db_path)) == 1

    notification_messages = [
        m["payload"]["message"] for m in recorder.messages if m.get("topic") == "notification"
    ]
    assert any("already in progress" in message for message in notification_messages), notification_messages


# -- R6.6 regression: register_chat_library must survive a missing event loop --


def test_register_chat_library_does_not_crash_without_a_running_event_loop(db_path):
    # A real, shipped bug: register_chat_library's own R6.6 addition
    # (register_autosave) called asyncio.create_task() unconditionally, which
    # raises RuntimeError outside of a running event loop. backend/app.py's
    # _configure_session calls register_chat_library from exactly this kind
    # of sync context under Starlette's TestClient (confirmed - it broke
    # test_ws_origin.py and test_assets.py, both unrelated to chat_library),
    # so this proves the real production call shape - a bare, non-async
    # call, default autosave_interval_seconds - can never take core session
    # setup down with it.
    bus = SessionBus("chat-library-no-loop-test")
    document = SceneDocument()
    bus.register_topic("scene", document.scene_payload)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)

    register_chat_library(bus, db_path, document, notifications)

    assert bus.autosave_task is None
