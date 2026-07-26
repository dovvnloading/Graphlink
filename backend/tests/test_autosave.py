"""Autosave tests (Qt-removal plan R6.6).

Exercises backend/autosave.py's autosave_tick directly wherever possible
(no real sleep involved, deterministic) - the timer loop itself
(register_autosave) is only exercised in the 2 tests that specifically
need to prove the guard/wiring behavior, each with a tiny interval and an
explicit task cancellation in a finally block so nothing leaks into a
later test.
"""

import asyncio

import pytest

from backend.autosave import autosave_tick, register_autosave
from backend.canvas import SceneDocument
from backend.chat_library import (
    _new_save_state,
    chat_library_payload,
    delete_chat,
    get_all_chats,
    load_chat_row,
    rename_chat,
)
from backend.events import SessionBus
from backend.notifications import NotificationState


class Recorder:
    """Same shape backend/tests/test_chat_library.py already uses - the bus's
    Connection protocol is just an async send_json(dict)."""

    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "chats.db"


def _bus(db_path):
    """Registers "app-chat-library" via the real chat_library_payload
    builder (autosave_tick publishes to it on every successful save) -
    without pulling in the full register_chat_library (which would ALSO
    register loadChat/saveChat/newChat intents this file has no interest
    in), keeping this test file focused on autosave alone."""
    bus = SessionBus("autosave-test")
    document = SceneDocument()
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("app-chat-library", lambda: chat_library_payload(db_path))
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    return bus, document, notifications


def test_autosave_tick_skips_an_empty_never_saved_canvas(db_path):
    bus, document, notifications = _bus(db_path)
    last_saved = _new_save_state()

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    assert get_all_chats(db_path) == []
    assert last_saved["digest"] is None
    assert not notifications.visible


def test_autosave_tick_inserts_a_new_row_for_a_fresh_canvas_with_content(db_path):
    bus, document, notifications = _bus(db_path)
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    last_saved = _new_save_state()

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    rows = get_all_chats(db_path)
    assert len(rows) == 1
    assert document.current_chat_id == rows[0]["id"]
    assert last_saved["digest"] is not None
    # Silent on success - autosave must never show a "Saved" toast the way
    # the explicit Save button does (see this module's own docstring).
    assert not notifications.visible


def test_autosave_tick_is_a_no_op_when_nothing_changed_since_the_last_tick(db_path, monkeypatch):
    bus, document, notifications = _bus(db_path)
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    last_saved = _new_save_state()

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))
    first_digest = last_saved["digest"]

    import backend.autosave as autosave_module
    calls = []
    real_save = autosave_module.save_chat_atomically_row

    def counting_save(*args, **kwargs):
        calls.append(args)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(autosave_module, "save_chat_atomically_row", counting_save)

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    assert calls == [], "a second tick with no scene changes must not write again"
    assert last_saved["digest"] == first_digest
    assert len(get_all_chats(db_path)) == 1


def test_autosave_tick_writes_again_after_a_real_change(db_path):
    bus, document, notifications = _bus(db_path)
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    last_saved = _new_save_state()
    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))
    first_digest = last_saved["digest"]

    document.add_code_node(100, 0, "x = 1", "python")
    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    assert last_saved["digest"] != first_digest
    row = load_chat_row(db_path, document.current_chat_id)
    assert len(row["data"]["nodes"]) == 2


def test_autosave_tick_never_regenerates_an_existing_chats_title(db_path):
    # Audit fix - this test used to be unable to fail. Its only inter-tick
    # change was add_code_node, but _resolve_seed_message reads chat-kind
    # nodes ONLY, so the regenerated title would have been byte-identical to
    # the original and the assertion held whether or not the implementation
    # preserved the row's title (verified by mutation: swapping the
    # preserve-title line for the regeneration it exists to prevent still
    # passed). Renaming the row out of band - what the library's own Rename
    # action really does - plus a seed-changing chat node makes regeneration
    # observably wrong, which is the only version of this that can fail.
    bus, document, notifications = _bus(db_path)
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    last_saved = _new_save_state()
    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))
    chat_id = document.current_chat_id

    rename_chat(db_path, chat_id, "Q3 planning")
    document.add_chat_node(200, 0, "an entirely different seed message", is_user=True)

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    rows = get_all_chats(db_path)
    assert len(rows) == 1
    assert rows[0]["title"] == "Q3 planning"


def test_autosave_tick_falls_back_to_insert_when_current_row_was_deleted_elsewhere(db_path):
    bus, document, notifications = _bus(db_path)
    document.current_chat_id = 999
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    last_saved = _new_save_state()

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    rows = get_all_chats(db_path)
    assert len(rows) == 1
    assert document.current_chat_id == rows[0]["id"] != 999


