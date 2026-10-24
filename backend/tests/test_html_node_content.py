"""The HTML node's source-edit commit path: SceneDocument.set_html_content
and the scene/setHtmlContent intent that carries it.

Before this path existed the HTML node's source textarea was local React
state only - the backend had add_html_node and nothing that could change the
stored source afterwards, so an edit was lost the moment the node unmounted
(off-viewport culling) and was never saved at all. These pin the domain
setter, the intent, its undo posture (same as setNoteContent's: a recorded
user edit), and the save/load round trip the edit has to survive.
"""

from __future__ import annotations

import asyncio

import pytest

import backend.agents as agents_module  # noqa: F401 - see test_canvas.py's own import-order note
from backend.agents import AgentDispatcher
from backend.canvas import SceneDocument, SceneError, register_canvas
from backend.composer import ComposerDocument
from backend.domain.model import HTML_TITLE_PREVIEW_LENGTH
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.token_counter import register_token_counter


class _Recorder:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data):
        self.messages.append(data)

    def scene_publishes(self) -> int:
        return sum(1 for m in self.messages if m["topic"] == "scene" and m["kind"] in ("state", "patch"))


class _FakeSettingsManager:
    def get_enable_system_prompt(self):
        return True

    def get_recipes(self):
        return []

    def set_recipes(self, recipes):
        pass


def _make_bus():
    bus = SessionBus("html-content-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    composer_document = ComposerDocument()
    bus.register_topic("app-composer", composer_document.payload)
    token_counter = register_token_counter(bus)
    document = register_canvas(
        bus, notifications, AgentDispatcher(_FakeSettingsManager()), composer_document, token_counter,
    )
    recorder = _Recorder()
    bus.attach(recorder)
    return bus, document, recorder


def _html_doc(source: str = "<p>original</p>") -> tuple[SceneDocument, str, str]:
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "hi", True)
    html = doc.add_html_node(0, 0, source, chat.id)
    return doc, chat.id, html.id


def test_set_html_content_replaces_the_stored_source_and_leaves_the_title_alone():
    doc, _chat_id, html_id = _html_doc()
    created_title = doc.nodes[html_id].title
    assert created_title == "<p>original</p>"[:HTML_TITLE_PREVIEW_LENGTH]

    doc.set_html_content(html_id, "<h1>edited</h1>")

    node = doc.nodes[html_id]
    assert node.content == "<h1>edited</h1>"
    # Same posture as set_note_content, update_chat_node_content and the
    # builderSetContent tool: an in-place content mutator never recomputes
    # the title it was created with.
    assert node.title == created_title
    payload_node = next(n for n in doc.scene_payload()["nodes"] if n["id"] == html_id)
    assert payload_node["content"] == "<h1>edited</h1>"


def test_set_html_content_stores_long_and_empty_source_verbatim():
    doc, _chat_id, html_id = _html_doc()
    created_title = doc.nodes[html_id].title
    long_source = "<div>" + "x" * (HTML_TITLE_PREVIEW_LENGTH * 2) + "</div>"

    doc.set_html_content(html_id, long_source)
    assert doc.nodes[html_id].content == long_source
    assert doc.nodes[html_id].title == created_title

    doc.set_html_content(html_id, "")
    assert doc.nodes[html_id].content == ""
    assert doc.nodes[html_id].title == created_title


def test_set_html_content_rejects_a_non_html_node_and_an_unknown_id():
    doc, chat_id, _html_id = _html_doc()

    with pytest.raises(SceneError):
        doc.set_html_content(chat_id, "<p>nope</p>")
    assert doc.nodes[chat_id].content == "hi"

    with pytest.raises(SceneError):
        doc.set_html_content("does-not-exist", "<p>nope</p>")


def test_set_html_content_intent_mutates_publishes_and_is_undoable():
    async def run():
        bus, document, recorder = _make_bus()
        chat_id = await bus.dispatch_intent("scene", "addChatNode", [0, 0, "hi", True])
        html_id = await bus.dispatch_intent("scene", "addHtmlNode", [0, 0, "<p>hi</p>", chat_id])
        recorder.messages.clear()

        await bus.dispatch_intent("scene", "setHtmlContent", [html_id, "<p>edited</p>"])
        assert document.nodes[html_id].content == "<p>edited</p>"
        assert recorder.scene_publishes() == 1

        # A recorded user edit, same as setNoteContent: Ctrl+Z restores the
        # previous source.
        await bus.dispatch_intent("scene", "undo", [])
        assert document.nodes[html_id].content == "<p>hi</p>"

        # A non-html target and an unknown id both fail the dispatch, same
        # as the sibling setHtmlSplitterState intent.
        with pytest.raises(Exception):
            await bus.dispatch_intent("scene", "setHtmlContent", [chat_id, "<p>nope</p>"])
        with pytest.raises(Exception):
            await bus.dispatch_intent("scene", "setHtmlContent", ["does-not-exist", "<p>nope</p>"])
        assert document.nodes[chat_id].content == "hi"

    asyncio.run(run())


def test_edited_html_source_survives_a_save_load_round_trip():
    doc, _chat_id, html_id = _html_doc()
    doc.set_html_content(html_id, "<section>saved edit</section>")

    chat_data = build_chat_data(doc)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    restored = SceneDocument()
    restore_chat_into_document(restored, {"data": chat_data}, notes_data, pins_data)

    html_nodes = [n for n in restored.nodes.values() if n.kind == "html"]
    assert len(html_nodes) == 1
    assert html_nodes[0].content == "<section>saved edit</section>"
