"""Code moved into Code cards stays in model-visible chat history (#1099)."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import patch

import api_provider
import graphlink_task_config as task_config
from backend.canvas import SceneDocument
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.tests.conftest import chat_slots
from backend.tests.test_canvas import make_bus_with_dispatcher

CODE_ONLY = "```python\ndef answer():\n    return 41\n```"
MIXED = "Here it is:\n\n```python\ndef answer():\n    return 41\n```\n\nCall it once."


@contextlib.contextmanager
def _provider(reply, seen):
    def fake_chat(task, messages, **kwargs):
        seen.append([dict(m) for m in messages])
        return {"message": {"content": reply}}

    with patch.object(api_provider, "USE_API_MODE", False), \
            patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
            patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
            patch.object(api_provider, "chat", fake_chat):
        yield


async def _send(bus, dispatcher, text, reply, seen):
    with _provider(reply, seen):
        await bus.dispatch_intent("scene", "sendMessage", [text])
        await next(iter(chat_slots(dispatcher).values()))["task"]


def _assistant_turns(messages):
    return [m["content"] for m in messages if m.get("role") == "assistant"]


def _reply(document):
    return next(n for n in document.nodes.values() if n.kind == "chat" and not n.state.is_user)


def test_follow_up_requests_carry_code_only_and_mixed_replies_exactly_once():
    for first_reply, expected in ((CODE_ONLY, CODE_ONLY), (MIXED, MIXED)):
        async def run():
            bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
            seen = []
            await _send(bus, dispatcher, "write a function", first_reply, seen)
            reply = _reply(document)
            assert any(n.kind == "code" for n in document.nodes.values())
            await _send(bus, dispatcher, "make it return twice the value", "Done.", seen)
            turns = _assistant_turns(seen[-1])
            assert turns == [expected]
            assert turns[0].count("def answer") == 1
            assert reply.content != expected  # the bubble still shows its card view
        asyncio.run(run())


def test_reasoning_is_not_replayed():
    async def run():
        bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
        seen = []
        await _send(bus, dispatcher, "write", "<think>private plan</think>" + CODE_ONLY, seen)
        history = document.chat_branch_history(_reply(document).id)
        assert history[-1]["content"] == CODE_ONLY
    asyncio.run(run())


def test_regenerate_and_version_switch_replay_the_selected_answer():
    async def run():
        bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
        seen = []
        await _send(bus, dispatcher, "write", CODE_ONLY, seen)
        reply = _reply(document)
        second = "```javascript\nconst answer = () => 42;\n```"
        with _provider(second, seen):
            await bus.dispatch_intent("scene", "regenerateResponse", [reply.id])
            await next(iter(chat_slots(dispatcher).values()))["task"]
        assert document.chat_branch_history(reply.id)[-1]["content"] == second
        document.set_chat_version(reply.id, 0)
        assert document.chat_branch_history(reply.id)[-1]["content"] == CODE_ONLY
        document.set_chat_version(reply.id, 1)
        assert document.chat_branch_history(reply.id)[-1]["content"] == second
    asyncio.run(run())


def test_undo_redo_and_save_reload_keep_the_model_visible_answer():
    async def run():
        bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
        seen = []
        await _send(bus, dispatcher, "write", MIXED, seen)
        reply = _reply(document)
        document.undo()
        document.redo()
        assert document.chat_branch_history(reply.id)[-1]["content"] == MIXED
        raw = build_chat_data(document)
        reopened = SceneDocument()
        restore_chat_into_document(reopened, {"data": raw}, raw.get("notes_data", []), [])
        restored = _reply(reopened)
        assert reopened.chat_branch_history(restored.id)[-1]["content"] == MIXED
    asyncio.run(run())


def test_plain_replies_store_nothing_extra():
    async def run():
        bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
        await _send(bus, dispatcher, "hello", "Just prose.", [])
        assert _reply(document).state.model_content is None
    asyncio.run(run())
