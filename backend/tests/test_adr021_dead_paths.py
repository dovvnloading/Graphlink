"""ADR-021 stage 21.5: the built-but-dead paths, now reachable.

Three capabilities shipped complete and tested and then sat unreachable
from the running app - no caller, no UI, no affordance. These cover the
wiring that finally reaches them.
"""

from __future__ import annotations

import asyncio

from backend.attachments import StagedAttachment
from backend.domain.model import SceneError
from backend.tests.test_canvas import _bus_with_composer_document, make_bus_with_dispatcher


def _staged_document(name="notes.pdf", text="the extracted body", path="/tmp/notes.pdf"):
    return StagedAttachment(
        kind="document",
        name=name,
        path=path,
        byte_size=1234,
        context_label="Document | PDF",
        mime_type="application/pdf",
        extracted_text=text,
    )


class TestAttachmentsBecomeDocumentNodes:
    """backend/attachments.py has always done real classification and
    extraction, but at Send the extracted text was inlined into the message
    and the file itself discarded - nothing on the canvas recorded it, and
    sceneStore.addDocumentNode had no caller outside its own test."""

    def test_a_document_attachment_lands_as_a_child_document_node(self):
        async def run():
            bus, document, composer_document, _recorder, _dispatcher = _bus_with_composer_document()
            composer_document.staged_attachments.append(_staged_document())

            user_id = await bus.dispatch_intent("scene", "sendMessage", ["look at this"])

            doc_nodes = [n for n in document.nodes.values() if n.kind == "document"]
            assert len(doc_nodes) == 1
            doc = doc_nodes[0]
            assert doc.title == "notes.pdf"
            assert doc.content == "the extracted body"
            assert any(
                e.source == user_id and e.target == doc.id for e in document.edges.values()
            ), "the document hangs off the message it was attached to"

        asyncio.run(run())

    def test_the_message_text_and_reply_context_are_unchanged(self):
        """Additive by construction: the inline text stays exactly as it
        was, so every token count and the reply's own context are
        byte-identical to before this existed."""

        async def run():
            bus, document, composer_document, _recorder, _dispatcher = _bus_with_composer_document()
            composer_document.staged_attachments.append(_staged_document())

            user_id = await bus.dispatch_intent("scene", "sendMessage", ["look at this"])

            user_node = document.nodes[user_id]
            assert "--- Attached: notes.pdf" in user_node.content
            assert "the extracted body" in user_node.content

            # The document node is a CHILD, so the ancestor walk that builds
            # the reply's context never visits it - its text cannot be
            # counted twice.
            history = document.chat_branch_history(user_id)
            assert len(history) == 1
            assert history[0]["content"] == user_node.content

        asyncio.run(run())

    def test_two_document_attachments_fan_out_instead_of_stacking(self):
        async def run():
            bus, document, composer_document, _recorder, _dispatcher = _bus_with_composer_document()
            composer_document.staged_attachments.extend([
                _staged_document(name="a.pdf"),
                _staged_document(name="b.pdf"),
            ])

            await bus.dispatch_intent("scene", "sendMessage", ["two files"])

            docs = sorted(
                (n for n in document.nodes.values() if n.kind == "document"), key=lambda n: n.x,
            )
            assert [d.title for d in docs] == ["a.pdf", "b.pdf"]
            assert docs[0].x != docs[1].x, "siblings must not land on top of each other"

        asyncio.run(run())

    def test_image_attachments_do_not_become_document_nodes(self):
        """An image already renders inside the message as a real content
        part - a second copy on the canvas would be duplication."""

        async def run():
            bus, document, composer_document, _recorder, _dispatcher = _bus_with_composer_document()
            composer_document.staged_attachments.append(
                StagedAttachment(
                    kind="image",
                    name="shot.png",
                    content_part={"type": "image_bytes", "data": b"x"},
                )
            )

            await bus.dispatch_intent("scene", "sendMessage", ["see this"])

            assert not [n for n in document.nodes.values() if n.kind == "document"]

        asyncio.run(run())

    def test_a_promoted_document_node_is_undoable(self):
        async def run():
            bus, document, composer_document, _recorder, _dispatcher = _bus_with_composer_document()
            composer_document.staged_attachments.append(_staged_document())

            await bus.dispatch_intent("scene", "sendMessage", ["look"])
            assert [n for n in document.nodes.values() if n.kind == "document"]

            document.undo()  # the document node's own command
            assert not [n for n in document.nodes.values() if n.kind == "document"]

        asyncio.run(run())