def test_autosave_tick_shows_an_error_notification_on_db_failure(db_path, monkeypatch):
    bus, document, notifications = _bus(db_path)
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    last_saved = _new_save_state()

    import backend.autosave as autosave_module

    def failing_save(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(autosave_module, "save_chat_atomically_row", failing_save)

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    assert notifications.visible and notifications.msg_type == "error"
    assert "disk full" in notifications.message
    assert get_all_chats(db_path) == []


def test_autosave_tick_shows_an_error_notification_when_the_existing_row_lookup_fails(db_path, monkeypatch):
    # Adversarial review finding: this lookup was previously unwrapped -
    # an uncaught exception here used to escape autosave_tick entirely and
    # would have killed register_autosave's own background _loop task,
    # silently and permanently disabling autosave for the rest of the
    # session (no error ever surfaced). This proves it now degrades the
    # same way the DB-write failure path already does: one failed tick,
    # loud notification, task keeps running for the next interval.
    bus, document, notifications = _bus(db_path)
    document.current_chat_id = 999
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    last_saved = _new_save_state()

    import backend.autosave as autosave_module

    def failing_load(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(autosave_module, "load_chat_row", failing_load)

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    assert notifications.visible and notifications.msg_type == "error"
    assert "database is locked" in notifications.message
    assert get_all_chats(db_path) == []
    assert last_saved["digest"] is None


# -- register_autosave: the real timer loop + the mutation_guard interplay --


def test_register_autosave_ticks_on_a_real_timer_and_writes_a_row(tmp_path):
    db_path = tmp_path / "chats.db"
    bus, document, notifications = _bus(db_path)
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    mutation_guard = {"active": False}

    async def _run():
        register_autosave(
            bus, db_path, document, notifications, mutation_guard, _new_save_state(), interval_seconds=0.05
        )
        try:
            await asyncio.sleep(0.2)
        finally:
            bus.autosave_task.cancel()
            try:
                await bus.autosave_task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())
    assert len(get_all_chats(db_path)) == 1


def test_register_autosave_skips_a_tick_while_mutation_guard_is_active(tmp_path):
    db_path = tmp_path / "chats.db"
    bus, document, notifications = _bus(db_path)
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    mutation_guard = {"active": True}  # simulates a manual load/save/new-chat in flight

    async def _run():
        register_autosave(
            bus, db_path, document, notifications, mutation_guard, _new_save_state(), interval_seconds=0.05
        )
        try:
            await asyncio.sleep(0.2)
        finally:
            bus.autosave_task.cancel()
            try:
                await bus.autosave_task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())
    assert get_all_chats(db_path) == [], "a tick must skip itself entirely while a manual operation is in flight"


# -- audit fixes: the change-guard must not mask a row that vanished, and the
# -- error paths must actually reach a connected client, not just local state --


def test_autosave_tick_writes_again_when_its_row_was_deleted_despite_unchanged_content(db_path):
    # Audit finding (real bug): the guard compared CONTENT only. Deleting the
    # currently-open chat leaves the document's digest completely unchanged,
    # so every subsequent tick short-circuited and autosave silently stopped
    # protecting the session - the recovery path below (existing_row is None
    # -> fresh INSERT) was unreachable. Comparing chat_id too fixes it.
    bus, document, notifications = _bus(db_path)
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    last_saved = _new_save_state()
    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))
    first_id = document.current_chat_id
    assert last_saved["chat_id"] == first_id

    # The row goes away and the canvas is NOT touched - exactly the state the
    # content-only guard could not distinguish from "already saved".
    delete_chat(db_path, first_id)
    document.current_chat_id = None  # what deleteChat's intent now does

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    rows = get_all_chats(db_path)
    assert len(rows) == 1, "the work must be re-protected under a fresh row"
    assert rows[0]["id"] != first_id
    assert document.current_chat_id == rows[0]["id"]


def test_a_failed_autosave_actually_broadcasts_its_error_to_a_connected_client(db_path, monkeypatch):
    # Audit finding: both error-path tests asserted on the NotificationState
    # object the test itself constructed, never on what was broadcast -
    # verified by mutation that all three `await bus.publish(...)` calls in
    # backend/autosave.py could be replaced with `pass` and the whole file
    # stayed green. NotificationState.show() only mutates in-memory state; a
    # connected SPA learns about it solely through the publish.
    bus, document, notifications = _bus(db_path)
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    last_saved = _new_save_state()

    recorder = Recorder()
    bus.attach(recorder)

    import backend.autosave as autosave_module

    def failing_save(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(autosave_module, "save_chat_atomically_row", failing_save)

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    broadcast = [
        m["payload"]["message"] for m in recorder.messages if m.get("topic") == "notification"
    ]
    assert any("disk full" in message for message in broadcast), recorder.messages


def test_a_successful_autosave_broadcasts_the_library_refresh(db_path):
    # Same gap, success side: without the app-chat-library publish an open
    # Chat Library dialog never learns the new row exists.
    bus, document, notifications = _bus(db_path)
    document.add_chat_node(0, 0, "hello autosave", is_user=True)
    last_saved = _new_save_state()

    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

    topics = [m.get("topic") for m in recorder.messages]
    assert "app-chat-library" in topics, recorder.messages
