"""Manual text transforms through the canvas intent registry and undo stack."""

import asyncio

import pytest

from backend.events import IntentValidationError
from backend.tests.conftest import client_row
from backend.tests.test_canvas import make_bus


def test_text_input_lifecycle_publishes_each_edit_and_preserves_the_source():
    async def run():
        bus, document, recorder = make_bus()

        async def send(intent, *args):
            return await bus.dispatch_intent("scene", intent, list(args))

        source = await send("addNote", 0, 0)
        target = await send("addNote", 500, 0)

        def published_status():
            row = next(row for row in document.published_scene_payload()["nodes"] if row["id"] == target)
            return client_row(row)["textInputStatus"]

        assert published_status() == "unbound"
        await send("setNoteContent", source, "first version")
        await send("setNoteContent", target, "my draft")
        recorder.messages.clear()

        await send("bindNoteTextInput", source, target)
        assert document.nodes[target].state.text_input_source_id == source
        assert published_status() == "unrun"
        assert document.nodes[target].content == "my draft"
        latest = "  mañana\n蓝色\twater  "
        await send("setNoteContent", source, latest)
        assert document.nodes[target].content == "my draft"
        await send("runNoteTextInput", target)
        assert published_status() == "current"
        assert document.nodes[target].content == latest
        assert document.nodes[source].content == latest
        assert recorder.topics_seen() == ["scene"] * 3

        await send("undo")
        assert document.nodes[target].content == "my draft"
        assert document.nodes[target].state.text_input_source_id == source
        assert published_status() == "unrun"
        await send("redo")
        assert document.nodes[target].content == latest
        assert published_status() == "current"
        await send("bindNoteTextInput", source, target, "copy")
        assert published_status() == "current"

        await send("bindNoteTextInput", source, target, "word_count")
        assert published_status() == "unrun"
        assert document.nodes[target].content == latest
        await send("setNoteContent", target, "3")
        await send("runNoteTextInput", target)
        assert document.nodes[target].content == "3"
        assert published_status() == "current"
        revised = "different three words"
        await send("setNoteContent", source, revised)
        assert published_status() == "stale"
        await send("bindNoteTextInput", source, target, "word_count")
        assert published_status() == "stale"
        await send("runNoteTextInput", target)
        assert published_status() == "current"
        await send("undo")
        assert document.nodes[target].content == "3"
        assert published_status() == "stale"
        await send("redo")
        await send("unbindNoteTextInput", target)
        assert not document.nodes[target].state.text_input_source_id
        assert published_status() == "unbound"
        assert document.nodes[target].content == "3"
        assert document.nodes[source].content == revised
        await send("undo")
        assert document.nodes[target].state.text_input_source_id == source
        assert document.nodes[target].state.text_input_operation == "word_count"
        assert published_status() == "current"

    asyncio.run(run())


def test_rejected_intents_return_actionable_errors_without_history_or_publish():
    async def run():
        bus, document, recorder = make_bus()
        source = await bus.dispatch_intent("scene", "addNote", [0, 0])
        target = await bus.dispatch_intent("scene", "addNote", [500, 0])
        history_length = len(document.command_log)
        recorder.messages.clear()
        for intent, args, expected in [
            ("bindNoteTextInput", [source, {}], "targetId"),
            ("bindNoteTextInput", [source, target, "unknown"], "operation"),
            ("runNoteTextInput", [target], "(?i)(input|source|bind)"),
        ]:
            with pytest.raises(IntentValidationError, match=expected):
                await bus.dispatch_intent("scene", intent, args)
        assert not document.nodes[target].state.text_input_source_id
        assert len(document.command_log) == history_length
        assert recorder.messages == []

    asyncio.run(run())