class TestWebResearchRetentionOptIn:
    """WebResearchRequest.retain_to_knowledge and the whole retention path
    shipped at ADR-017; no production caller ever set the flag."""

    def test_the_toggle_round_trips_through_the_intent(self):
        async def run():
            bus, document, _recorder, _dispatcher = make_bus_with_dispatcher()
            parent = document.add_chat_node(0, 0, "parent", True)
            node = document.add_web_research_node(0, 200, parent.id)

            assert node.state.research_retain_to_knowledge is False, "off by default"

            await bus.dispatch_intent(
                "scene", "setWebResearchRetainToKnowledge", [node.id, True],
            )
            assert document.nodes[node.id].state.research_retain_to_knowledge is True

            await bus.dispatch_intent(
                "scene", "setWebResearchRetainToKnowledge", [node.id, False],
            )
            assert document.nodes[node.id].state.research_retain_to_knowledge is False

        asyncio.run(run())

    def test_the_flag_reaches_the_dispatched_request(self):
        """The point of the whole stage: a run started from a node with the
        toggle on must carry retain_to_knowledge=True into the dispatcher,
        which is what _retain_documents keys off."""

        async def run():
            bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
            parent = document.add_chat_node(0, 0, "parent", True)
            node = document.add_web_research_node(0, 200, parent.id)
            document.set_web_research_retain_to_knowledge(node.id, True)

            seen = {}

            async def fake_start(**kwargs):
                seen.update(kwargs)

            dispatcher.start_web_research = fake_start
            dispatcher.is_web_research_busy = lambda: False

            await bus.dispatch_intent("scene", "runWebResearch", [node.id, "why is the sky blue"])

            assert seen["retain_to_knowledge"] is True

        asyncio.run(run())

    def test_a_node_without_the_toggle_still_discards(self):
        async def run():
            bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
            parent = document.add_chat_node(0, 0, "parent", True)
            node = document.add_web_research_node(0, 200, parent.id)

            seen = {}

            async def fake_start(**kwargs):
                seen.update(kwargs)

            dispatcher.start_web_research = fake_start
            dispatcher.is_web_research_busy = lambda: False

            await bus.dispatch_intent("scene", "runWebResearch", [node.id, "a question"])

            assert seen["retain_to_knowledge"] is False, (
                "fetch-summarize-discard stays the default for anything that "
                "has not opted in"
            )

        asyncio.run(run())

    def test_the_preference_survives_a_save_reload_cycle(self):
        from backend.session_load import restore_chat_into_document
        from backend.session_save import build_chat_data

        bus, document, _recorder, _dispatcher = make_bus_with_dispatcher()
        parent = document.add_chat_node(0, 0, "parent", True)
        node = document.add_web_research_node(0, 200, parent.id)
        document.set_web_research_retain_to_knowledge(node.id, True)

        data = build_chat_data(document)
        _bus2, restored, _r2, _d2 = make_bus_with_dispatcher()
        # restore_chat_into_document takes the CHAT ROW, whose payload sits
        # under "data" - the same shape chat_library.py loads from the db.
        restore_chat_into_document(restored, {"data": data}, [], [])

        research = [n for n in restored.nodes.values() if n.kind == "web_research"]
        assert len(research) == 1
        assert research[0].state.research_retain_to_knowledge is True, (
            "a node told to remember its sources must still do so after a reload"
        )

    def test_setting_it_on_a_wrong_kind_is_refused(self):
        bus, document, _recorder, _dispatcher = make_bus_with_dispatcher()
        chat = document.add_chat_node(0, 0, "not research", True)

        try:
            document.set_web_research_retain_to_knowledge(chat.id, True)
        except SceneError:
            pass
        else:  # pragma: no cover - the assertion below is the real failure
            raise AssertionError("expected SceneError for a non-web_research node")
