"""Conversation Node admission, stale-response and history integrity.

Network-free tests for real dispatch admission gates and API callbacks.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import api_provider
from backend.canvas import register_canvas
from backend.composer import ComposerDocument
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.tests.test_canvas import make_bus_with_dispatcher


def _manual_canvas():
    class CapturingDispatcher:
        def __init__(self):
            self.calls: list[dict] = []

        async def start_conversation_reply(self, **kwargs):
            self.calls.append(kwargs)
            kwargs["on_claim"]()
            kwargs["node"].pending_request_id = "captured-run-id"

    bus = SessionBus("conversation-admission-tests")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    composer = ComposerDocument()
    bus.register_topic("app-composer", composer.payload)
    dispatcher = CapturingDispatcher()
    document = register_canvas(bus, notifications, dispatcher, composer)
    node = document.add_conversation_node(0, 0, None)
    return bus, document, node, dispatcher


def test_busy_composer_refusal_does_not_append_a_phantom_conversation_message():
    async def run():
        bus, doc, _, dispatcher = make_bus_with_dispatcher()
        node = doc.add_conversation_node(0, 0, None)
        handle = dispatcher._runs.claim("chat")
        try:
            result = await bus.dispatch_intent("scene", "sendConversationMessage", [node.id, "unsent"])
            assert result is None
            assert node.history == []
        finally:
            dispatcher._runs.release(handle.request_id)

    asyncio.run(run())


def test_unconfigured_provider_refusal_keeps_history_unchanged():
    async def run():
        bus, doc, _, dispatcher = make_bus_with_dispatcher()
        node = doc.add_conversation_node(0, 0, None)
        with patch.object(api_provider, "is_configured", return_value=False):
            result = await bus.dispatch_intent("scene", "sendConversationMessage", [node.id, "draft"])
        assert result is None
        assert node.history == []
        assert not dispatcher._runs.is_busy("chat")

    asyncio.run(run())


def test_admitted_send_mutates_once_and_forwards_branch_context():
    async def run():
        bus, doc, node, dispatcher = _manual_canvas()
        result = await bus.dispatch_intent("scene", "sendConversationMessage", [node.id, "real question"])
        assert result == node.id
        assert node.history == [{"role": "user", "content": "real question"}]
        assert len(dispatcher.calls) == 1
        call = dispatcher.calls[0]
        assert call["canvas_document"] is doc and call["node_id"] == node.id
        assert call["conversation_history"] is node.history
        # The committed snapshot returned to _dispatch is a separate list.
        snapshot = call["on_claim"]()
        assert snapshot == node.history
        assert snapshot is not node.history

    asyncio.run(run())


def test_history_deletion_during_generation_discards_late_reply():
    async def run():
        bus, doc, node, dispatcher = _manual_canvas()
        await bus.dispatch_intent("scene", "sendConversationMessage", [node.id, "first"])
        doc.delete_conversation_message(node.id, 0)
        dispatcher.calls[0]["on_reply"]("would be attached to the wrong question")
        dispatcher.calls[0]["on_partial"]("truncated output")
        assert node.history == []

    asyncio.run(run())


def test_undoing_the_sent_message_prevents_late_reply_on_a_restored_node():
    async def run():
        bus, doc, node, dispatcher = _manual_canvas()
        await bus.dispatch_intent("scene", "sendConversationMessage", [node.id, "question"])
        doc.undo()
        restored = doc.nodes[node.id]
        assert restored is not node
        dispatcher.calls[0]["on_reply"]("stale response")
        assert restored.history == []

    asyncio.run(run())


def test_cleared_run_id_disallows_late_model_reply_or_partials():
    async def run():
        bus, doc, node, dispatcher = _manual_canvas()
        await bus.dispatch_intent("scene", "sendConversationMessage", [node.id, "question"])
        node.pending_request_id = None
        dispatcher.calls[0]["on_reply"]("late full reply")
        dispatcher.calls[0]["on_partial"]("late incomplete reply")
        assert node.history == [{"role": "user", "content": "question"}]

    asyncio.run(run())


def test_conversation_input_rejects_blank_wrong_kind_and_huge_text():
    doc = SceneDocument()
    node = doc.add_conversation_node(0, 0, None)
    other = doc.add_node(0, 0)
    for value in ("", "   ", None, 42):
        with pytest.raises(SceneError, match="nonempty text"):
            doc.send_conversation_message(node.id, value)
    with pytest.raises(SceneError, match="limited to 16000"):
        doc.send_conversation_message(node.id, "x" * 16001)
    with pytest.raises(SceneError, match="not a conversation node"):
        doc.send_conversation_message(other.id, "not this kind")
    assert node.history == []


def test_history_deletion_indices_must_be_exact_nonnegative_integers():
    doc = SceneDocument()
    node = doc.add_conversation_node(0, 0, None)
    doc.append_conversation_user_message(node.id, "hello")
    for index in (-1, 1, True, False, "0", None, 0.0):
        with pytest.raises(SceneError, match="valid nonnegative integer"):
            doc.delete_conversation_message(node.id, index)
    assert node.history == [{"role": "user", "content": "hello"}]
