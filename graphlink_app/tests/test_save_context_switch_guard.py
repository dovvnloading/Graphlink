"""Tests for the context-switch guard on background saves (ChatSessionManager).

Regression coverage for the stale-chat_id save race (doc/ARCHITECTURE_REVIEW_FINDINGS.md):
a background save serializes the chat that was active when it STARTED. If the user
switches chats (New Chat, or loading another chat) while that save is in flight, the
save's completion used to unconditionally do `current_chat_id = new_chat_id`, restoring
the PREVIOUS chat as active. The next autosave then serialized the now-visible scene and
UPDATE'd it into the previous chat's row - silently overwriting an unrelated conversation
and never saving the new work under its own id.

The fix: ChatSessionManager tracks a `_context_epoch` that bumps on every switch
(mark_context_switch), records the epoch a save started under (`_saving_epoch`), and
_on_save_finished only adopts the returned id when they still match.

These tests drive _on_save_finished directly (no real QThread) so the race is exercised
deterministically.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import graphlink_session.manager as manager_module
from graphlink_session.manager import ChatSessionManager


class _FakeDB:
    def __init__(self):
        self.saved = []

    def load_chat(self, chat_id):
        return None


@pytest.fixture
def manager(monkeypatch):
    # Avoid touching the real ~/.graphlink/chats.db: swap ChatDatabase for a fake before
    # the manager constructs one. window=None keeps this a pure logic test (no Qt).
    monkeypatch.setattr(manager_module, "ChatDatabase", lambda *a, **k: _FakeDB())
    return ChatSessionManager(window=None)


class TestNoSwitchKeepsExistingBehavior:
    def test_finish_adopts_new_id_when_context_unchanged(self, manager):
        manager.current_chat_id = 7
        manager._saving_epoch = manager._context_epoch  # a save "started"

        manager._on_save_finished(7)

        assert manager.current_chat_id == 7

    def test_finish_adopts_freshly_inserted_id_for_a_new_chat(self, manager):
        manager.current_chat_id = None
        manager._saving_epoch = manager._context_epoch

        manager._on_save_finished(42)  # INSERT returned a new row id

        assert manager.current_chat_id == 42


class TestSwitchInvalidatesInFlightSave:
    def test_new_chat_during_save_is_not_clobbered_by_the_stale_result(self, manager):
        # Save of chat A (id=1) starts...
        manager.current_chat_id = 1
        manager._saving_epoch = manager._context_epoch

        # ...user starts a New Chat while it runs (window calls mark_context_switch).
        manager.mark_context_switch()
        manager.current_chat_id = None

        # ...the stale save of chat A finishes.
        manager._on_save_finished(1)

        # current_chat_id must stay None (the new chat), NOT snap back to chat A.
        assert manager.current_chat_id is None

    def test_loading_another_chat_during_save_is_not_clobbered(self, manager):
        # Save of chat A (id=1) starts...
        manager.current_chat_id = 1
        manager._saving_epoch = manager._context_epoch

        # ...user loads chat B (id=2) while it runs.
        manager.mark_context_switch()
        manager.current_chat_id = 2

        # ...the stale save of chat A finishes and returns A's id.
        manager._on_save_finished(1)

        # The active chat must remain B, so the next autosave targets B's row, not A's.
        assert manager.current_chat_id == 2

    def test_load_chat_bumps_the_epoch(self, manager):
        before = manager._context_epoch
        manager.load_chat(123)  # _FakeDB.load_chat returns None, but the epoch still bumps
        # load_chat bumps only after confirming the row exists; with the fake DB returning
        # None it returns early WITHOUT switching, so the epoch must be unchanged here.
        assert manager._context_epoch == before

    def test_load_chat_with_a_real_row_bumps_the_epoch(self, manager):
        manager.db.load_chat = lambda chat_id: {"id": chat_id, "title": "x", "chat_data": "{}"}
        before = manager._context_epoch

        manager.load_chat(5)

        assert manager._context_epoch == before + 1
        assert manager.current_chat_id == 5  # no window -> direct id set path


class TestQueuedSaveStillRunsAfterSwitch:
    def test_pending_save_is_dispatched_even_when_context_changed(self, manager, monkeypatch):
        manager.current_chat_id = 1
        manager._saving_epoch = manager._context_epoch
        manager._save_pending = True

        manager.mark_context_switch()
        manager.current_chat_id = None

        calls = []
        monkeypatch.setattr(manager, "save_current_chat", lambda: calls.append(True))

        manager._on_save_finished(1)

        assert calls == [True]  # the queued save of the NEW scene still fires
        assert manager.current_chat_id is None  # ...and the stale id was not restored
