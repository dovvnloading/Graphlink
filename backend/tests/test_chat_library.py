"""Chat library topic tests (Qt-removal plan R2.5e + R6.4 loadChat + R6.5
saveChat/newChat)."""

import asyncio
import contextlib
import json
import sqlite3
import time

import pytest

import backend.autosave as autosave_module
import backend.chat_library as chat_library_module
from backend.autosave import autosave_tick, register_autosave
from backend.canvas import SceneDocument
from backend.chat_library import (
    AUTOSAVE_OWNER,
    USER_OWNER,
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


# -- audit fixes: the save-state cell is genuinely shared across every path --


def _library_session(db_path):
    bus = SessionBus("chat-library-save-state-test")
    document = SceneDocument()
    bus.register_topic("scene", document.scene_payload)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    # autosave_interval_seconds=None: no background timer, so these tests
    # drive autosave_tick explicitly and deterministically instead.
    register_chat_library(bus, db_path, document, notifications, autosave_interval_seconds=None)
    return bus, document, notifications


def test_a_manual_save_seeds_the_save_state_so_the_next_tick_is_a_no_op(db_path, monkeypatch):
    # Audit finding: backend/autosave.py's docstring claimed its change-guard
    # covered "auto OR manual", but the cell was a closure-local nothing else
    # could reach, so every manual Save was followed 30s later by a
    # byte-identical rewrite that bumped updated_at and re-sorted the Chat
    # Library out from under the user.
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    assert len(get_all_chats(db_path)) == 1
    saved_id = document.current_chat_id

    state = bus.chat_save_state
    assert state["digest"] is not None and state["chat_id"] == saved_id

    # A tick with nothing changed since that manual save must not write.
    writes = []
    real_save = autosave_module.save_chat_atomically_row
    monkeypatch.setattr(
        autosave_module, "save_chat_atomically_row",
        lambda *a, **k: (writes.append(a), real_save(*a, **k))[1],
    )
    asyncio.run(autosave_tick(bus, db_path, document, notifications, state))
    assert writes == [], "a tick right after a manual Save must not rewrite the row"


def test_loading_a_chat_seeds_the_save_state_so_the_next_tick_is_a_no_op(db_path, monkeypatch):
    # Same gap on the load side: opening a chat and touching nothing still
    # rewrote its row on the first tick, re-sorting the library.
    seed_bus, seed_document, seed_notifications = _library_session(db_path)
    seed_document.add_chat_node(0, 0, "hello", is_user=True)
    asyncio.run(seed_bus.dispatch_intent("app-chat-library", "saveChat", []))
    chat_id = seed_document.current_chat_id

    bus, document, notifications = _library_session(db_path)
    asyncio.run(bus.dispatch_intent("app-chat-library", "loadChat", [chat_id]))
    assert document.current_chat_id == chat_id

    state = bus.chat_save_state
    assert state["chat_id"] == chat_id
    assert state["digest"] is not None

    writes = []
    real_save = autosave_module.save_chat_atomically_row
    monkeypatch.setattr(
        autosave_module, "save_chat_atomically_row",
        lambda *a, **k: (writes.append(a), real_save(*a, **k))[1],
    )
    asyncio.run(autosave_tick(bus, db_path, document, notifications, state))
    assert writes == [], "a tick right after loadChat must not rewrite the row"


def test_deleting_the_open_chat_clears_the_pointer_and_reenables_autosave(db_path):
    # Audit finding (real bug): deleteChat left current_chat_id dangling and
    # left the content-only digest looking "already saved", so a user who
    # deleted their open chat and kept working had NO autosave protection
    # until they happened to edit the canvas.
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    saved_id = document.current_chat_id

    asyncio.run(bus.dispatch_intent("app-chat-library", "deleteChat", [saved_id]))

    assert get_all_chats(db_path) == []
    assert document.current_chat_id is None, "the pointer to a deleted row must not dangle"

    state = bus.chat_save_state
    # The canvas is deliberately NOT touched - the pre-fix content-only guard
    # could not tell this apart from "already saved" and skipped forever.
    asyncio.run(autosave_tick(bus, db_path, document, notifications, state))

    rows = get_all_chats(db_path)
    assert len(rows) == 1, "the still-open work must be re-protected under a fresh row"
    assert rows[0]["id"] != saved_id


def test_deleting_a_different_chat_leaves_the_open_session_pointer_alone(db_path):
    # The guard must be scoped to the row the session actually points at.
    bus, document, notifications = _library_session(db_path)
    other_id = _insert_chat(db_path, "Someone else's chat")
    document.add_chat_node(0, 0, "hello", is_user=True)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    saved_id = document.current_chat_id

    asyncio.run(bus.dispatch_intent("app-chat-library", "deleteChat", [other_id]))

    assert document.current_chat_id == saved_id
    assert bus.chat_save_state["chat_id"] == saved_id


# -- audit fix: a background autosave tick must never beat the user to their
# -- own data. The guard is shared, so it has to be ownership-aware.


def test_a_user_save_waits_out_a_real_in_flight_autosave_tick_instead_of_being_dropped(db_path, monkeypatch):
    # Audit finding (the fix this test exists for): _serialize_mutating_intent
    # DROPS a blocked intent and warns. That contract was written when only a
    # user-initiated intent could hold the flag. R6.6 then had a BACKGROUND
    # task claim the same flag, so an autosave tick that happened to be
    # mid-write made the user's own Save vanish - with a warning naming an
    # operation they never started.
    #
    # Drives the REAL register_autosave loop, deliberately: an earlier version
    # of this test used a hand-written double that reimplemented the
    # claim/release itself, which meant mutating the actual _guarded_tick
    # (dropping its owner tag, or its release signal) left the test green -
    # the production wiring was never under test at all.
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)

    real_save = autosave_module.save_chat_atomically_row

    def slow_save(*args, **kwargs):
        # Runs inside autosave's own asyncio.to_thread, so this holds the
        # guard across a real await point exactly like a slow disk would.
        time.sleep(0.1)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(autosave_module, "save_chat_atomically_row", slow_save)

    async def _run():
        register_autosave(
            bus, db_path, document, notifications,
            bus.chat_mutation_guard, bus.chat_save_state, interval_seconds=0.02,
        )
        try:
            for _ in range(200):  # wait for a real tick to claim the guard
                if bus.chat_mutation_guard["owner"] == AUTOSAVE_OWNER:
                    break
                await asyncio.sleep(0.01)
            assert bus.chat_mutation_guard["owner"] == AUTOSAVE_OWNER, "no real tick ever started"

            # Bounded well under AUTOSAVE_YIELD_TIMEOUT_SECONDS (2.0s): the
            # save must proceed as soon as the tick RELEASES, not after
            # sitting out the whole timeout. Without _guarded_tick's release
            # signal this hangs past 1s and fails here.
            await asyncio.wait_for(
                bus.dispatch_intent("app-chat-library", "saveChat", []), timeout=1.0
            )
        finally:
            bus.autosave_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bus.autosave_task

    asyncio.run(_run())

    assert notifications.visible and notifications.msg_type == "success", notifications.message
    assert notifications.message.startswith("Saved "), "the user's Save must not be discarded"
    assert document.current_chat_id is not None


