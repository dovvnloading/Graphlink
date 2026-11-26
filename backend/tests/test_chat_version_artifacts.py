"""Reply versions own their generated Code and Thinking cards (#1100)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import api_provider
import graphlink_task_config as task_config
from backend.canvas import SceneDocument
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.tests.conftest import chat_slots
from backend.tests.test_canvas import make_bus_with_dispatcher

FIRST = "<think>plan A</think>Prose A\n\n```python\ndef f():\n    return 1\n```"
SECOND = "Prose B\n\n```python\ndef f():\n    return 2\n```"
PROSE_ONLY = "Just prose this time."


async def _reply(bus, dispatcher, intent, args, text):
    def fake_chat(task, messages, **kwargs):
        return {"message": {"content": text}}

    with patch.object(api_provider, "USE_API_MODE", False), \
            patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
            patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
            patch.object(api_provider, "chat", fake_chat):
        result = await bus.dispatch_intent("scene", intent, args)
        await next(iter(chat_slots(dispatcher).values()))["task"]
    return result


def _cards(document):
    return sorted(
        ("code", n.state.code) if n.kind == "code" else ("thinking", n.content)
        for n in document.nodes.values() if n.kind in ("code", "thinking"))


async def _two_versions(second=SECOND):
    bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
    user_id = await _reply(bus, dispatcher, "sendMessage", ["write f"], FIRST)
    reply = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)
    await _reply(bus, dispatcher, "regenerateResponse", [reply.id], second)
    return bus, document, reply


A_CARDS = [("code", "def f():\n    return 1"), ("thinking", "plan A")]
B_CARDS = [("code", "def f():\n    return 2")]


def test_switching_versions_restores_each_versions_own_cards():
    async def run():
        bus, document, reply = await _two_versions()
        assert _cards(document) == B_CARDS
        await bus.dispatch_intent("scene", "setChatVersion", [reply.id, 0])
        assert reply.content == "Prose A" and _cards(document) == A_CARDS
        await bus.dispatch_intent("scene", "setChatVersion", [reply.id, 1])
        assert reply.content == "Prose B" and _cards(document) == B_CARDS
        await bus.dispatch_intent("scene", "setChatVersion", [reply.id, 0])
        document.undo()
        assert _cards(document) == B_CARDS
        document.redo()
        assert _cards(document) == A_CARDS
    asyncio.run(run())


def test_code_replaced_by_a_prose_only_answer_is_recoverable():
    async def run():
        bus, document, reply = await _two_versions(PROSE_ONLY)
        assert _cards(document) == []
        await bus.dispatch_intent("scene", "setChatVersion", [reply.id, 0])
        assert _cards(document) == A_CARDS
    asyncio.run(run())


def test_edited_and_independent_cards_are_left_alone():
    async def run():
        bus, document, reply = await _two_versions()
        edited = next(n for n in document.nodes.values() if n.kind == "code")
        edited.state.code = "def f():\n    return 'edited'"
        other_parent = document.add_chat_node(0, 0, "elsewhere", True)
        independent = document.add_code_node(0, 0, "independent()", "python", parent_id=other_parent.id)
        link = document.connect(reply.id, independent.id)
        link.role = "reference"
        await bus.dispatch_intent("scene", "setChatVersion", [reply.id, 0])
        assert edited.id in document.nodes and independent.id in document.nodes
        assert ("code", "def f():\n    return 1") in _cards(document)
    asyncio.run(run())


def test_versions_and_their_cards_survive_save_and_reload():
    async def run():
        _bus, document, _reply_node = await _two_versions()
        raw = build_chat_data(document)
        reopened = SceneDocument()
        restore_chat_into_document(reopened, {"data": raw}, raw.get("notes_data", []), [])
        reply = next(n for n in reopened.nodes.values() if n.kind == "chat" and not n.state.is_user)
        assert _cards(reopened) == B_CARDS
        reopened.set_chat_version(reply.id, 0)
        assert _cards(reopened) == A_CARDS
        reopened.set_chat_version(reply.id, 1)
        assert _cards(reopened) == B_CARDS
    asyncio.run(run())


def test_unrecorded_versions_leave_the_canvas_untouched():
    document = SceneDocument()
    user = document.add_chat_node(0, 0, "q", True)
    reply = document.add_chat_node(0, 0, "old answer", False, parent_id=user.id)
    card = document.add_code_node(0, 0, "legacy()", "python", parent_id=reply.id)
    document.update_chat_node_content(reply.id, "new answer")
    document.set_chat_version(reply.id, 0)
    assert card.id in document.nodes
