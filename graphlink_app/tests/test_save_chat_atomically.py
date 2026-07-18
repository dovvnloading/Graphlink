"""Tests for ChatDatabase.save_chat_atomically().

Regression coverage for non-transactional cross-table saves: save_chat/update_chat,
save_notes, and save_pins each previously opened their own connection and committed
independently. A crash (or any exception) between two of those steps left the chat row,
notes, and pins inconsistent with each other - e.g. a chat saved with notes that were
never written because the process died between the two separate commits.

save_chat_atomically() does all three writes through one connection, so they commit
together on success or (the part that actually matters) roll back together on failure -
proven below by forcing a failure partway through and confirming *nothing* landed, not
just the notes/pins.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_session.database import ChatDatabase


def _make_db(tmp_path):
    return ChatDatabase(tmp_path / "test_chats.db")


def _sample_note(content="A note"):
    return {
        "content": content,
        "position": {"x": 1.0, "y": 2.0},
        "size": {"width": 100.0, "height": 50.0},
        "color": "#333333",
        "header_color": None,
    }


def _sample_pin(title="A pin"):
    return {"title": title, "note": "pin note", "position": {"x": 3.0, "y": 4.0}}


class TestInsertPath:
    def test_creates_a_new_chat_with_notes_and_pins_and_returns_its_id(self, tmp_path):
        db = _make_db(tmp_path)

        chat_id = db.save_chat_atomically(None, "New Chat", {"nodes": []}, [_sample_note()], [_sample_pin()])

        assert chat_id is not None
        assert db.get_chat_title(chat_id) == "New Chat"
        assert len(db.load_notes(chat_id)) == 1
        assert len(db.load_pins(chat_id)) == 1

    def test_works_with_no_notes_or_pins(self, tmp_path):
        db = _make_db(tmp_path)

        chat_id = db.save_chat_atomically(None, "Plain Chat", {"nodes": []}, [], [])

        assert db.get_chat_title(chat_id) == "Plain Chat"
        assert db.load_notes(chat_id) == []
        assert db.load_pins(chat_id) == []


class TestUpdatePath:
    def test_updates_the_existing_chat_and_replaces_notes_and_pins(self, tmp_path):
        db = _make_db(tmp_path)
        chat_id = db.save_chat_atomically(None, "Original", {"nodes": []}, [_sample_note("old note")], [])

        returned_id = db.save_chat_atomically(
            chat_id, "Original", {"nodes": ["updated"]}, [_sample_note("new note")], [_sample_pin()]
        )

        assert returned_id == chat_id
        loaded = db.load_chat(chat_id)
        assert loaded["data"] == {"nodes": ["updated"]}
        notes = db.load_notes(chat_id)
        assert len(notes) == 1
        assert notes[0]["content"] == "new note"
        assert len(db.load_pins(chat_id)) == 1

    def test_does_not_touch_a_different_chats_notes_or_pins(self, tmp_path):
        db = _make_db(tmp_path)
        chat_a = db.save_chat_atomically(None, "Chat A", {"nodes": []}, [_sample_note("A's note")], [])
        chat_b = db.save_chat_atomically(None, "Chat B", {"nodes": []}, [_sample_note("B's note")], [])

        db.save_chat_atomically(chat_a, "Chat A", {"nodes": ["x"]}, [], [])

        assert db.load_notes(chat_a) == []  # replaced with nothing
        assert len(db.load_notes(chat_b)) == 1  # untouched
        assert db.load_notes(chat_b)[0]["content"] == "B's note"


class TestAtomicityOnFailure:
    def test_a_failure_while_writing_notes_rolls_back_the_chat_row_too(self, tmp_path):
        db = _make_db(tmp_path)
        malformed_note = {"content": "missing required keys"}  # no "position"/"size"/"color"

        with pytest.raises(KeyError):
            db.save_chat_atomically(None, "Should Not Persist", {"nodes": []}, [malformed_note], [])

        # Nothing committed - not the chat row, not any notes - because the failure
        # happened inside the same transaction as the chat insert.
        with sqlite3.connect(db.db_path) as conn:
            chat_count = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
            notes_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        assert chat_count == 0
        assert notes_count == 0

    def test_a_failure_while_writing_pins_rolls_back_the_chat_row_and_notes_too(self, tmp_path):
        db = _make_db(tmp_path)
        malformed_pin = {"title": "missing position"}  # no "note"/"position"

        with pytest.raises(KeyError):
            db.save_chat_atomically(None, "Should Not Persist", {"nodes": []}, [_sample_note()], [malformed_pin])

        with sqlite3.connect(db.db_path) as conn:
            chat_count = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
            notes_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        assert chat_count == 0
        assert notes_count == 0  # the earlier, successful note write was rolled back too

    def test_a_failure_on_update_leaves_the_original_row_and_notes_intact(self, tmp_path):
        db = _make_db(tmp_path)
        chat_id = db.save_chat_atomically(None, "Original Title", {"nodes": []}, [_sample_note("original note")], [])
        malformed_note = {"content": "missing required keys"}

        with pytest.raises(KeyError):
            db.save_chat_atomically(chat_id, "New Title", {"nodes": ["new"]}, [malformed_note], [])

        # The UPDATE and the notes DELETE both happened earlier in the same failed
        # transaction, so both must be rolled back - the original data survives.
        loaded = db.load_chat(chat_id)
        assert loaded["title"] == "Original Title"
        assert loaded["data"] == {"nodes": []}
        notes = db.load_notes(chat_id)
        assert len(notes) == 1
        assert notes[0]["content"] == "original note"


class TestSaveNotesAndSavePinsStillWorkStandalone:
    """save_notes/save_pins were refactored to share _write_notes/_write_pins with
    save_chat_atomically - confirm their own public behavior is unchanged."""

    def test_save_notes_still_works_on_its_own(self, tmp_path):
        db = _make_db(tmp_path)
        chat_id = db.save_chat(  # save_chat/update_chat/save_notes/save_pins remain
            "A Chat", {"nodes": []}  # available standalone, unchanged, for any other caller
        )

        db.save_notes(chat_id, [_sample_note()])

        assert len(db.load_notes(chat_id)) == 1

    def test_save_pins_still_works_on_its_own(self, tmp_path):
        db = _make_db(tmp_path)
        chat_id = db.save_chat("A Chat", {"nodes": []})

        db.save_pins(chat_id, [_sample_pin()])

        assert len(db.load_pins(chat_id)) == 1