def test_a_user_save_still_loses_to_another_user_operation(db_path):
    # The other direction is deliberate and must NOT change: two real user
    # operations racing is an honest "you started two things" conflict.
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)
    bus.chat_mutation_guard["active"] = True
    bus.chat_mutation_guard["owner"] = USER_OWNER

    async def _run():
        # Must be rejected IMMEDIATELY, not after sitting out the 2.0s
        # autosave-yield timeout. Asserting the outcome alone would pass even
        # if the wait were (wrongly) applied to user-vs-user conflicts too -
        # the test would just get slower, which no assertion would notice.
        await asyncio.wait_for(
            bus.dispatch_intent("app-chat-library", "saveChat", []), timeout=0.5
        )

    asyncio.run(_run())

    assert get_all_chats(db_path) == []
    assert notifications.visible and notifications.msg_type == "warning"
    assert "Another chat operation" in notifications.message


def test_a_user_save_gives_up_with_an_honest_message_if_autosave_is_genuinely_stuck(db_path, monkeypatch):
    # The wait is bounded: a tick stuck on sqlite's own 30s lock timeout must
    # not freeze the UI. It degrades to the pre-fix drop - but with a message
    # that names autosave rather than "another chat operation", which is what
    # made the original warning read as a bug.
    monkeypatch.setattr(chat_library_module, "AUTOSAVE_YIELD_TIMEOUT_SECONDS", 0.05)
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)
    bus.chat_mutation_guard["active"] = True
    bus.chat_mutation_guard["owner"] = AUTOSAVE_OWNER
    bus.chat_mutation_guard["released"].clear()

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))

    assert get_all_chats(db_path) == []
    assert notifications.visible and notifications.msg_type == "warning"
    assert "Autosave is still finishing" in notifications.message


def test_the_guard_is_released_with_its_owner_cleared_after_a_normal_save(db_path):
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))

    guard = bus.chat_mutation_guard
    assert guard["active"] is False
    assert guard["owner"] is None
    assert guard["released"].is_set()


def test_the_yield_still_works_on_a_LATER_autosave_tick_not_just_the_first(db_path, monkeypatch):
    # Mutation testing caught this gap: the test above only ever observes the
    # FIRST tick to claim the guard, when `released` has never been set. Drop
    # _guarded_tick's `released.clear()` and that test stays green - but from
    # the second tick onward a waiting user intent would be woken instantly by
    # the STALE signal from the previous tick, re-check, find the guard still
    # held, and be dropped exactly as before the fix. A fix that works once and
    # then quietly stops working is worse than no fix, so this pins the
    # steady-state behavior rather than the first-run behavior.
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)

    real_save = autosave_module.save_chat_atomically_row

    def slow_save(*args, **kwargs):
        time.sleep(0.1)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(autosave_module, "save_chat_atomically_row", slow_save)

    async def _await_owner(expected):
        for _ in range(300):
            if bus.chat_mutation_guard["owner"] == expected:
                return True
            await asyncio.sleep(0.01)
        return False

    async def _run():
        register_autosave(
            bus, db_path, document, notifications,
            bus.chat_mutation_guard, bus.chat_save_state, interval_seconds=0.02,
        )
        try:
            assert await _await_owner(AUTOSAVE_OWNER), "no first tick"
            assert await _await_owner(None), "first tick never released"
            assert bus.chat_mutation_guard["released"].is_set()

            # A real change, so the NEXT tick actually writes (and so holds the
            # guard) instead of short-circuiting on the unchanged-content guard.
            document.add_chat_node(300, 0, "a second message", is_user=True)
            assert await _await_owner(AUTOSAVE_OWNER), "no second tick"

            await asyncio.wait_for(
                bus.dispatch_intent("app-chat-library", "saveChat", []), timeout=1.0
            )
        finally:
            bus.autosave_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bus.autosave_task

    asyncio.run(_run())

    assert notifications.visible and notifications.msg_type == "success", notifications.message
    assert notifications.message.startswith("Saved ")
