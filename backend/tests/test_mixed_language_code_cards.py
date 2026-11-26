"""Each fenced block becomes its own correctly typed Code card (#1102)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import api_provider
import graphlink_task_config as task_config
from backend.tests.conftest import chat_slots
from backend.tests.test_canvas import make_bus_with_dispatcher

MIXED = "Server and client:\n\n```python\nprint('server')\n```\n\n```javascript\nconst greeting = 'client';\n```"
LATER = "Now in Go:\n\n```go\nfmt.Println(\"go\")\n```"


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
    return sorted((n.state.language, n.state.code) for n in document.nodes.values() if n.kind == "code")


def test_send_and_regenerate_create_one_card_per_fence():
    async def run():
        bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
        user_id = await _reply(bus, dispatcher, "sendMessage", ["write both"], MIXED)
        assert _cards(document) == [("javascript", "const greeting = 'client';"), ("python", "print('server')")]
        reply = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)
        await _reply(bus, dispatcher, "regenerateResponse", [reply.id], LATER)
        assert _cards(document) == [("go", 'fmt.Println("go")')]
        document.undo()
        assert _cards(document) == [("javascript", "const greeting = 'client';"), ("python", "print('server')")]
    asyncio.run(run())
