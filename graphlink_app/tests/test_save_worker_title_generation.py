"""Tests for SaveWorkerThread's title generation on new-chat saves.

Regression coverage for dead TitleGenerator wiring (see
doc/ARCHITECTURE_REVIEW_FINDINGS.md #53): SaveWorkerThread received a title_generator
constructor argument but never called it, always falling back to the first five words
of the first message. TitleGenerator.generate_title() itself was fully implemented
and already documented in GRAPHLINK_REPO_NAVIGATION.md's "Title generation flow" but had
zero callers anywhere in the app. SaveWorkerThread.run() now calls it for new chats
(current_chat_id is falsy, or the referenced chat_id no longer exists) and only drops
to the plain fallback title if title generation is unavailable, raises, or returns
nothing usable.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_session.workers import SaveWorkerThread


def _make_db(load_chat_return=None):
    db = MagicMock()
    db.save_chat_atomically.return_value = 42
    db.load_chat.return_value = load_chat_return
    return db


class TestNewChatUsesGeneratedTitle:
    def test_generated_title_is_used_when_available(self):
        db = _make_db()
        title_generator = MagicMock()
        title_generator.generate_title.return_value = "Weekend Trip Plans"
        worker = SaveWorkerThread(db, title_generator, {"nodes": []}, None, "Let's plan a weekend trip")

        worker.run()

        title_generator.generate_title.assert_called_once_with("Let's plan a weekend trip")
        db.save_chat_atomically.assert_called_once_with(None, "Weekend Trip Plans", {"nodes": []}, [], [])

    def test_falls_back_when_generate_title_raises(self):
        db = _make_db()
        title_generator = MagicMock()
        title_generator.generate_title.side_effect = RuntimeError("model unreachable")
        worker = SaveWorkerThread(db, title_generator, {"nodes": []}, None, "Hello there friend")

        worker.run()

        db.save_chat_atomically.assert_called_once()
        used_title = db.save_chat_atomically.call_args[0][1]
        assert used_title == "Hello there friend"

    def test_falls_back_when_generate_title_returns_empty(self):
        db = _make_db()
        title_generator = MagicMock()
        title_generator.generate_title.return_value = "   "
        worker = SaveWorkerThread(db, title_generator, {"nodes": []}, None, "Hello there friend")

        worker.run()

        used_title = db.save_chat_atomically.call_args[0][1]
        assert used_title == "Hello there friend"

    def test_falls_back_when_title_generator_has_no_generate_title(self):
        db = _make_db()
        worker = SaveWorkerThread(db, object(), {"nodes": []}, None, "Hello there friend")

        worker.run()

        used_title = db.save_chat_atomically.call_args[0][1]
        assert used_title == "Hello there friend"

    def test_generated_title_is_also_used_when_the_referenced_chat_id_no_longer_exists(self):
        db = _make_db(load_chat_return=None)
        title_generator = MagicMock()
        title_generator.generate_title.return_value = "Recovered Chat"
        worker = SaveWorkerThread(db, title_generator, {"nodes": []}, 7, "some first message")

        worker.run()

        title_generator.generate_title.assert_called_once_with("some first message")
        # chat_id_for_save is None here (not 7) - the referenced id no longer resolves,
        # so this is an insert of a *new* row, same as the current_chat_id=None case.
        db.save_chat_atomically.assert_called_once_with(None, "Recovered Chat", {"nodes": []}, [], [])


class TestExistingChatDoesNotRegenerateTitle:
    def test_existing_chat_keeps_its_stored_title_and_never_calls_generate_title(self):
        db = _make_db(load_chat_return={"title": "Existing Title", "data": {}})
        title_generator = MagicMock()
        worker = SaveWorkerThread(db, title_generator, {"nodes": []}, 7, "some first message")

        worker.run()

        title_generator.generate_title.assert_not_called()
        db.save_chat_atomically.assert_called_once_with(7, "Existing Title", {"nodes": []}, [], [])


class TestFallbackTitleIsUnicodeAware:
    """Regression coverage for doc/ARCHITECTURE_REVIEW_FINDINGS.md #73: the fallback
    title regex used to be r"[A-Za-z0-9']+", which strips non-ASCII text entirely -
    every non-English first message (CJK, Cyrillic, accented Latin, ...) fell through
    to a bare timestamp title instead of using any of the actual message content."""

    def _fallback_title_for(self, message):
        worker = SaveWorkerThread(_make_db(), object(), {"nodes": []}, None, message)
        return worker._fallback_title()

    def test_cjk_message_produces_a_content_based_title_not_a_timestamp(self):
        message = "你好，今天天气怎么样"  # "Hello, how's the weather today"
        title = self._fallback_title_for(message)
        assert not title.startswith("Chat ")
        assert "你好" in title  # first two CJK characters ("Hello") survive

    def test_cyrillic_message_produces_a_content_based_title_not_a_timestamp(self):
        message = "Привет как дела"  # "Hi how are things"
        title = self._fallback_title_for(message)
        assert not title.startswith("Chat ")
        assert "Привет" in title

    def test_contractions_stay_joined(self):
        message = "don't stop believing"
        title = self._fallback_title_for(message)
        assert title == "don't stop believing"

    def test_empty_message_still_falls_back_to_a_timestamp_title(self):
        title = self._fallback_title_for("   ")
        assert title.startswith("Chat ")
