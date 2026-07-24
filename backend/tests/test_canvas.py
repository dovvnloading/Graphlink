"""Canvas domain tests (Qt-removal plan R1): scene document invariants,
intent surface, grid payload compatibility with the generated validator's
shape, and snapshot publishing."""

import asyncio
import base64
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Importing any backend.* submodule runs backend/__init__.py first, which
# puts graphlink_app/ on sys.path - these must come before the bare
# top-level graphlink_app imports below (api_provider, graphlink_task_config)
# for this module to import cleanly when run standalone, not just as part of
# a larger session where some earlier-collected module already did this.
import backend.agents as agents_module
from backend.agents import AgentDispatcher
from backend.canvas import (
    DRAG_FACTOR_MAX,
    DRAG_FACTOR_MIN,
    SceneDocument,
    SceneEmptyPromptError,
    SceneError,
    _research_result_wire,
    register_canvas,
)
from backend.composer import ComposerDocument
from backend.events import SessionBus
from backend.notifications import NotificationState

import api_provider
import graphlink_task_config as task_config
from graphlink_plugins.web_research.domain import ResearchCitation, ResearchResult, ResearchSource


# -- document invariants ----------------------------------------------------


def test_add_move_and_remove_nodes():
    doc = SceneDocument()
    a = doc.add_node(10, 20, "A")
    assert doc.nodes[a.id].x == 10
    doc.move_node(a.id, -5.5, 7.25)
    assert (doc.nodes[a.id].x, doc.nodes[a.id].y) == (-5.5, 7.25)
    doc.remove_nodes([a.id])
    assert doc.nodes == {}


def test_move_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().move_node("nope", 0, 0)


def test_connect_validates_and_is_idempotent():
    doc = SceneDocument()
    a, b = doc.add_node(0, 0), doc.add_node(100, 0)
    edge = doc.connect(a.id, b.id)
    assert doc.connect(a.id, b.id).id == edge.id, "duplicate connect returns the same edge"
    with pytest.raises(SceneError):
        doc.connect(a.id, a.id)
    with pytest.raises(SceneError):
        doc.connect(a.id, "ghost")


def test_removing_a_node_removes_its_edges():
    doc = SceneDocument()
    a, b, c = doc.add_node(0, 0), doc.add_node(1, 1), doc.add_node(2, 2)
    doc.connect(a.id, b.id)
    keep = doc.connect(b.id, c.id)
    doc.remove_nodes([a.id])
    assert list(doc.edges) == [keep.id], "edges die with either endpoint"


# -- R3.1: chat nodes --------------------------------------------------------


def test_add_chat_node_creates_a_real_chat_kind_node():
    doc = SceneDocument()
    node = doc.add_chat_node(10, 20, "Hello there, this is a real message", True)
    assert node.kind == "chat"
    assert node.content == "Hello there, this is a real message"
    assert node.is_user is True
    assert node.is_collapsed is False
    assert node.title == "Hello there, this is a real message"[:60]


def test_add_chat_node_falls_back_to_role_title_for_empty_content():
    doc = SceneDocument()
    node = doc.add_chat_node(0, 0, "", False)
    assert node.title == "Assistant"


def test_add_chat_node_connects_to_a_real_parent():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "question", True)
    child = doc.add_chat_node(10, 10, "answer", False, parent_id=parent.id)
    assert any(e.source == parent.id and e.target == child.id for e in doc.edges.values())


def test_add_chat_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_chat_node(0, 0, "orphaned", True, parent_id="ghost")


def test_delete_chat_node_reparents_children_to_the_deleted_nodes_parent():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", True)
    b = doc.add_chat_node(1, 1, "b", False, parent_id=a.id)
    c = doc.add_chat_node(2, 2, "c", True, parent_id=b.id)

    doc.delete_chat_node(b.id)

    assert b.id not in doc.nodes
    assert any(e.source == a.id and e.target == c.id for e in doc.edges.values())
    assert not any(e.target == b.id or e.source == b.id for e in doc.edges.values())


def test_delete_chat_node_at_the_root_makes_children_new_roots():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    child = doc.add_chat_node(1, 1, "child", False, parent_id=root.id)

    doc.delete_chat_node(root.id)

    assert root.id not in doc.nodes
    assert not any(e.target == child.id for e in doc.edges.values()), "child has no parent edge left"


def test_delete_chat_node_unknown_raises():
    with pytest.raises(SceneError):
        SceneDocument().delete_chat_node("ghost")


def test_set_chat_collapsed():
    doc = SceneDocument()
    node = doc.add_chat_node(0, 0, "hi", True)
    doc.set_chat_collapsed(node.id, True)
    assert doc.nodes[node.id].is_collapsed is True
    with pytest.raises(SceneError):
        doc.set_chat_collapsed("ghost", True)


def test_scene_payload_includes_chat_fields_defaulted_for_placeholders():
    doc = SceneDocument()
    doc.add_node(0, 0, "plain")
    doc.add_chat_node(1, 1, "real content", True)
    rows = {n["title"]: n for n in doc.scene_payload()["nodes"]}
    assert rows["plain"]["kind"] == "placeholder"
    assert rows["plain"]["content"] == ""
    assert rows["plain"]["isUser"] is False
    assert rows["plain"]["isCollapsed"] is False
    chat_row = rows["real content"]
    assert chat_row["kind"] == "chat"
    assert chat_row["content"] == "real content"
    assert chat_row["isUser"] is True


# -- R3.5: code nodes --------------------------------------------------------


def test_add_code_node_creates_a_real_code_kind_node():
    doc = SceneDocument()
    node = doc.add_code_node(10, 20, "print('hi')", "python")
    assert node.kind == "code"
    assert node.code == "print('hi')"
    assert node.language == "python"
    assert node.title == "python: print('hi')"


def test_add_code_node_falls_back_to_language_only_title_for_empty_code():
    doc = SceneDocument()
    node = doc.add_code_node(0, 0, "", "python")
    assert node.title == "python"


def test_add_code_node_falls_back_to_code_label_when_language_and_code_are_empty():
    doc = SceneDocument()
    node = doc.add_code_node(0, 0, "", "")
    assert node.title == "code"


def test_add_code_node_connects_to_a_real_parent():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    child = doc.add_code_node(10, 10, "x = 1", "python", parent_id=parent.id)
    assert any(e.source == parent.id and e.target == child.id for e in doc.edges.values())


def test_add_code_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_code_node(0, 0, "x = 1", "python", parent_id="ghost")


def test_scene_payload_includes_code_fields_defaulted_for_other_kinds():
    doc = SceneDocument()
    doc.add_node(0, 0, "plain")
    doc.add_code_node(1, 1, "x = 1", "python")
    rows = {n["title"]: n for n in doc.scene_payload()["nodes"]}
    assert rows["plain"]["kind"] == "placeholder"
    assert rows["plain"]["code"] == ""
    assert rows["plain"]["language"] == ""
    code_row = rows["python: x = 1"]
    assert code_row["kind"] == "code"
    assert code_row["code"] == "x = 1"
    assert code_row["language"] == "python"


def test_code_node_deletion_goes_through_the_generic_remove_nodes_path():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_code_node(10, 10, "x = 1", "python", parent_id=parent.id)
    assert not hasattr(doc, "delete_code_node"), "code nodes are not branch points - no special delete method"
    doc.remove_nodes([node.id])
    assert node.id not in doc.nodes
    assert not any(e.target == node.id or e.source == node.id for e in doc.edges.values())


# -- R3.9: document nodes -----------------------------------------------------


def test_add_document_node_creates_a_real_document_kind_node():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "the message that attached a file", True)
    node = doc.add_document_node(
        10,
        20,
        "report.pdf",
        "some extracted text",
        "document",
        parent.id,
        file_path="C:/files/report.pdf",
        mime_type="application/pdf",
        duration_seconds=None,
        byte_size=2048,
        preview_label="PDF",
    )
    assert node.kind == "document"
    assert node.title == "report.pdf"
    assert node.content == "some extracted text"
    assert node.attachment_kind == "document"
    assert node.file_path == "C:/files/report.pdf"
    assert node.mime_type == "application/pdf"
    assert node.duration_seconds is None
    assert node.byte_size == 2048
    assert node.preview_label == "PDF"
    assert any(e.source == parent.id and e.target == node.id for e in doc.edges.values())


def test_add_document_node_normalizes_attachment_kind_casing():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_document_node(0, 0, "voice.wav", "", "Audio", parent.id)
    assert node.attachment_kind == "audio"


def test_add_document_node_requires_a_parent_id():
    # Unlike chat/code, parent_id has no default in add_document_node's
    # signature - the legacy DocumentNode can never exist unparented - so
    # calling without one is a TypeError (missing required argument), same
    # as any other required positional in this codebase.
    doc = SceneDocument()
    with pytest.raises(TypeError):
        doc.add_document_node(0, 0, "file.txt", "content", "document")


def test_add_document_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_document_node(0, 0, "file.txt", "content", "document", "ghost")


def test_scene_payload_includes_document_fields_defaulted_for_other_kinds():
    doc = SceneDocument()
    doc.add_node(0, 0, "plain")
    parent = doc.add_chat_node(1, 1, "parent message", True)
    doc.add_document_node(
        2,
        2,
        "notes.txt",
        "hello",
        "document",
        parent.id,
        file_path="/tmp/notes.txt",
        mime_type="text/plain",
        byte_size=512,
        preview_label="TXT",
    )
    rows = {n["title"]: n for n in doc.scene_payload()["nodes"]}
    plain_row = rows["plain"]
    assert plain_row["kind"] == "placeholder"
    assert plain_row["attachmentKind"] == ""
    assert plain_row["filePath"] == ""
    assert plain_row["mimeType"] == ""
    assert plain_row["durationSeconds"] is None
    assert plain_row["byteSize"] is None
    assert plain_row["previewLabel"] == ""

    doc_row = rows["notes.txt"]
    assert doc_row["kind"] == "document"
    assert doc_row["attachmentKind"] == "document"
    assert doc_row["filePath"] == "/tmp/notes.txt"
    assert doc_row["mimeType"] == "text/plain"
    assert doc_row["byteSize"] == 512
    assert doc_row["previewLabel"] == "TXT"


def test_add_document_node_intent_creates_a_real_node_and_publishes():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addChatNode", [0, 0, "hi", True])
        # dispatch_intent only ever forwards a positional args list (see
        # SessionBus.dispatch_intent: `handler(*args)`), so every argument -
        # including the wrapper's keyword-defaulted ones - is passed
        # positionally here, in add_document_node's declared order.
        node_id = await bus.dispatch_intent(
            "scene",
            "addDocumentNode",
            [10, 10, "audio.mp3", "", "audio", parent_id, "", "audio/mpeg", 125.4, 4096, ""],
        )
        assert document.nodes[node_id].kind == "document"
        assert document.nodes[node_id].attachment_kind == "audio"
        assert document.nodes[node_id].mime_type == "audio/mpeg"
        assert document.nodes[node_id].duration_seconds == 125.4
        assert document.nodes[node_id].byte_size == 4096
        assert any(
            e.source == parent_id and e.target == node_id for e in document.edges.values()
        )
        assert recorder.topics_seen().count("scene") == 2, "both mutations publish"

    asyncio.run(run())


def test_document_node_deletion_goes_through_the_generic_remove_nodes_path():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_document_node(10, 10, "file.txt", "content", "document", parent.id)
    assert not hasattr(doc, "delete_document_node"), "document nodes are not branch points - no special delete method"
    doc.remove_nodes([node.id])
    assert node.id not in doc.nodes
    assert not any(e.target == node.id or e.source == node.id for e in doc.edges.values())


# -- R3.13: thinking nodes + docking -----------------------------------------


def test_add_thinking_node_creates_a_real_thinking_kind_node():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "the message that triggered reasoning", True)
    node = doc.add_thinking_node(10, 20, "step one, then step two, then a conclusion", parent.id)
    assert node.kind == "thinking"
    assert node.content == "step one, then step two, then a conclusion"
    assert node.title == "step one, then step two, then a conclusion"[:60]
    assert node.is_docked is False
    assert any(e.source == parent.id and e.target == node.id for e in doc.edges.values())


def test_add_thinking_node_falls_back_to_thinking_title_for_empty_text():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_thinking_node(0, 0, "", parent.id)
    assert node.title == "Thinking"


def test_add_thinking_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_thinking_node(0, 0, "orphaned reasoning", "ghost")


def test_add_thinking_node_requires_a_parent_id():
    # Same as add_document_node - parent_id has no default in
    # add_thinking_node's signature, so calling without one is a TypeError
    # (missing required argument), not a SceneError.
    doc = SceneDocument()
    with pytest.raises(TypeError):
        doc.add_thinking_node(0, 0, "orphaned reasoning")


def test_set_node_docked_toggles_true_then_false():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_thinking_node(1, 1, "reasoning", parent.id)

    doc.set_node_docked(node.id, True)
    assert doc.nodes[node.id].is_docked is True
    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]
    assert row["isDocked"] is True

    doc.set_node_docked(node.id, False)
    assert doc.nodes[node.id].is_docked is False
    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]
    assert row["isDocked"] is False


def test_set_node_docked_unknown_node_raises():
    with pytest.raises(SceneError):
        SceneDocument().set_node_docked("ghost", True)


def test_scene_payload_includes_is_docked_defaulted_for_other_kinds():
    doc = SceneDocument()
    doc.add_node(0, 0, "plain")
    doc.add_chat_node(1, 1, "a chat message", True)
    rows = {n["title"]: n for n in doc.scene_payload()["nodes"]}
    assert rows["plain"]["isDocked"] is False
    assert rows["a chat message"]["isDocked"] is False


def test_thinking_node_deletion_goes_through_the_generic_remove_nodes_path():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_thinking_node(10, 10, "reasoning", parent.id)
    assert not hasattr(doc, "delete_thinking_node"), "thinking nodes are not branch points - no special delete method"
    doc.remove_nodes([node.id])
    assert node.id not in doc.nodes
    assert not any(e.target == node.id or e.source == node.id for e in doc.edges.values())


def test_add_thinking_node_intent_creates_a_real_node_and_publishes():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addChatNode", [0, 0, "hi", True])
        node_id = await bus.dispatch_intent(
            "scene", "addThinkingNode", [10, 10, "reasoning text", parent_id]
        )
        assert document.nodes[node_id].kind == "thinking"
        assert document.nodes[node_id].content == "reasoning text"
        assert document.nodes[node_id].is_docked is False
        assert any(
            e.source == parent_id and e.target == node_id for e in document.edges.values()
        )
        assert recorder.topics_seen().count("scene") == 2, "both mutations publish"

    asyncio.run(run())


def test_set_node_docked_intent_flips_is_docked_and_publishes():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addNode", [0, 0, "parent"])
        node_id = await bus.dispatch_intent(
            "scene", "addThinkingNode", [10, 10, "reasoning", parent_id]
        )
        await bus.dispatch_intent("scene", "setNodeDocked", [node_id, True])
        assert document.nodes[node_id].is_docked is True
        await bus.dispatch_intent("scene", "setNodeDocked", [node_id, False])
        assert document.nodes[node_id].is_docked is False
        assert recorder.topics_seen().count("scene") == 4, "every mutation publishes"

    asyncio.run(run())


# -- R3.17: html nodes --------------------------------------------------------


def test_add_html_node_creates_a_real_html_kind_node():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "show me a preview", True)
    node = doc.add_html_node(10, 20, "<h1>Hello</h1><p>world</p>", parent.id)
    assert node.kind == "html"
    assert node.content == "<h1>Hello</h1><p>world</p>"
    assert node.title == "<h1>Hello</h1><p>world</p>"[:60]
    assert any(e.source == parent.id and e.target == node.id for e in doc.edges.values())


def test_add_html_node_stores_script_content_as_an_opaque_string():
    # The backend never parses, sanitizes, or interprets HTML content - it is
    # stored verbatim, exactly like any other opaque text field.
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    raw = "<script>alert(1)</script>"
    node = doc.add_html_node(0, 0, raw, parent.id)
    assert node.content == raw
    assert node.title == raw


def test_add_html_node_falls_back_to_html_title_for_empty_content():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_html_node(0, 0, "", parent.id)
    assert node.title == "HTML"


def test_add_html_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_html_node(0, 0, "<div>orphan</div>", "ghost")


def test_add_html_node_requires_a_parent_id():
    # Same as add_document_node/add_thinking_node - parent_id has no default
    # in add_html_node's signature, so calling without one is a TypeError
    # (missing required argument), not a SceneError.
    doc = SceneDocument()
    with pytest.raises(TypeError):
        doc.add_html_node(0, 0, "<div>orphan</div>")


def test_html_node_scene_payload_needs_no_new_key():
    # The raw HTML source reuses the existing `content` field - scene_payload
    # gets no html-specific key at all.
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    doc.add_html_node(1, 1, "<b>bold</b>", parent.id)
    rows = {n["kind"]: n for n in doc.scene_payload()["nodes"]}
    html_row = rows["html"]
    assert html_row["content"] == "<b>bold</b>"
    assert "html" not in html_row, "no html-specific key - content already carries it"


def test_html_node_deletion_goes_through_the_generic_remove_nodes_path():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_html_node(10, 10, "<p>doomed</p>", parent.id)
    assert not hasattr(doc, "delete_html_node"), "html nodes are not branch points - no special delete method"
    doc.remove_nodes([node.id])
    assert node.id not in doc.nodes
    assert not any(e.target == node.id or e.source == node.id for e in doc.edges.values())


def test_add_html_node_intent_creates_a_real_node_and_publishes():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addChatNode", [0, 0, "hi", True])
        node_id = await bus.dispatch_intent(
            "scene", "addHtmlNode", [10, 10, "<h1>preview</h1>", parent_id]
        )
        assert document.nodes[node_id].kind == "html"
        assert document.nodes[node_id].content == "<h1>preview</h1>"
        assert any(
            e.source == parent_id and e.target == node_id for e in document.edges.values()
        )
        assert recorder.topics_seen().count("scene") == 2, "both mutations publish"

    asyncio.run(run())


# -- R3.21: image nodes -------------------------------------------------------


def test_add_image_node_creates_a_real_image_kind_node():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "generate a picture of a cat", True)
    node = doc.add_image_node(10, 20, b"\x89PNG raw bytes", "a cat wearing a hat", parent.id)
    assert node.kind == "image"
    assert node.content == "a cat wearing a hat"
    assert node.title == "a cat wearing a hat"
    assert node.image_asset_id != ""
    assert any(e.source == parent.id and e.target == node.id for e in doc.edges.values())


def test_add_image_node_falls_back_to_image_title_for_empty_prompt():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_image_node(0, 0, b"bytes", "", parent.id)
    assert node.title == "Image"


def test_add_image_node_stores_the_asset_retrievable_with_correct_bytes_and_mime_type():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_image_node(0, 0, b"raw-png-bytes", "prompt", parent.id, mime_type="image/png")
    asset = doc.get_image_asset(node.image_asset_id)
    assert asset == (b"raw-png-bytes", "image/png")


def test_get_image_asset_returns_none_for_unknown_id():
    doc = SceneDocument()
    assert doc.get_image_asset("ghost") is None


def test_add_image_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_image_node(0, 0, b"bytes", "orphaned image", "ghost")


def test_add_image_node_requires_a_parent_id():
    # Same as add_document_node/add_thinking_node/add_html_node - parent_id
    # has no default in add_image_node's signature, so calling without one is
    # a TypeError (missing required argument), not a SceneError.
    doc = SceneDocument()
    with pytest.raises(TypeError):
        doc.add_image_node(0, 0, b"bytes", "orphaned image")


def test_scene_payload_includes_image_asset_id_defaulted_for_other_kinds():
    doc = SceneDocument()
    doc.add_node(0, 0, "plain")
    parent = doc.add_node(1, 1, "parent")
    image_node = doc.add_image_node(2, 2, b"bytes", "a real prompt", parent.id)
    rows = {n["id"]: n for n in doc.scene_payload()["nodes"]}
    assert rows["n0"]["imageAssetId"] == ""
    assert rows[parent.id]["imageAssetId"] == ""
    assert rows[image_node.id]["imageAssetId"] == image_node.image_asset_id
    assert rows[image_node.id]["imageAssetId"] != ""


def test_image_node_deletion_goes_through_remove_nodes_and_evicts_the_asset():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_image_node(10, 10, b"doomed bytes", "doomed image", parent.id)
    asset_id = node.image_asset_id
    assert not hasattr(doc, "delete_image_node"), "image nodes are not branch points - no special delete method"

    assert doc.get_image_asset(asset_id) is not None
    doc.remove_nodes([node.id])

    assert node.id not in doc.nodes
    assert not any(e.target == node.id or e.source == node.id for e in doc.edges.values())
    assert doc.get_image_asset(asset_id) is None, "deleting the node must evict its asset-store entry too"
    assert doc.image_assets == {}, "no leftover entries linger after the owning node is gone"


def test_non_image_node_deletion_does_not_touch_image_assets():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    image_node = doc.add_image_node(0, 0, b"keep me", "keep this image", parent.id)
    code_node = doc.add_code_node(1, 1, "x = 1", "python", parent_id=parent.id)

    doc.remove_nodes([code_node.id])

    assert code_node.id not in doc.nodes
    assert doc.get_image_asset(image_node.image_asset_id) == (b"keep me", "image/png"), (
        "deleting a node with no image_asset_id must not touch image_assets at all"
    )


def test_add_image_node_intent_creates_a_real_node_and_publishes():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addChatNode", [0, 0, "hi", True])
        image_bytes = b"\x89PNG\r\n\x1a\nfake-but-real-bytes"
        encoded = base64.b64encode(image_bytes).decode("ascii")
        node_id = await bus.dispatch_intent(
            "scene",
            "addImageNode",
            [10, 10, encoded, "a generated image", parent_id, "image/jpeg"],
        )
        assert document.nodes[node_id].kind == "image"
        assert document.nodes[node_id].content == "a generated image"
        asset = document.get_image_asset(document.nodes[node_id].image_asset_id)
        assert asset == (image_bytes, "image/jpeg"), "base64 payload must decode back to the exact original bytes"
        assert any(
            e.source == parent_id and e.target == node_id for e in document.edges.values()
        )
        assert recorder.topics_seen().count("scene") == 2, "both mutations publish"

    asyncio.run(run())


# -- R3.25: conversation nodes -----------------------------------------------


def test_add_conversation_node_creates_a_real_conversation_kind_node():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "let's have a back-and-forth", True)
    node = doc.add_conversation_node(10, 20, parent.id)
    assert node.kind == "conversation"
    assert node.title == "Conversation"
    assert node.history == []
    assert any(e.source == parent.id and e.target == node.id for e in doc.edges.values())


def test_add_conversation_node_title_never_changes_after_messages_are_appended():
    # Unlike every scalar-content kind before it (chat/thinking/html/image all
    # preview their own text), a conversation node's title is a fixed literal
    # - there is no natural single preview string for a growing message list.
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_conversation_node(0, 0, parent.id)
    doc.append_conversation_user_message(node.id, "hello there, a whole essay of text")
    doc.append_conversation_assistant_message(node.id, "a long reply that would have been truncated as a title")
    assert node.title == "Conversation"


def test_add_conversation_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_conversation_node(0, 0, "ghost")


def test_add_conversation_node_requires_a_parent_id():
    # Same as add_document_node/add_thinking_node/add_html_node/
    # add_image_node - parent_id has no default in add_conversation_node's
    # signature, so calling without one is a TypeError (missing required
    # argument), not a SceneError.
    doc = SceneDocument()
    with pytest.raises(TypeError):
        doc.add_conversation_node(0, 0)


def test_append_conversation_user_message_appends_role_and_content():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_conversation_node(0, 0, parent.id)
    returned = doc.append_conversation_user_message(node.id, "hi there")
    assert returned is node
    assert node.history == [{"role": "user", "content": "hi there"}]


def test_append_conversation_assistant_message_appends_role_and_content():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_conversation_node(0, 0, parent.id)
    returned = doc.append_conversation_assistant_message(node.id, "hello, how can I help?")
    assert returned is node
    assert node.history == [{"role": "assistant", "content": "hello, how can I help?"}]


def test_append_conversation_message_unknown_node_raises():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.append_conversation_user_message("ghost", "hi")
    with pytest.raises(SceneError):
        doc.append_conversation_assistant_message("ghost", "hi")


def test_send_conversation_message_is_equivalent_to_append_conversation_user_message():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_conversation_node(0, 0, parent.id)
    returned = doc.send_conversation_message(node.id, "what's up")
    assert returned is node
    assert node.history == [{"role": "user", "content": "what's up"}]


def test_delete_conversation_message_removes_the_correct_index():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_conversation_node(0, 0, parent.id)
    doc.append_conversation_user_message(node.id, "first")
    doc.append_conversation_assistant_message(node.id, "second")
    doc.append_conversation_user_message(node.id, "third")

    doc.delete_conversation_message(node.id, 1)

    assert node.history == [
        {"role": "user", "content": "first"},
        {"role": "user", "content": "third"},
    ]


def test_delete_conversation_message_out_of_range_raises():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_conversation_node(0, 0, parent.id)
    doc.append_conversation_user_message(node.id, "only message")
    with pytest.raises(SceneError):
        doc.delete_conversation_message(node.id, 5)
    with pytest.raises(SceneError):
        doc.delete_conversation_message(node.id, -1)


def test_delete_conversation_message_unknown_node_raises():
    with pytest.raises(SceneError):
        SceneDocument().delete_conversation_message("ghost", 0)


def test_conversation_node_deletion_goes_through_the_generic_remove_nodes_path():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_conversation_node(10, 10, parent.id)
    doc.append_conversation_user_message(node.id, "doomed message")
    assert not hasattr(doc, "delete_conversation_node"), (
        "conversation nodes are not branch points - no special delete method"
    )
    doc.remove_nodes([node.id])
    assert node.id not in doc.nodes
    assert not any(e.target == node.id or e.source == node.id for e in doc.edges.values())


def test_set_chat_collapsed_works_generically_against_a_conversation_node():
    # setChatCollapsed is already fully generic (looks up any node by id
    # regardless of kind) - ConversationNode reuses it with zero backend
    # change, same as document/html already do.
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_conversation_node(0, 0, parent.id)
    doc.set_chat_collapsed(node.id, True)
    assert doc.nodes[node.id].is_collapsed is True


def test_no_bulk_replace_or_cancel_methods_exist_this_increment():
    # Deliberate omissions, documented the same way other kinds' tests
    # document intentional gaps: no set_history (no clone-on-create/session
    # persistence call site yet) and no delete_conversation_node (leaf
    # deletion goes through the generic remove_nodes).
    doc = SceneDocument()
    assert not hasattr(doc, "set_history")
    assert not hasattr(doc, "delete_conversation_node")


def test_scene_payload_includes_history_defaulted_for_other_kinds():
    doc = SceneDocument()
    doc.add_node(0, 0, "plain")
    parent = doc.add_node(1, 1, "parent")
    node = doc.add_conversation_node(2, 2, parent.id)
    doc.append_conversation_user_message(node.id, "hi")
    doc.append_conversation_assistant_message(node.id, "hello!")

    rows = {n["id"]: n for n in doc.scene_payload()["nodes"]}
    assert rows["n0"]["history"] == []
    assert rows[parent.id]["history"] == []
    assert rows[node.id]["history"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello!"},
    ]
    # R4.3: pendingRequestId defaults to None for every kind - including a
    # conversation node with no in-flight dispatch (it is only ever set by
    # AgentDispatcher.start_conversation_reply while a reply is generating -
    # see test_agents.py and test_send_conversation_message_intent_dispatches_a_real_agent_reply
    # below for the non-None in-flight case).
    assert rows["n0"]["pendingRequestId"] is None
    assert rows[parent.id]["pendingRequestId"] is None
    assert rows[node.id]["pendingRequestId"] is None


def test_add_conversation_node_intent_creates_a_real_node_and_publishes():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addChatNode", [0, 0, "hi", True])
        node_id = await bus.dispatch_intent(
            "scene", "addConversationNode", [10, 10, parent_id]
        )
        assert document.nodes[node_id].kind == "conversation"
        assert document.nodes[node_id].title == "Conversation"
        assert document.nodes[node_id].history == []
        assert any(
            e.source == parent_id and e.target == node_id for e in document.edges.values()
        )
        assert recorder.topics_seen().count("scene") == 2, "both mutations publish"

    asyncio.run(run())


def test_send_conversation_message_intent_dispatches_a_real_agent_reply():
    # R4.3: sendConversationMessage's deferred "lands in a follow-up
    # increment" notice is gone - the real intent now dispatches through
    # AgentDispatcher.start_conversation_reply, same monkeypatch seam as
    # test_send_message_intent_dispatches_a_real_agent_reply and
    # test_agents.py (api_provider.chat directly). Uses a blocking fake chat
    # (started/release threading.Events, same convention as test_agents.py's
    # mid-flight tests) so the in-flight pendingRequestId state can actually
    # be observed, not just the before/after idle states.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        parent_id = await bus.dispatch_intent("scene", "addNode", [0, 0, "parent"])
        node_id = await bus.dispatch_intent("scene", "addConversationNode", [10, 10, parent_id])

        started = threading.Event()
        release = threading.Event()

        def blocking_chat(task, messages, **kwargs):
            started.set()
            release.wait(5)
            return {"message": {"content": "a real conversation reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", blocking_chat):
            returned_id = await bus.dispatch_intent(
                "scene", "sendConversationMessage", [node_id, "what is this graph about?"]
            )
            assert returned_id == node_id
            assert document.nodes[node_id].history == [
                {"role": "user", "content": "what is this graph about?"}
            ]

            await asyncio.to_thread(started.wait, 5)
            # Mid-flight: pendingRequestId surfaces as non-None both on the
            # domain node and in scene_payload.
            assert document.nodes[node_id].pending_request_id is not None
            rows = {n["id"]: n for n in document.scene_payload()["nodes"]}
            assert rows[node_id]["pendingRequestId"] is not None
            assert rows[node_id]["pendingRequestId"] == document.nodes[node_id].pending_request_id

            release.set()
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assert document.nodes[node_id].history == [
            {"role": "user", "content": "what is this graph about?"},
            {"role": "assistant", "content": "a real conversation reply"},
        ]

        notice = await bus.publish("notification")
        assert notice["visible"] is False, "a real reply landing is not a deferral - no notification fires"
        assert document.nodes[node_id].pending_request_id is None, "cleared again once the reply lands"
        rows = {n["id"]: n for n in document.scene_payload()["nodes"]}
        assert rows[node_id]["pendingRequestId"] is None

    asyncio.run(run())


def test_send_conversation_message_reply_with_code_fence_lands_raw_and_unparsed():
    # R4.3b: ConversationNode is EXEMPT from the response_parsing retrofit -
    # this is the machine-checked proof, not just the documenting comment in
    # backend/canvas.py's send_conversation_message _on_reply. A reply
    # containing a fenced code block must land verbatim in the conversation
    # node's plain-text history, with no code/thinking child node created.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        parent_id = await bus.dispatch_intent("scene", "addNode", [0, 0, "parent"])
        node_id = await bus.dispatch_intent("scene", "addConversationNode", [10, 10, parent_id])

        raw_reply = "Sure thing:\n\n```python\nprint('unparsed')\n```"

        def fake_chat(task, messages, **kwargs):
            return {"message": {"content": raw_reply}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", fake_chat):
            await bus.dispatch_intent(
                "scene", "sendConversationMessage", [node_id, "show me some code"]
            )
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assert document.nodes[node_id].history == [
            {"role": "user", "content": "show me some code"},
            {"role": "assistant", "content": raw_reply},
        ], "the raw reply (fences and all) lands verbatim - no parsing happened"
        assert not any(n.kind in ("code", "thinking") for n in document.nodes.values()), (
            "no child node of any kind was created for this reply"
        )

    asyncio.run(run())


def test_cancel_chat_request_intent_on_scene_topic_calls_agent_dispatcher_cancel():
    # A lightweight fake dispatcher is sufficient here - no real LLM call
    # needed, this just confirms the intent forwards to cancel().
    class _FakeDispatcher:
        def __init__(self):
            self.cancel_calls = []

        def cancel(self, request_id):
            self.cancel_calls.append(request_id)
            return True

    async def run():
        bus = SessionBus("cancel-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        register_canvas(bus, notifications, fake_dispatcher, composer_document)

        result = await bus.dispatch_intent("scene", "cancelChatRequest", ["req-123"])

        assert result is True
        assert fake_dispatcher.cancel_calls == ["req-123"]

    asyncio.run(run())


def test_append_conversation_assistant_message_intent_publishes_with_no_notification():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addNode", [0, 0, "parent"])
        node_id = await bus.dispatch_intent("scene", "addConversationNode", [10, 10, parent_id])

        returned_id = await bus.dispatch_intent(
            "scene", "appendConversationAssistantMessage", [node_id, "here is my answer"]
        )
        assert returned_id == node_id
        assert document.nodes[node_id].history == [
            {"role": "assistant", "content": "here is my answer"}
        ]

        notice = await bus.publish("notification")
        assert notice["visible"] is False, "a real reply landing is not a deferral - no notification fires"
        assert recorder.topics_seen().count("scene") == 3, "all three mutations publish (addNode, addConversationNode, appendConversationAssistantMessage)"

    asyncio.run(run())


def test_delete_conversation_message_intent_mutates_and_publishes():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addNode", [0, 0, "parent"])
        node_id = await bus.dispatch_intent("scene", "addConversationNode", [10, 10, parent_id])
        await bus.dispatch_intent("scene", "sendConversationMessage", [node_id, "first"])
        await bus.dispatch_intent(
            "scene", "appendConversationAssistantMessage", [node_id, "second"]
        )

        result = await bus.dispatch_intent("scene", "deleteConversationMessage", [node_id, 0])
        assert result is None
        assert document.nodes[node_id].history == [{"role": "assistant", "content": "second"}]

    asyncio.run(run())


def test_conversation_node_removed_generically_through_remove_nodes_intent():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addNode", [0, 0, "parent"])
        node_id = await bus.dispatch_intent("scene", "addConversationNode", [10, 10, parent_id])
        await bus.dispatch_intent("scene", "removeNodes", [[node_id]])
        assert node_id not in document.nodes

    asyncio.run(run())


def test_set_chat_collapsed_intent_works_generically_against_a_conversation_node():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addNode", [0, 0, "parent"])
        node_id = await bus.dispatch_intent("scene", "addConversationNode", [10, 10, parent_id])
        await bus.dispatch_intent("scene", "setChatCollapsed", [node_id, True])
        assert document.nodes[node_id].is_collapsed is True

    asyncio.run(run())


def test_send_message_starts_a_root_branch():
    doc = SceneDocument()
    node = doc.send_message("hello there")
    assert node.kind == "chat"
    assert node.is_user is True
    assert node.content == "hello there"
    assert doc.last_chat_node_id == node.id


def test_send_message_continues_the_active_branch():
    doc = SceneDocument()
    first = doc.send_message("first message")
    second = doc.send_message("second message")
    assert any(e.source == first.id and e.target == second.id for e in doc.edges.values())
    assert doc.last_chat_node_id == second.id


def test_send_message_after_deleting_the_active_node_continues_from_its_parent():
    doc = SceneDocument()
    first = doc.send_message("first")
    second = doc.send_message("second")
    doc.delete_chat_node(second.id)
    assert doc.last_chat_node_id == first.id
    third = doc.send_message("third")
    assert any(e.source == first.id and e.target == third.id for e in doc.edges.values())


# -- R4: chat_branch_history --------------------------------------------------


def test_chat_branch_history_returns_root_to_leaf_for_a_multi_hop_branch():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root question", True)
    reply = doc.add_chat_node(0, 160, "root answer", False, parent_id=root.id)
    follow_up = doc.add_chat_node(0, 320, "follow up", True, parent_id=reply.id)

    history = doc.chat_branch_history(follow_up.id)

    assert history == [
        {"role": "user", "content": "root question"},
        {"role": "assistant", "content": "root answer"},
        {"role": "user", "content": "follow up"},
    ]


def test_chat_branch_history_for_a_single_root_node_returns_one_entry():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "only message", True)
    assert doc.chat_branch_history(root.id) == [{"role": "user", "content": "only message"}]


def test_chat_branch_history_does_not_error_on_an_unknown_node_id():
    doc = SceneDocument()
    assert doc.chat_branch_history("ghost") == []


# -- R4.3c: regenerate response (domain-level) --------------------------------


def test_regenerate_response_returns_node_and_parent_id_for_a_valid_chat_node():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "question", True)
    child = doc.add_chat_node(0, 160, "answer", False, parent_id=parent.id)
    node, parent_id = doc.regenerate_response(child.id)
    assert node is child
    assert parent_id == parent.id


def test_regenerate_response_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().regenerate_response("ghost")


def test_regenerate_response_non_chat_node_raises_scene_error():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "question", True)
    code_node = doc.add_code_node(10, 10, "x = 1", "python", parent_id=parent.id)
    with pytest.raises(SceneError):
        doc.regenerate_response(code_node.id)


def test_regenerate_response_node_without_parent_raises_scene_error():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root question", True)
    with pytest.raises(SceneError):
        doc.regenerate_response(root.id)


def test_update_chat_node_content_mutates_content_only_leaves_title_and_flags_untouched():
    doc = SceneDocument()
    node = doc.add_chat_node(0, 0, "original content", False)
    original_title = node.title
    returned = doc.update_chat_node_content(node.id, "new content")
    assert returned is node
    assert node.content == "new content"
    assert node.title == original_title
    assert node.is_user is False
    assert node.is_collapsed is False
    assert node.kind == "chat"


def test_update_chat_node_content_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().update_chat_node_content("ghost", "text")


def test_remove_associated_content_children_removes_direct_code_document_image_thinking_children():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "assistant reply", False)
    code = doc.add_code_node(10, 10, "x = 1", "python", parent_id=chat.id)
    document_node = doc.add_document_node(20, 20, "file.txt", "content", "document", chat.id)
    image_node = doc.add_image_node(30, 30, b"bytes", "prompt", chat.id)
    thinking = doc.add_thinking_node(40, 40, "reasoning", chat.id)
    sibling = doc.add_chat_node(50, 50, "sibling", True)

    doc.remove_associated_content_children(chat.id)

    assert code.id not in doc.nodes
    assert document_node.id not in doc.nodes
    assert image_node.id not in doc.nodes
    assert thinking.id not in doc.nodes
    assert sibling.id in doc.nodes, "an unrelated sibling chat node must survive"
    assert chat.id in doc.nodes, "the chat node itself must survive"


def test_remove_associated_content_children_evicts_image_assets_via_remove_nodes():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "assistant reply", False)
    image_node = doc.add_image_node(10, 10, b"doomed bytes", "prompt", chat.id)
    asset_id = image_node.image_asset_id
    assert doc.get_image_asset(asset_id) is not None

    doc.remove_associated_content_children(chat.id)

    assert doc.get_image_asset(asset_id) is None, (
        "built on top of remove_nodes - asset eviction must come free, not be reimplemented"
    )


def test_remove_associated_content_children_is_one_hop_only():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "assistant reply", False)
    code_child = doc.add_code_node(10, 10, "x = 1", "python", parent_id=chat.id)
    grandchild = doc.add_code_node(20, 20, "y = 2", "python")
    doc.connect(code_child.id, grandchild.id)

    doc.remove_associated_content_children(chat.id)

    assert code_child.id not in doc.nodes
    assert grandchild.id in doc.nodes, "no cascade past the direct one-hop children"


def test_remove_associated_content_children_noop_when_no_content_children_exist():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "assistant reply", False)
    before_nodes = dict(doc.nodes)
    doc.remove_associated_content_children(chat.id)
    assert doc.nodes == before_nodes


# -- R4.3c: regenerate response (WS-intent level) -----------------------------


def test_regenerate_response_intent_mutates_the_existing_node_in_place_not_a_new_one():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def first_reply(task, messages, **kwargs):
            return {"message": {"content": "original reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", first_reply):
            user_id = await bus.dispatch_intent("scene", "sendMessage", ["hi"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_node = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)
        assert assistant_node.content == "original reply"
        node_count_before = len(document.nodes)

        def regenerated_reply(task, messages, **kwargs):
            return {"message": {"content": "regenerated reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", regenerated_reply):
            returned_id = await bus.dispatch_intent("scene", "regenerateResponse", [assistant_node.id])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assert returned_id == assistant_node.id, "the same node id, never a new node"
        assert assistant_node.id in document.nodes
        assert document.nodes[assistant_node.id].content == "regenerated reply"
        assert len(document.nodes) == node_count_before, "no new node was created"

    asyncio.run(run())


def test_regenerate_response_does_not_stream_unlike_an_ordinary_send():
    # R4.4 regression: start_chat_reply's stream=True default is for
    # send_message's Composer-send surface only. regenerate_response passes
    # stream=False explicitly (see canvas.py's own call site) - an
    # adversarial reviewer found the first R4.4 cut hardcoded stream=True
    # unconditionally, silently activating the Composer dock's live preview
    # for a Regenerate click on some unrelated node in the canvas, with no
    # way for the frontend to distinguish that from a real Send in flight.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def first_reply(task, messages, **kwargs):
            return {"message": {"content": "original reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", first_reply):
            user_id = await bus.dispatch_intent("scene", "sendMessage", ["hi"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_node = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)
        recorder.messages.clear()  # only care about messages from the regenerate call below

        def regenerated_reply(task, messages, **kwargs):
            return {"message": {"content": "regenerated reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", regenerated_reply):
            await bus.dispatch_intent("scene", "regenerateResponse", [assistant_node.id])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        stream_frames = [m for m in recorder.messages if m.get("kind") == "stream"]
        assert stream_frames == [], "regenerate_response must never emit stream frames"
        assert document.nodes[assistant_node.id].content == "regenerated reply"

    asyncio.run(run())


def test_regenerate_response_replaces_code_child_not_accumulates():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def first_reply(task, messages, **kwargs):
            return {"message": {"content": "Here:\n\n```python\nprint('one')\n```"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", first_reply):
            user_id = await bus.dispatch_intent("scene", "sendMessage", ["write code"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_node = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)
        assert len([n for n in document.nodes.values() if n.kind == "code"]) == 1

        def second_reply(task, messages, **kwargs):
            return {"message": {"content": "Now:\n\n```python\nprint('two')\n```"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", second_reply):
            await bus.dispatch_intent("scene", "regenerateResponse", [assistant_node.id])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        code_nodes = [n for n in document.nodes.values() if n.kind == "code"]
        assert len(code_nodes) == 1, "the old code child must be replaced, not accumulated"
        assert code_nodes[0].code == "print('two')"

    asyncio.run(run())


def test_regenerate_response_document_and_image_children_torn_down_but_never_recreated():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def first_reply(task, messages, **kwargs):
            return {"message": {"content": "original reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", first_reply):
            user_id = await bus.dispatch_intent("scene", "sendMessage", ["attach files"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_node = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)
        # Simulate a prior real attachment - manually attached, since
        # send_message's own _on_reply never creates document/image children.
        document_node = document.add_document_node(0, 0, "file.txt", "content", "document", assistant_node.id)
        image_node = document.add_image_node(0, 0, b"bytes", "prompt", assistant_node.id)

        def plain_reply(task, messages, **kwargs):
            return {"message": {"content": "just plain text, no attachments"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", plain_reply):
            await bus.dispatch_intent("scene", "regenerateResponse", [assistant_node.id])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assert document_node.id not in document.nodes
        assert image_node.id not in document.nodes
        assert not any(n.kind in ("document", "image") for n in document.nodes.values()), (
            "torn down but never recreated - parse_response structurally never emits document/image parts"
        )

    asyncio.run(run())


def test_regenerate_response_empty_reply_keeps_original_content_and_notifies():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def first_reply(task, messages, **kwargs):
            return {"message": {"content": "Here's code:\n\n```python\nprint('original')\n```"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", first_reply):
            user_id = await bus.dispatch_intent("scene", "sendMessage", ["show me code"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_node = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)
        assert assistant_node.content == "Here's code:"
        node_ids_before = set(document.nodes)

        def empty_reply(task, messages, **kwargs):
            return {"message": {"content": "   \n\n  "}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", empty_reply):
            await bus.dispatch_intent("scene", "regenerateResponse", [assistant_node.id])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assert document.nodes[assistant_node.id].content == "Here's code:", "original content is kept"
        assert set(document.nodes) == node_ids_before, "existing children must be untouched"
        notice = await bus.publish("notification")
        assert notice["visible"] is True
        assert notice["msgType"] == "warning"
        assert notice["message"] == (
            "The model returned an empty response. The original response has been kept."
        )

    asyncio.run(run())


def test_regenerate_response_reasoning_only_reply_uses_generated_content_not_reasoning_placeholder():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def first_reply(task, messages, **kwargs):
            return {"message": {"content": "original reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", first_reply):
            user_id = await bus.dispatch_intent("scene", "sendMessage", ["think about it"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_node = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)

        def reasoning_only_reply(task, messages, **kwargs):
            return {"message": {"content": "<think>pondering deeply</think>"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", reasoning_only_reply):
            await bus.dispatch_intent("scene", "regenerateResponse", [assistant_node.id])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        # The critical differentiator: regenerate's ternary is 1-way, unlike
        # send_message's 3-way priority chain - a reasoning-only reply must
        # fall back to the GENERATED-content placeholder, never the
        # reasoning-specific one.
        assert document.nodes[assistant_node.id].content == "[Generated Content]"
        assert document.nodes[assistant_node.id].content != "[Assistant Reasoning]"

        thinking_nodes = [n for n in document.nodes.values() if n.kind == "thinking"]
        assert len(thinking_nodes) == 1
        assert thinking_nodes[0].content == "pondering deeply"

    asyncio.run(run())


def test_regenerate_response_node_deleted_mid_flight_is_a_silent_noop():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def first_reply(task, messages, **kwargs):
            return {"message": {"content": "original reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", first_reply):
            user_id = await bus.dispatch_intent("scene", "sendMessage", ["hi"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_node = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)

        started = threading.Event()
        release = threading.Event()

        def blocking_chat(task, messages, **kwargs):
            started.set()
            release.wait(5)
            return {"message": {"content": "regenerated reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", blocking_chat):
            returned_id = await bus.dispatch_intent("scene", "regenerateResponse", [assistant_node.id])
            assert returned_id == assistant_node.id

            await asyncio.to_thread(started.wait, 5)
            document.remove_nodes([assistant_node.id])

            release.set()
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assert assistant_node.id not in document.nodes
        notice = await bus.publish("notification")
        assert notice["visible"] is False, "deleted-mid-flight is a silent no-op - no notification fires"

    asyncio.run(run())


def test_regenerate_response_unknown_node_id_shows_notification_not_a_crash():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        result = await bus.dispatch_intent("scene", "regenerateResponse", ["ghost"])

        assert result is None
        assert dispatcher._requests == {}, "no dispatch was ever scheduled"
        notice = await bus.publish("notification")
        assert notice["visible"] is True
        assert notice["msgType"] == "warning"
        assert notice["message"] == "This node has no parent and cannot be regenerated."

    asyncio.run(run())


def test_regenerate_response_shares_the_single_in_flight_guard():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def first_reply(task, messages, **kwargs):
            return {"message": {"content": "original reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", first_reply):
            user_id = await bus.dispatch_intent("scene", "sendMessage", ["hi"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_node = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)

        started = threading.Event()
        release = threading.Event()

        def blocking_chat(task, messages, **kwargs):
            started.set()
            release.wait(5)
            return {"message": {"content": "second message reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", blocking_chat):
            # Occupy the single in-flight slot with an ordinary sendMessage.
            await bus.dispatch_intent("scene", "sendMessage", ["second message"])
            await asyncio.to_thread(started.wait, 5)
            assert len(dispatcher._requests) == 1

            returned = await bus.dispatch_intent("scene", "regenerateResponse", [assistant_node.id])
            # Validation (a real chat node with a parent) still succeeds - the
            # guard lives one layer deeper, inside AgentDispatcher._dispatch.
            assert returned == assistant_node.id
            assert len(dispatcher._requests) == 1, "still just the original sendMessage in flight"

            notice = await bus.publish("notification")
            assert notice["visible"] is True
            assert notice["message"] == "A response is already being generated."

            release.set()
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

    asyncio.run(run())


def test_regenerate_response_of_a_non_tip_node_leaves_last_chat_node_id_untouched():
    # R4.3c design spec section 5's load-bearing rule: regenerating an OLDER,
    # non-tip node must never rewind last_chat_node_id back to it - only
    # send_message (creating a brand-new node) or delete_chat_node (removing
    # the tip itself) ever move that pointer. A regenerate mutates
    # node_to_regenerate in place without changing its id, so if it is not
    # already the tip, it must not become the tip just by being regenerated.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def first_reply(task, messages, **kwargs):
            return {"message": {"content": "first reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", first_reply):
            user1_id = await bus.dispatch_intent("scene", "sendMessage", ["first"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        old_node = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user1_id)

        def second_reply(task, messages, **kwargs):
            return {"message": {"content": "second reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", second_reply):
            await bus.dispatch_intent("scene", "sendMessage", ["second"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        tip_id = document.last_chat_node_id
        assert tip_id != old_node.id, "the branch must have advanced past old_node by now"

        def regenerated_reply(task, messages, **kwargs):
            return {"message": {"content": "regenerated content"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", regenerated_reply):
            await bus.dispatch_intent("scene", "regenerateResponse", [old_node.id])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assert document.last_chat_node_id == tip_id, \
            "regenerating an older node must never rewind the active branch tip"
        assert old_node.content == "regenerated content"

    asyncio.run(run())


def test_regenerate_response_shares_the_single_in_flight_guard_in_reverse():
    # Mirror of test_regenerate_response_shares_the_single_in_flight_guard:
    # this occupies the slot with a regenerateResponse first, then asserts a
    # concurrent ordinary sendMessage is bounced. Both directions exercise
    # the exact same AgentDispatcher._dispatch guard (`if self._requests:`),
    # caller-agnostic - see backend/tests/test_agents.py's own cross-channel
    # guard tests for the underlying primitive this is layered on top of.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def first_reply(task, messages, **kwargs):
            return {"message": {"content": "original reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", first_reply):
            user_id = await bus.dispatch_intent("scene", "sendMessage", ["hi"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_node = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != user_id)

        started = threading.Event()
        release = threading.Event()

        def blocking_chat(task, messages, **kwargs):
            started.set()
            release.wait(5)
            return {"message": {"content": "regenerated reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", blocking_chat):
            # Occupy the single in-flight slot with a regenerateResponse this time.
            await bus.dispatch_intent("scene", "regenerateResponse", [assistant_node.id])
            await asyncio.to_thread(started.wait, 5)
            assert len(dispatcher._requests) == 1

            returned = await bus.dispatch_intent("scene", "sendMessage", ["second message"])
            # sendMessage's own domain mutation (a new user ChatNode) still
            # happens unconditionally - the guard lives one layer deeper,
            # inside AgentDispatcher._dispatch, same as the forward direction.
            assert returned is not None
            assert len(dispatcher._requests) == 1, "still just the original regenerateResponse in flight"

            notice = await bus.publish("notification")
            assert notice["visible"] is True
            assert notice["message"] == "A response is already being generated."

            release.set()
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

    asyncio.run(run())


def test_drag_factor_is_bounded():
    doc = SceneDocument()
    doc.set_drag_factor(99)
    assert doc.drag_factor == DRAG_FACTOR_MAX
    doc.set_drag_factor(0.0001)
    assert doc.drag_factor == DRAG_FACTOR_MIN


def test_grid_payload_matches_generated_validator_shape():
    payload = SceneDocument().grid_payload()
    # Field-for-field the GridControlStatePayload contract (minus the
    # envelope, which the bus stamps): the R2 island port depends on this.
    assert set(payload) == {
        "gridSize",
        "gridOpacityPercent",
        "gridStyle",
        "gridColor",
        "sizePresets",
        "stylePresets",
        "colorPresets",
    }
    assert isinstance(payload["gridOpacityPercent"], int)
    assert len(payload["colorPresets"]) == 5


# -- intent surface over the bus --------------------------------------------


class Recorder:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)

    def topics_seen(self):
        return [m["topic"] for m in self.messages if m["kind"] == "state"]


class _FakeSettingsManager:
    """Stand-in for AgentDispatcher's settings_manager - canvas tests only
    need persona() to resolve, not real settings persistence."""

    def get_enable_system_prompt(self):
        return True


def make_bus_with_dispatcher():
    bus = SessionBus("canvas-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    composer_document = ComposerDocument()
    bus.register_topic("app-composer", composer_document.payload)
    agent_dispatcher = AgentDispatcher(_FakeSettingsManager())
    document = register_canvas(bus, notifications, agent_dispatcher, composer_document)
    recorder = Recorder()
    bus.attach(recorder)
    return bus, document, recorder, agent_dispatcher


def make_bus():
    bus, document, recorder, _ = make_bus_with_dispatcher()
    return bus, document, recorder


def test_scene_intents_mutate_and_publish():
    async def run():
        bus, document, recorder = make_bus()
        node_id = await bus.dispatch_intent("scene", "addNode", [40, 60, "hello"])
        assert document.nodes[node_id].title == "hello"
        other = await bus.dispatch_intent("scene", "addNode", [0, 0])
        edge_id = await bus.dispatch_intent("scene", "connectNodes", [node_id, other])
        assert edge_id in document.edges
        await bus.dispatch_intent("scene", "moveNode", [node_id, 1, 2])
        assert (document.nodes[node_id].x, document.nodes[node_id].y) == (1, 2)
        assert recorder.topics_seen().count("scene") == 4, "every mutation publishes"

    asyncio.run(run())


def test_chat_node_intents_mutate_and_publish():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addChatNode", [0, 0, "hi", True])
        child_id = await bus.dispatch_intent(
            "scene", "addChatNode", [10, 10, "reply", False, parent_id]
        )
        assert document.nodes[child_id].kind == "chat"
        assert any(
            e.source == parent_id and e.target == child_id for e in document.edges.values()
        )

        await bus.dispatch_intent("scene", "setChatCollapsed", [child_id, True])
        assert document.nodes[child_id].is_collapsed is True

        await bus.dispatch_intent("scene", "deleteChatNode", [parent_id])
        assert parent_id not in document.nodes
        assert child_id in document.nodes, "deleting the parent must not cascade-delete the child"
        assert recorder.topics_seen().count("scene") == 4, "every mutation publishes"

    asyncio.run(run())


def test_add_code_node_intent_creates_a_real_node_and_publishes():
    async def run():
        bus, document, recorder = make_bus()
        node_id = await bus.dispatch_intent(
            "scene", "addCodeNode", [0, 0, "def f(): pass", "python"]
        )
        assert document.nodes[node_id].kind == "code"
        assert document.nodes[node_id].code == "def f(): pass"
        assert document.nodes[node_id].language == "python"
        assert recorder.topics_seen().count("scene") == 1, "the mutation publishes"

    asyncio.run(run())


def test_send_message_intent_dispatches_a_real_agent_reply():
    # R4: sendMessage's deferred "lands in R4" notice is gone - the real
    # intent now dispatches through AgentDispatcher. Same monkeypatch seam as
    # test_agents.py (api_provider.chat directly), validating the real
    # wiring end to end through the WS intent layer.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def fake_chat(task, messages, **kwargs):
            return {"message": {"content": "a real agent reply"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", fake_chat):
            node_id = await bus.dispatch_intent("scene", "sendMessage", ["what is this graph about?"])
            # The reply lands inside a scheduled (not awaited) background
            # task - grab it from the dispatcher's registry and await it
            # directly rather than assuming the intent itself blocks for it.
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assert document.nodes[node_id].content == "what is this graph about?"
        assert document.nodes[node_id].is_user is True

        reply_nodes = [n for n in document.nodes.values() if n.kind == "chat" and n.id != node_id]
        assert len(reply_nodes) == 1
        reply_node = reply_nodes[0]
        assert reply_node.content == "a real agent reply"
        assert reply_node.is_user is False
        assert any(e.source == node_id and e.target == reply_node.id for e in document.edges.values())
        assert document.last_chat_node_id == reply_node.id
        assert recorder.topics_seen().count("scene") >= 2, "user node + reply node both publish scene"

    asyncio.run(run())


def test_send_message_reply_that_is_genuinely_empty_creates_no_assistant_node():
    # R4.3b regression: mirrors legacy handle_response's own outer gate
    # (`if text_content or parsed_parts:`) - a reply that parse_response
    # reduces to an empty parts list (whitespace-only) must create NO
    # assistant node at all, not a "[Empty Response]" placeholder node, and
    # must leave last_chat_node_id pointed at the user's own message (set by
    # send_message's domain method), not touch it again.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def fake_chat(task, messages, **kwargs):
            return {"message": {"content": "   \n\n   "}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", fake_chat):
            node_id = await bus.dispatch_intent("scene", "sendMessage", ["hello"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        reply_nodes = [n for n in document.nodes.values() if n.id != node_id]
        assert reply_nodes == [], "a genuinely empty reply must create no assistant/child nodes at all"
        assert document.last_chat_node_id == node_id

    asyncio.run(run())


def test_send_message_reply_with_code_fence_creates_code_child_and_edge():
    # R4.3b: a reply with leading text plus a fenced code block must split
    # into a real code-kind child node (correct language/content) connected
    # to the assistant node by a real edge - the assistant node's own
    # content is just the text portion, not the raw unparsed reply.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def fake_chat(task, messages, **kwargs):
            return {"message": {"content": "Here is the fix:\n\n```python\nprint('hi')\n```"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", fake_chat):
            user_node_id = await bus.dispatch_intent("scene", "sendMessage", ["write me a hello world"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_nodes = [
            n for n in document.nodes.values() if n.kind == "chat" and n.id != user_node_id
        ]
        assert len(assistant_nodes) == 1
        assistant_node = assistant_nodes[0]
        assert assistant_node.content == "Here is the fix:"

        code_nodes = [n for n in document.nodes.values() if n.kind == "code"]
        assert len(code_nodes) == 1
        code_node = code_nodes[0]
        assert code_node.language == "python"
        assert code_node.code == "print('hi')"

        assert any(
            e.source == assistant_node.id and e.target == code_node.id
            for e in document.edges.values()
        ), "a real edge connects the assistant node to its code child"
        assert document.last_chat_node_id == assistant_node.id

    asyncio.run(run())


def test_send_message_reply_that_is_only_thinking_uses_reasoning_placeholder():
    # R4.3b: a reply that is nothing but a <think> block has no text
    # content at all - the assistant node's content must fall back to the
    # literal "[Assistant Reasoning]" placeholder, with the actual reasoning
    # text living on a real thinking-kind child node instead.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def fake_chat(task, messages, **kwargs):
            return {"message": {"content": "<think>pondering deeply</think>"}}

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", fake_chat):
            user_node_id = await bus.dispatch_intent("scene", "sendMessage", ["what are you thinking?"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_nodes = [
            n for n in document.nodes.values() if n.kind == "chat" and n.id != user_node_id
        ]
        assert len(assistant_nodes) == 1
        assistant_node = assistant_nodes[0]
        assert assistant_node.content == "[Assistant Reasoning]"

        thinking_nodes = [n for n in document.nodes.values() if n.kind == "thinking"]
        assert len(thinking_nodes) == 1
        thinking_node = thinking_nodes[0]
        assert thinking_node.content == "pondering deeply"
        assert any(
            e.source == assistant_node.id and e.target == thinking_node.id
            for e in document.edges.values()
        )

        code_nodes = [n for n in document.nodes.values() if n.kind == "code"]
        assert code_nodes == [], "no code node was created"

    asyncio.run(run())


def test_send_message_reply_with_thinking_text_and_code_creates_both_children_on_same_parent():
    # R4.3b: thinking + surrounding text + one code fence must produce
    # exactly one thinking child and one code child, both parented to the
    # SAME assistant node (never chained to each other), with the assistant
    # node's own content being the real text portion, not a placeholder.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        def fake_chat(task, messages, **kwargs):
            return {
                "message": {
                    "content": (
                        "<think>working it out</think>\n"
                        "Here's the plan.\n"
                        "```python\nprint('plan')\n```"
                    )
                }
            }

        with patch.object(api_provider, "USE_API_MODE", False), \
                patch.object(api_provider, "LOCAL_PROVIDER_TYPE", task_config.LOCAL_PROVIDER_OLLAMA), \
                patch.dict(task_config.OLLAMA_MODELS, {task_config.TASK_CHAT: "test-model"}), \
                patch.object(api_provider, "chat", fake_chat):
            user_node_id = await bus.dispatch_intent("scene", "sendMessage", ["plan it out"])
            entry = next(iter(dispatcher._requests.values()))
            await entry["task"]

        assistant_nodes = [
            n for n in document.nodes.values() if n.kind == "chat" and n.id != user_node_id
        ]
        assert len(assistant_nodes) == 1
        assistant_node = assistant_nodes[0]
        assert assistant_node.content == "Here's the plan."

        thinking_nodes = [n for n in document.nodes.values() if n.kind == "thinking"]
        code_nodes = [n for n in document.nodes.values() if n.kind == "code"]
        assert len(thinking_nodes) == 1
        assert len(code_nodes) == 1

        assert any(
            e.source == assistant_node.id and e.target == thinking_nodes[0].id
            for e in document.edges.values()
        )
        assert any(
            e.source == assistant_node.id and e.target == code_nodes[0].id
            for e in document.edges.values()
        )
        assert not any(
            e.source == thinking_nodes[0].id and e.target == code_nodes[0].id
            for e in document.edges.values()
        ), "thinking and code children are not chained to each other"
        assert not any(
            e.source == code_nodes[0].id and e.target == thinking_nodes[0].id
            for e in document.edges.values()
        )
        assert document.last_chat_node_id == assistant_node.id

    asyncio.run(run())


def test_pin_intents_round_trip_through_the_store():
    async def run():
        bus, document, _ = make_bus()
        pin_id = await bus.dispatch_intent("scene", "addPin", ["Start here", 5, 9, "note!"])
        payload = document.scene_payload()
        assert payload["pins"] == [
            {"id": pin_id, "title": "Start here", "note": "note!", "x": 5.0, "y": 9.0}
        ]
        await bus.dispatch_intent("scene", "movePin", [pin_id, 50, 90])
        assert document.scene_payload()["pins"][0]["x"] == 50.0
        await bus.dispatch_intent("scene", "removePin", [pin_id])
        assert document.scene_payload()["pins"] == []

    asyncio.run(run())


def test_update_pin_intent_renames_and_validates():
    async def run():
        bus, document, _ = make_bus()
        pin_id = await bus.dispatch_intent("scene", "addPin", ["Original", 0, 0])
        await bus.dispatch_intent("scene", "updatePin", [pin_id, "Renamed", "a note"])
        pin = document.scene_payload()["pins"][0]
        assert pin["title"] == "Renamed"
        assert pin["note"] == "a note"
        with pytest.raises(Exception):
            await bus.dispatch_intent("scene", "updatePin", [pin_id, "   ", ""])

    asyncio.run(run())


def test_grid_intents_use_the_bridge_slot_names_and_publish_grid_topic():
    async def run():
        bus, document, recorder = make_bus()
        await bus.dispatch_intent("grid-control", "setGridSize", [50])
        await bus.dispatch_intent("grid-control", "setGridOpacityPercent", [140])
        await bus.dispatch_intent("grid-control", "setGridStyle", ["Lines"])
        await bus.dispatch_intent("grid-control", "setGridColor", ["#404040"])
        assert document.grid.grid_size == 50
        assert document.grid.grid_opacity == 1.0, "opacity clamps to 100%"
        assert document.grid.grid_style == "Lines"
        assert recorder.topics_seen().count("grid-control") == 4

    asyncio.run(run())


def test_unknown_grid_style_is_rejected():
    async def run():
        bus, _, _ = make_bus()
        with pytest.raises(SceneError):
            await bus.dispatch_intent("grid-control", "setGridStyle", ["Sparkles"])

    asyncio.run(run())


def test_font_intents_use_bridge_slot_names_and_bound_values():
    async def run():
        bus, document, _ = make_bus()
        await bus.dispatch_intent("scene", "setFontFamily", ["Consolas"])
        await bus.dispatch_intent("scene", "setFontSize", [99])
        await bus.dispatch_intent("scene", "setFontColor", ["#C7C7C7"])
        payload = document.scene_payload()
        assert payload["fontFamily"] == "Consolas"
        assert payload["fontSizePt"] == 16, "size clamps to FONT_SIZE_MAX"
        assert payload["fontColor"] == "#C7C7C7"
        with pytest.raises(SceneError):
            await bus.dispatch_intent("scene", "setFontFamily", ["Comic Sans MS"])

    asyncio.run(run())


def test_organize_arranges_nodes_in_a_stable_grid():
    async def run():
        bus, document, _ = make_bus()
        for i in range(5):
            await bus.dispatch_intent("scene", "addNode", [500 - i * 37, i * 91])
        await bus.dispatch_intent("scene", "organizeNodes", [])
        positions = {n.id: (n.x, n.y) for n in document.nodes.values()}
        # 5 nodes -> 3 columns; stable id order fills rows left-to-right.
        assert positions["n0"] == (0.0, 0.0)
        assert positions["n1"] == (260.0, 0.0)
        assert positions["n2"] == (520.0, 0.0)
        assert positions["n3"] == (0.0, 180.0)
        assert positions["n4"] == (260.0, 180.0)

    asyncio.run(run())


def test_preset_topics_match_generated_validator_shapes():
    async def run():
        bus, _, recorder = make_bus()
        drag = await bus.publish("drag-speed")
        font = await bus.publish("font-control")
        assert set(drag) >= {"percentPresets", "percentMin", "percentMax"}
        assert set(font) >= {"fontFamilies", "colorPresets", "sizeMin", "sizeMax"}
        assert len(font["fontFamilies"]) == 16

    asyncio.run(run())


def test_snap_and_drag_factor_intents_publish_scene():
    async def run():
        bus, document, _ = make_bus()
        await bus.dispatch_intent("scene", "setSnapToGrid", [True])
        await bus.dispatch_intent("scene", "setDragFactor", [0.5])
        payload = document.scene_payload()
        assert payload["snapToGrid"] is True
        assert payload["dragFactor"] == 0.5

    asyncio.run(run())


# -- R4.4a: Generate/Regenerate Image - domain-level resolvers ---------------


def test_resolve_generate_image_returns_chat_node_id_and_its_own_content():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "a cat wearing a wizard hat", True)
    parent_id, prompt = doc.resolve_generate_image(chat.id)
    assert parent_id == chat.id
    assert prompt == "a cat wearing a wizard hat"


def test_resolve_generate_image_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().resolve_generate_image("ghost")


def test_resolve_generate_image_non_chat_node_raises_scene_error():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "question", True)
    code_node = doc.add_code_node(10, 10, "x = 1", "python", parent_id=parent.id)
    with pytest.raises(SceneError):
        doc.resolve_generate_image(code_node.id)


def test_resolve_generate_image_empty_content_raises_the_empty_prompt_variant():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "   ", True)
    with pytest.raises(SceneEmptyPromptError):
        doc.resolve_generate_image(chat.id)
    # The empty-prompt variant IS a SceneError too - callers that only check
    # for the base class must still catch it.
    with pytest.raises(SceneError):
        doc.resolve_generate_image(chat.id)


def test_resolve_regenerate_image_returns_parent_id_and_the_image_nodes_own_content_not_the_parents():
    doc = SceneDocument()
    # The parent chat node's content is deliberately DIFFERENT from the
    # image's own stored prompt - regression-guards the R4.4a fix: regenerate
    # must read the ImageNode's OWN content, never the parent ChatNode's,
    # even though legacy's real mechanism reuses the (wrapped) parent text.
    chat = doc.add_chat_node(0, 0, 'Generated image for prompt: "a cat"', False)
    image_node = doc.add_image_node(0, 160, b"bytes", "a cat", chat.id)
    parent_id, prompt = doc.resolve_regenerate_image(image_node.id)
    assert parent_id == chat.id
    assert prompt == "a cat"
    assert prompt != chat.content


def test_resolve_regenerate_image_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().resolve_regenerate_image("ghost")


def test_resolve_regenerate_image_non_image_node_raises_scene_error():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "question", True)
    with pytest.raises(SceneError):
        doc.resolve_regenerate_image(chat.id)


def test_resolve_regenerate_image_empty_content_raises_the_empty_prompt_variant():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "assistant reply", False)
    image_node = doc.add_image_node(0, 160, b"bytes", "", chat.id)
    with pytest.raises(SceneEmptyPromptError):
        doc.resolve_regenerate_image(image_node.id)


# -- R4.4a: Generate/Regenerate Image - the success primitive -----------------


def test_add_generated_image_reply_creates_two_new_nodes_with_the_correct_parent_chain():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "draw a cat", True)
    node_count_before = len(doc.nodes)

    new_chat_node, new_image_node = doc.add_generated_image_reply(chat.id, "a cat", b"png-bytes")

    assert len(doc.nodes) == node_count_before + 2
    assert new_chat_node.kind == "chat"
    assert new_image_node.kind == "image"

    def parent_of(node_id):
        edge = next((e for e in doc.edges.values() if e.target == node_id), None)
        return edge.source if edge is not None else None

    assert parent_of(new_image_node.id) == new_chat_node.id
    assert parent_of(new_chat_node.id) == chat.id


def test_add_generated_image_reply_new_chat_node_content_is_the_exact_wrapper_string():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "draw a cat", True)
    new_chat_node, new_image_node = doc.add_generated_image_reply(chat.id, "a cat wearing a hat", b"png-bytes")
    assert new_chat_node.content == 'Generated image for prompt: "a cat wearing a hat"'
    assert new_chat_node.is_user is False
    assert new_image_node.content == "a cat wearing a hat"


def test_add_generated_image_reply_gains_exactly_one_image_asset_entry():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "draw a cat", True)
    assets_before = dict(doc.image_assets)
    _, new_image_node = doc.add_generated_image_reply(chat.id, "a cat", b"png-bytes", mime_type="image/jpeg")
    assert len(doc.image_assets) == len(assets_before) + 1
    assert doc.get_image_asset(new_image_node.image_asset_id) == (b"png-bytes", "image/jpeg")


def test_add_generated_image_reply_leaves_last_chat_node_id_untouched():
    doc = SceneDocument()
    node = doc.send_message("hello")
    assert doc.last_chat_node_id == node.id
    doc.add_generated_image_reply(node.id, "a cat", b"png-bytes")
    assert doc.last_chat_node_id == node.id, "image generation is side content, not a branch-continuation point"


def test_add_generated_image_reply_unknown_parent_raises_scene_error():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_generated_image_reply("ghost", "a cat", b"png-bytes")


# -- R4.4a: Generate/Regenerate Image - WS-intent level -----------------------


def test_generate_image_intent_empty_content_shows_warning_and_never_dispatches():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        chat = document.add_chat_node(0, 0, "   ", True)

        calls = []

        def recording_generate_image(prompt, **kwargs):
            calls.append(prompt)
            return b"bytes"

        with patch.object(api_provider, "generate_image", recording_generate_image):
            result = await bus.dispatch_intent("scene", "generateImage", [chat.id])

        assert result is None
        assert calls == [], "api_provider.generate_image must never be reached"
        assert dispatcher._image_requests == {}
        notice = await bus.publish("notification")
        assert notice["visible"] is True
        assert notice["msgType"] == "warning"
        assert notice["message"] == "The selected node has no text to use as a prompt."

    asyncio.run(run())


def test_generate_image_intent_unknown_node_shows_the_wrong_kind_message():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        result = await bus.dispatch_intent("scene", "generateImage", ["ghost"])
        assert result is None
        assert dispatcher._image_requests == {}
        notice = await bus.publish("notification")
        assert notice["visible"] is True
        assert notice["msgType"] == "warning"
        assert notice["message"] == "This node can't be used to generate an image."

    asyncio.run(run())


def test_generate_image_intent_non_chat_node_shows_the_wrong_kind_message():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        parent = document.add_chat_node(0, 0, "question", True)
        code_node = document.add_code_node(10, 10, "x = 1", "python", parent_id=parent.id)
        result = await bus.dispatch_intent("scene", "generateImage", [code_node.id])
        assert result is None
        notice = await bus.publish("notification")
        assert notice["message"] == "This node can't be used to generate an image."

    asyncio.run(run())


def test_regenerate_image_intent_unknown_node_shows_the_no_prompt_message():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        result = await bus.dispatch_intent("scene", "regenerateImage", ["ghost"])
        assert result is None
        assert dispatcher._image_requests == {}
        notice = await bus.publish("notification")
        assert notice["visible"] is True
        assert notice["msgType"] == "warning"
        assert notice["message"] == "This image has no prompt to regenerate from."

    asyncio.run(run())


def test_regenerate_image_intent_non_image_node_shows_the_no_prompt_message():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        chat = document.add_chat_node(0, 0, "a chat node", True)
        result = await bus.dispatch_intent("scene", "regenerateImage", [chat.id])
        assert result is None
        notice = await bus.publish("notification")
        assert notice["message"] == "This image has no prompt to regenerate from."

    asyncio.run(run())


def test_regenerate_image_intent_empty_content_shows_the_no_prompt_message():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        chat = document.add_chat_node(0, 0, "assistant reply", False)
        image_node = document.add_image_node(0, 160, b"bytes", "", chat.id)
        result = await bus.dispatch_intent("scene", "regenerateImage", [image_node.id])
        assert result is None
        notice = await bus.publish("notification")
        assert notice["message"] == "This image has no prompt to regenerate from."

    asyncio.run(run())


def test_generate_image_intent_full_success_round_trip_creates_two_nodes_and_republishes_scene():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        chat = document.add_chat_node(0, 0, "a cat wearing a hat", True)
        node_count_before = len(document.nodes)
        scene_publishes_before = recorder.topics_seen().count("scene")

        with patch.object(api_provider, "generate_image", lambda prompt, **kwargs: b"real-png-bytes"):
            result = await bus.dispatch_intent("scene", "generateImage", [chat.id])
            assert result is None
            entry = next(iter(dispatcher._image_requests.values()))
            await entry["task"]

        assert len(document.nodes) == node_count_before + 2
        new_chat = next(n for n in document.nodes.values() if n.kind == "chat" and n.id != chat.id)
        new_image = next(n for n in document.nodes.values() if n.kind == "image")
        assert new_chat.content == 'Generated image for prompt: "a cat wearing a hat"'
        assert new_image.content == "a cat wearing a hat"
        assert document.get_image_asset(new_image.image_asset_id) == (b"real-png-bytes", "image/png")
        assert recorder.topics_seen().count("scene") > scene_publishes_before
        assert dispatcher._image_requests == {}

    asyncio.run(run())


def test_regenerate_image_intent_full_success_round_trip_creates_two_nodes_using_the_image_nodes_own_prompt():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        chat = document.add_chat_node(0, 0, 'Generated image for prompt: "a cat"', False)
        old_image = document.add_image_node(0, 160, b"old-bytes", "a cat", chat.id)
        node_count_before = len(document.nodes)

        with patch.object(api_provider, "generate_image", lambda prompt, **kwargs: b"new-png-bytes"):
            result = await bus.dispatch_intent("scene", "regenerateImage", [old_image.id])
            assert result is None
            entry = next(iter(dispatcher._image_requests.values()))
            await entry["task"]

        assert len(document.nodes) == node_count_before + 2, "old image node is left untouched, not replaced"
        assert old_image.id in document.nodes
        new_image = next(n for n in document.nodes.values() if n.kind == "image" and n.id != old_image.id)
        assert new_image.content == "a cat"
        assert document.get_image_asset(new_image.image_asset_id) == (b"new-png-bytes", "image/png")

    asyncio.run(run())


def test_dispatch_image_mid_flight_delete_of_the_parent_is_a_silent_noop():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        chat = document.add_chat_node(0, 0, "a cat wearing a hat", True)

        started = threading.Event()
        release = threading.Event()

        def blocking_generate_image(prompt, **kwargs):
            started.set()
            release.wait(5)
            return b"png-bytes"

        with patch.object(api_provider, "generate_image", blocking_generate_image):
            result = await bus.dispatch_intent("scene", "generateImage", [chat.id])
            assert result is None

            await asyncio.to_thread(started.wait, 5)
            document.remove_nodes([chat.id])

            release.set()
            entry = next(iter(dispatcher._image_requests.values()))
            await entry["task"]

        assert chat.id not in document.nodes
        assert not any(n.kind == "image" for n in document.nodes.values()), "no new nodes were created"
        assert dispatcher._image_requests == {}
        notice = await bus.publish("notification")
        assert notice["visible"] is False, "deleted-mid-flight is a silent no-op - no notification fires"

    asyncio.run(run())


# -- R5.1: web research node - domain-level ----------------------------------


def test_add_web_research_node_creates_a_real_web_research_kind_node():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "research this for me", True)
    node = doc.add_web_research_node(10, 20, parent.id)
    assert node.kind == "web_research"
    assert node.title == "Web Research"
    assert node.content == ""
    assert any(e.source == parent.id and e.target == node.id for e in doc.edges.values())


def test_add_web_research_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_web_research_node(0, 0, "ghost")


def test_add_web_research_node_requires_a_parent_id():
    # Same as add_document_node/add_thinking_node/add_html_node/add_image_node/
    # add_conversation_node - parent_id has no default in add_web_research_node's
    # signature, so calling without one is a TypeError (missing required
    # argument), not a SceneError.
    doc = SceneDocument()
    with pytest.raises(TypeError):
        doc.add_web_research_node(0, 0)


def test_web_research_node_deletion_goes_through_the_generic_remove_nodes_path():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_web_research_node(10, 10, parent.id)
    assert not hasattr(doc, "delete_web_research_node"), (
        "web_research nodes are not branch points - no special delete method"
    )
    doc.remove_nodes([node.id])
    assert node.id not in doc.nodes
    assert not any(e.target == node.id or e.source == node.id for e in doc.edges.values())


def test_start_web_research_run_sets_content_and_resets_progress_fields():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_web_research_node(0, 0, parent.id)
    # Simulate a previous run's leftover progress state.
    node.research_stage = "fetching"
    node.research_completed = 2
    node.research_total = 4
    node.research_active_source_id = "s1-old"
    node.research_error = "stale error"

    returned = doc.start_web_research_run(node.id, "what is the capital of France?")

    assert returned is node
    assert node.content == "what is the capital of France?"
    assert node.research_stage == ""
    assert node.research_completed == 0
    assert node.research_total == 0
    assert node.research_active_source_id is None
    assert node.research_error == ""


def test_start_web_research_run_does_not_clear_a_stale_previous_result():
    # Deliberate stale-while-revalidate (see the method's own docstring): the
    # previous run's answer stays visible until THIS run replaces it on
    # success, or fails/cancels (leaving the stale result annotated by the
    # new research_error).
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_web_research_node(0, 0, parent.id)
    node.research_result = {"answerMarkdown": "a previous stale answer"}

    doc.start_web_research_run(node.id, "a follow-up question")

    assert node.research_result == {"answerMarkdown": "a previous stale answer"}


def test_start_web_research_run_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().start_web_research_run("ghost", "a query")


def test_start_web_research_run_wrong_kind_raises_scene_error():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "not a web research node", True)
    with pytest.raises(SceneError):
        doc.start_web_research_run(chat.id, "a query")


def test_apply_web_research_progress_updates_stage_completed_total_source_id_from_a_duck_typed_event():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_web_research_node(0, 0, parent.id)
    fake_event = SimpleNamespace(
        stage=SimpleNamespace(value="fetching"), completed=1, total=4, source_id="s1-abc123"
    )

    returned = doc.apply_web_research_progress(node.id, fake_event)

    assert returned is node
    assert node.research_stage == "fetching"
    assert node.research_completed == 1
    assert node.research_total == 4
    assert node.research_active_source_id == "s1-abc123"


def test_apply_web_research_progress_is_a_silent_noop_for_a_deleted_or_unknown_node():
    doc = SceneDocument()
    fake_event = SimpleNamespace(
        stage=SimpleNamespace(value="searching"), completed=0, total=1, source_id=None
    )
    assert doc.apply_web_research_progress("ghost", fake_event) is None


def test_complete_web_research_run_sets_result_and_clears_error_and_active_source():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_web_research_node(0, 0, parent.id)
    node.research_error = "a previous error"
    node.research_active_source_id = "s1-still-active"
    result_wire = {"answerMarkdown": "the answer", "sources": []}

    returned = doc.complete_web_research_run(node.id, result_wire)

    assert returned is node
    assert node.research_stage == "completed"
    assert node.research_error == ""
    assert node.research_active_source_id is None
    assert node.research_result == result_wire


def test_complete_web_research_run_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().complete_web_research_run("ghost", {"answerMarkdown": "x"})


def test_fail_web_research_run_sets_cancelled_stage_and_message():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_web_research_node(0, 0, parent.id)
    node.research_active_source_id = "s1-in-flight"

    returned = doc.fail_web_research_run(node.id, cancelled=True, message="Web research cancelled.")

    assert returned is node
    assert node.research_stage == "cancelled"
    assert node.research_error == "Web research cancelled."
    assert node.research_active_source_id is None


def test_fail_web_research_run_sets_failed_stage_and_message():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_web_research_node(0, 0, parent.id)

    returned = doc.fail_web_research_run(node.id, cancelled=False, message="The search provider failed.")

    assert returned is node
    assert node.research_stage == "failed"
    assert node.research_error == "The search provider failed."


def test_fail_web_research_run_does_not_clear_a_stale_previous_result():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_web_research_node(0, 0, parent.id)
    node.research_result = {"answerMarkdown": "a previous stale answer"}

    doc.fail_web_research_run(node.id, cancelled=False, message="boom")

    assert node.research_result == {"answerMarkdown": "a previous stale answer"}


def test_fail_web_research_run_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().fail_web_research_run("ghost", cancelled=False, message="boom")


def test_scene_payload_includes_research_fields_defaulted_for_other_kinds():
    doc = SceneDocument()
    doc.add_node(0, 0, "plain")
    parent = doc.add_node(1, 1, "parent")
    node = doc.add_web_research_node(2, 2, parent.id)

    rows = {n["id"]: n for n in doc.scene_payload()["nodes"]}
    assert rows["n0"]["researchStage"] == ""
    assert rows["n0"]["researchCompleted"] == 0
    assert rows["n0"]["researchTotal"] == 0
    assert rows["n0"]["researchActiveSourceId"] is None
    assert rows["n0"]["researchError"] == ""
    assert rows["n0"]["researchResult"] is None

    doc.start_web_research_run(node.id, "a query")
    fake_event = SimpleNamespace(
        stage=SimpleNamespace(value="fetching"), completed=1, total=2, source_id="s1-x"
    )
    doc.apply_web_research_progress(node.id, fake_event)
    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]
    assert row["kind"] == "web_research"
    assert row["content"] == "a query"
    assert row["researchStage"] == "fetching"
    assert row["researchCompleted"] == 1
    assert row["researchTotal"] == 2
    assert row["researchActiveSourceId"] == "s1-x"


def test_research_result_wire_camel_cases_a_research_result():
    result = ResearchResult(
        request_id="req-1",
        original_query="what is the capital of France?",
        effective_query="capital of France",
        answer_markdown="Paris is the capital of France [s1-abc].",
        sources=[
            ResearchSource(
                source_id="s1-abc",
                title="France - Wikipedia",
                url="https://example.test/france",
                canonical_url="https://example.test/france",
                snippet="France is a country...",
                rank=1,
                provider="DuckDuckGo",
                final_url="https://example.test/france",
                status="accepted",
                error_code="",
                error_message="",
                truncated=False,
                content_hash="deadbeef",
                citation_count=1,
            )
        ],
        citations=[ResearchCitation(source_id="s1-abc", marker="[s1-abc]", claim_context="")],
        warnings=["Source 2 was not used (low_quality)."],
        provider_snapshot={},
    )

    wire = _research_result_wire(result)

    assert wire == {
        "requestId": "req-1",
        "originalQuery": "what is the capital of France?",
        "effectiveQuery": "capital of France",
        "answerMarkdown": "Paris is the capital of France [s1-abc].",
        "sources": [
            {
                "sourceId": "s1-abc",
                "title": "France - Wikipedia",
                "url": "https://example.test/france",
                "canonicalUrl": "https://example.test/france",
                "snippet": "France is a country...",
                "rank": 1,
                "provider": "DuckDuckGo",
                "finalUrl": "https://example.test/france",
                "status": "accepted",
                "errorCode": "",
                "errorMessage": "",
                "truncated": False,
                "contentHash": "deadbeef",
                "citationCount": 1,
            }
        ],
        "citations": [{"sourceId": "s1-abc", "marker": "[s1-abc]", "claimContext": ""}],
        "warnings": ["Source 2 was not used (low_quality)."],
        "providerSnapshot": {},
    }


# -- R5.1: web research node - WS-intent level -------------------------------


def test_run_web_research_intent_publishes_scene_and_calls_start_web_research_with_correct_branch_history():
    class _FakeDispatcher:
        def __init__(self):
            self.calls = []

        async def start_web_research(self, **kwargs):
            self.calls.append(kwargs)

        def cancel_web_research(self, request_id):
            return False

        def is_web_research_busy(self):
            return False

    async def run():
        bus = SessionBus("run-web-research-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        document = register_canvas(bus, notifications, fake_dispatcher, composer_document)
        recorder = Recorder()
        bus.attach(recorder)

        root = await bus.dispatch_intent("scene", "addChatNode", [0, 0, "root question", True])
        # add_web_research_node itself always requires a real parent - here we
        # parent the web_research node off the CHAT node so branch_history is
        # meaningfully non-trivial (mirrors how a real Web Research node
        # branches from a chat node in production).
        wr_node = document.add_web_research_node(0, 0, root)
        recorder.messages.clear()  # only care about messages from runWebResearch below

        result = await bus.dispatch_intent(
            "scene", "runWebResearch", [wr_node.id, "what is this about?"]
        )

        assert result == wr_node.id
        assert document.nodes[wr_node.id].content == "what is this about?"
        assert recorder.topics_seen().count("scene") == 1, "start_web_research_run publishes scene once"

        assert len(fake_dispatcher.calls) == 1
        call = fake_dispatcher.calls[0]
        assert call["bus"] is bus
        assert call["notifications_state"] is notifications
        assert call["node"] is wr_node
        assert call["node_id"] == wr_node.id
        assert call["query"] == "what is this about?"
        assert call["branch_history"] == document.chat_branch_history(root)
        assert callable(call["on_progress"])
        assert callable(call["on_success"])
        assert callable(call["on_failure"])

    asyncio.run(run())


def test_run_web_research_intent_unknown_node_shows_notification_not_a_crash():
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()

        result = await bus.dispatch_intent("scene", "runWebResearch", ["ghost", "a query"])

        assert result is None
        assert dispatcher._web_research_requests == {}
        notice = await bus.publish("notification")
        assert notice["visible"] is True
        assert notice["msgType"] == "warning"
        assert notice["message"] == "This node no longer exists."

    asyncio.run(run())


def test_cancel_web_research_request_intent_calls_agent_dispatcher_cancel_web_research():
    class _FakeDispatcher:
        def __init__(self):
            self.cancel_calls = []

        def cancel_web_research(self, request_id):
            self.cancel_calls.append(request_id)
            return True

        def is_web_research_busy(self):
            return False

    async def run():
        bus = SessionBus("cancel-web-research-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        register_canvas(bus, notifications, fake_dispatcher, composer_document)

        result = await bus.dispatch_intent("scene", "cancelWebResearchRequest", ["req-123"])

        assert result is None
        assert fake_dispatcher.cancel_calls == ["req-123"]

    asyncio.run(run())


def test_run_web_research_mid_flight_delete_of_the_node_is_a_silent_noop():
    # Mirrors test_dispatch_image_mid_flight_delete_of_the_parent_is_a_silent_noop's
    # own pattern: block the underlying call with threading Events, delete the
    # node between dispatch and callback invocation, and confirm _on_progress/
    # _on_success/_on_failure inside run_web_research's closure all no-op
    # silently rather than raising or recreating anything.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        parent = document.add_chat_node(0, 0, "research this please", True)
        node = document.add_web_research_node(0, 160, parent.id)

        started = threading.Event()
        release = threading.Event()

        def blocking_run(self, request, *, token=None, progress=None):
            started.set()
            release.wait(5)
            return SimpleNamespace()  # never actually read - node is gone by then

        with patch.object(agents_module.WebResearchService, "run", blocking_run):
            result = await bus.dispatch_intent(
                "scene", "runWebResearch", [node.id, "what is this about?"]
            )
            assert result == node.id

            await asyncio.to_thread(started.wait, 5)
            document.remove_nodes([node.id])

            release.set()
            entry = next(iter(dispatcher._web_research_requests.values()))
            await entry["task"]

        assert node.id not in document.nodes
        assert dispatcher._web_research_requests == {}
        notice = await bus.publish("notification")
        assert notice["visible"] is False, "deleted-mid-flight is a silent no-op - no notification fires"

    asyncio.run(run())


def test_run_web_research_on_a_different_node_while_one_is_busy_does_not_clobber_its_state():
    # Review-found regression guard: run_web_research used to call
    # start_web_research_run (which unconditionally resets research_stage/
    # research_error/progress fields and republishes scene) BEFORE checking
    # whether AgentDispatcher's single in-flight slot was already busy - so
    # clicking Run on node B while node A was mid-research would silently wipe
    # B's existing failure/cancelled banner even though no new run for B ever
    # actually started. The busy check must happen first.
    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        parent = document.add_chat_node(0, 0, "research this please", True)
        node_a = document.add_web_research_node(0, 160, parent.id)
        node_b = document.add_web_research_node(200, 160, parent.id)

        # Give node_b a pre-existing failed state to prove it survives.
        document.start_web_research_run(node_b.id, "node b's original query")
        document.fail_web_research_run(node_b.id, cancelled=False, message="node b failed earlier")
        assert node_b.research_stage == "failed"
        assert node_b.research_error == "node b failed earlier"

        started = threading.Event()
        release = threading.Event()
        fake_result = ResearchResult(
            request_id="req-a",
            original_query="node a's query",
            effective_query="node a's query",
            answer_markdown="node a's result",
            sources=[],
            citations=[],
            warnings=[],
            provider_snapshot={},
        )

        def blocking_run(self, request, *, token=None, progress=None):
            started.set()
            release.wait(5)
            return fake_result

        with patch.object(agents_module.WebResearchService, "run", blocking_run):
            result_a = await bus.dispatch_intent(
                "scene", "runWebResearch", [node_a.id, "node a's query"]
            )
            assert result_a == node_a.id
            await asyncio.to_thread(started.wait, 5)

            # node_a is now in flight (the single web-research slot is busy).
            # Clicking Run on node_b must be rejected up front, WITHOUT
            # touching node_b's stage/error/content at all.
            result_b = await bus.dispatch_intent(
                "scene", "runWebResearch", [node_b.id, "a brand new query for b"]
            )
            assert result_b is None
            assert node_b.research_stage == "failed", "node_b's terminal state must survive the bounce"
            assert node_b.research_error == "node b failed earlier"
            assert node_b.content == "node b's original query", "node_b's content must not be overwritten"

            notice = await bus.publish("notification")
            assert notice["visible"] is True
            assert notice["msgType"] == "info"
            assert notice["message"] == "A web research request is already running."

            release.set()
            entry = next(iter(dispatcher._web_research_requests.values()))
            await entry["task"]

        assert node_a.research_stage == "completed"
        assert node_a.research_result["answerMarkdown"] == "node a's result"

    asyncio.run(run())


# -- R5.2: artifact/drafter node ----------------------------------------------


def test_add_artifact_node_creates_a_real_artifact_kind_node():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "draft this for me", True)
    node = doc.add_artifact_node(10, 20, parent.id)
    assert node.kind == "artifact"
    assert node.title == "Artifact"
    assert node.artifact_content == ""
    assert node.history == []
    assert any(e.source == parent.id and e.target == node.id for e in doc.edges.values())


def test_add_artifact_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_artifact_node(0, 0, "ghost")


def test_add_artifact_node_requires_a_parent_id():
    # Same as add_document_node/add_thinking_node/add_html_node/add_image_node/
    # add_conversation_node/add_web_research_node - parent_id has no default in
    # add_artifact_node's signature, so calling without one is a TypeError
    # (missing required argument), not a SceneError.
    doc = SceneDocument()
    with pytest.raises(TypeError):
        doc.add_artifact_node(0, 0)


def test_artifact_node_deletion_goes_through_the_generic_remove_nodes_path():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_artifact_node(10, 10, parent.id)
    assert not hasattr(doc, "delete_artifact_node"), (
        "artifact nodes are not branch points - no special delete method"
    )
    doc.remove_nodes([node.id])
    assert node.id not in doc.nodes
    assert not any(e.target == node.id or e.source == node.id for e in doc.edges.values())


def test_append_artifact_user_message_appends_a_user_turn():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_artifact_node(0, 0, parent.id)
    returned = doc.append_artifact_user_message(node.id, "add a conclusion section")
    assert returned is node
    assert node.history == [{"role": "user", "content": "add a conclusion section"}]


def test_append_artifact_user_message_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().append_artifact_user_message("ghost", "hi")


def test_send_artifact_message_is_a_thin_wrapper_over_append_artifact_user_message():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_artifact_node(0, 0, parent.id)
    returned = doc.send_artifact_message(node.id, "start drafting")
    assert returned is node
    assert node.history == [{"role": "user", "content": "start drafting"}]


def test_complete_artifact_generation_replaces_content_and_appends_assistant_turn():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_artifact_node(0, 0, parent.id)
    doc.append_artifact_user_message(node.id, "draft a project brief")

    returned = doc.complete_artifact_generation(
        node.id, "# Project Brief\n\nDraft content.", "Here's a first draft."
    )

    assert returned is node
    assert node.artifact_content == "# Project Brief\n\nDraft content."
    assert node.history == [
        {"role": "user", "content": "draft a project brief"},
        {"role": "assistant", "content": "Here's a first draft."},
    ]


def test_complete_artifact_generation_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().complete_artifact_generation("ghost", "doc", "message")


def test_scene_payload_includes_artifact_content_for_artifact_kind_rows():
    doc = SceneDocument()
    doc.add_node(0, 0, "plain")
    parent = doc.add_node(1, 1, "parent")
    node = doc.add_artifact_node(2, 2, parent.id)

    rows = {n["id"]: n for n in doc.scene_payload()["nodes"]}
    assert rows["n0"]["artifactContent"] == ""

    doc.complete_artifact_generation(node.id, "the drafted document", "done")
    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]
    assert row["kind"] == "artifact"
    assert row["artifactContent"] == "the drafted document"


# -- R5.2: artifact/drafter node - WS-intent level ----------------------------
#
# Beyond the spec's enumerated SceneDocument-level tests above, these two
# exercise register_canvas's own sendArtifactMessage/cancelArtifactRequest
# closures - the new production code path in canvas.py itself - mirroring
# Web Research's own WS-intent-level coverage
# (test_run_web_research_intent_publishes_scene_and_calls_start_web_research_with_correct_branch_history/
# test_cancel_web_research_request_intent_calls_agent_dispatcher_cancel_web_research)
# so that wiring is not left untested just because it wasn't spelled out
# alongside the SceneDocument-level list.


def test_send_artifact_message_intent_dispatches_a_real_agent_reply_with_correct_branch_history():
    class _FakeDispatcher:
        def __init__(self):
            self.calls = []

        async def start_artifact_reply(self, **kwargs):
            self.calls.append(kwargs)

        def cancel_artifact(self, request_id):
            return False

    async def run():
        bus = SessionBus("send-artifact-message-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        document = register_canvas(bus, notifications, fake_dispatcher, composer_document)
        recorder = Recorder()
        bus.attach(recorder)

        root = await bus.dispatch_intent("scene", "addChatNode", [0, 0, "draft me a brief", True])
        # add_artifact_node itself always requires a real parent - here we
        # parent the artifact node off the CHAT node so branch_history is
        # meaningfully non-trivial (mirrors run_web_research's own test).
        art_node = document.add_artifact_node(0, 0, root)
        recorder.messages.clear()  # only care about messages from sendArtifactMessage below

        result = await bus.dispatch_intent(
            "scene", "sendArtifactMessage", [art_node.id, "draft the introduction"]
        )

        assert result == art_node.id
        assert document.nodes[art_node.id].history == [
            {"role": "user", "content": "draft the introduction"}
        ]
        assert recorder.topics_seen().count("scene") == 1, "send_artifact_message publishes scene once"
        assert len(fake_dispatcher.calls) == 1
        call = fake_dispatcher.calls[0]
        assert call["bus"] is bus
        assert call["notifications_state"] is notifications
        assert call["node"] is art_node
        assert call["current_artifact"] == ""
        assert call["history"] == document.chat_branch_history(root) + [
            {"role": "user", "content": "draft the introduction"}
        ]
        assert callable(call["on_reply"])

    asyncio.run(run())


def test_cancel_artifact_request_intent_calls_agent_dispatcher_cancel_artifact():
    class _FakeDispatcher:
        def __init__(self):
            self.cancel_calls = []

        def cancel_artifact(self, request_id):
            self.cancel_calls.append(request_id)
            return True

    async def run():
        bus = SessionBus("cancel-artifact-request-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        register_canvas(bus, notifications, fake_dispatcher, composer_document)

        result = await bus.dispatch_intent("scene", "cancelArtifactRequest", ["req-123"])

        assert result is None
        assert fake_dispatcher.cancel_calls == ["req-123"]

    asyncio.run(run())


# -- R5.3: gitlink node -------------------------------------------------------


def test_add_gitlink_node_requires_valid_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_gitlink_node(0, 0, "ghost")


def test_add_gitlink_node_creates_child():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "wire up gitlink", True)
    node = doc.add_gitlink_node(10, 20, parent.id)
    assert node.kind == "gitlink"
    assert node.title == "Gitlink"
    assert node.gitlink_repo == ""
    assert node.gitlink_change_state == "draft"
    assert any(e.source == parent.id and e.target == node.id for e in doc.edges.values())


def test_add_gitlink_node_requires_a_parent_id():
    # Same TypeError-not-SceneError posture as every other required-parent
    # node kind (document/thinking/html/image/conversation/web_research/
    # artifact) - parent_id has no default in add_gitlink_node's signature.
    doc = SceneDocument()
    with pytest.raises(TypeError):
        doc.add_gitlink_node(0, 0)


def test_gitlink_node_deletion_goes_through_the_generic_remove_nodes_path():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(10, 10, parent.id)
    assert not hasattr(doc, "delete_gitlink_node"), (
        "gitlink nodes are not branch points - no special delete method"
    )
    doc.remove_nodes([node.id])
    assert node.id not in doc.nodes


def test_set_gitlink_local_root_sets_the_field():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    returned = doc.set_gitlink_local_root(node.id, "C:/checkout/repo")
    assert returned is node
    assert node.gitlink_local_root == "C:/checkout/repo"


def test_set_gitlink_local_root_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().set_gitlink_local_root("ghost", "C:/checkout")


def test_store_gitlink_repo_tree():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    returned = doc.store_gitlink_repo_tree(node.id, "octocat/hello-world", "main", ["a.py", "b.py"])
    assert returned is node
    assert node.gitlink_repo == "octocat/hello-world"
    assert node.gitlink_branch == "main"
    assert node.gitlink_repo_file_paths == ["a.py", "b.py"]


def test_store_gitlink_repo_tree_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().store_gitlink_repo_tree("ghost", "o/r", "main", [])


def test_store_gitlink_snapshot_root_sets_repo_branch_local_root_and_imported_root():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    returned = doc.store_gitlink_snapshot_root(node.id, "octocat/hello-world", "main", "C:/tmp/snapshot")
    assert returned is node
    assert node.gitlink_repo == "octocat/hello-world"
    assert node.gitlink_branch == "main"
    assert node.gitlink_local_root == "C:/tmp/snapshot"
    assert node.gitlink_imported_root == "C:/tmp/snapshot"


def test_store_gitlink_context_excluded_from_scene_payload():
    # Security/cost-boundary test: gitlinkContextXml must NEVER appear in
    # scene_payload() (see the field's own comment on SceneNode), while
    # gitlinkContextSummary/gitlinkContextStats - small, cheap status-badge
    # fields - ARE present.
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    huge_xml = "<gitlink_context>" + ("x" * 5000) + "</gitlink_context>"

    doc.store_gitlink_context(
        node.id,
        scope_mode="selected",
        selected_paths=["a.py"],
        context_xml=huge_xml,
        context_stats={"scanned_files": 3, "loaded_files": 3, "source_root": "github"},
        context_summary="Scanned 3 files.",
    )

    assert node.gitlink_context_xml == huge_xml, "the domain object DOES hold the full text"

    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]
    assert "gitlinkContextXml" not in row
    assert row["gitlinkContextSummary"] == "Scanned 3 files."
    assert row["gitlinkScopeMode"] == "selected"
    assert row["gitlinkSelectedPaths"] == ["a.py"]
    # context_stats' mixed int/str values are stringified end to end.
    assert row["gitlinkContextStats"] == {
        "scanned_files": "3", "loaded_files": "3", "source_root": "github",
    }


def test_store_gitlink_context_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().store_gitlink_context(
            "ghost", scope_mode="selected", selected_paths=[], context_xml="",
            context_stats={}, context_summary="",
        )


def test_store_gitlink_context_increments_version_even_with_an_identical_summary():
    """R5.3 post-review FIX 6: gitlink_context_version is a genuine
    MONOTONIC counter, incremented unconditionally on every call - unlike
    gitlink_context_summary (built purely from aggregate file counts, see
    repository.py's build_context_bundle), which can collide across two
    DIFFERENT Build Context results (e.g. selecting a different single file
    each time produces the exact same "Scanned 1 files." summary). Two
    successive store_gitlink_context calls for the SAME node, with
    IDENTICAL summary/stats (same file count from two different
    single-file selections), must still produce two DIFFERENT version
    values - this is what lets the frontend's lazy-fetch-once guard detect
    the second build actually happened."""
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    assert node.gitlink_context_version == 0, "a fresh node starts at 0"

    doc.store_gitlink_context(
        node.id,
        scope_mode="selected",
        selected_paths=["a.py"],
        context_xml="<gitlink_context>a.py content</gitlink_context>",
        context_stats={"scanned_files": 1, "loaded_files": 1},
        context_summary="Scanned 1 files.",
    )
    assert node.gitlink_context_version == 1

    # A second, genuinely DIFFERENT Build Context result (a different single
    # file selected) that happens to produce an IDENTICAL summary string.
    doc.store_gitlink_context(
        node.id,
        scope_mode="selected",
        selected_paths=["b.py"],
        context_xml="<gitlink_context>b.py content</gitlink_context>",
        context_stats={"scanned_files": 1, "loaded_files": 1},
        context_summary="Scanned 1 files.",
    )
    assert node.gitlink_context_version == 2, (
        "the version must increase even when the summary string is identical to the prior build"
    )
    assert node.gitlink_context_xml == "<gitlink_context>b.py content</gitlink_context>", (
        "the second build's own content must have actually landed"
    )

    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]
    assert row["gitlinkContextVersion"] == 2, "scene_payload() must reflect the current version"


def test_fetch_gitlink_context_xml_returns_full_text_not_in_payload():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    doc.store_gitlink_context(
        node.id, scope_mode="full", selected_paths=[], context_xml="<gitlink_context>full text</gitlink_context>",
        context_stats={}, context_summary="done",
    )

    fetched = doc.fetch_gitlink_context_xml(node.id)

    assert fetched == "<gitlink_context>full text</gitlink_context>"
    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]
    assert "gitlinkContextXml" not in row


def test_fetch_gitlink_context_xml_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().fetch_gitlink_context_xml("ghost")


def test_start_gitlink_run_sets_task_prompt_and_clears_error():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    node.gitlink_error = "a previous error"

    returned = doc.start_gitlink_run(node.id, "add a health check endpoint")

    assert returned is node
    assert node.gitlink_task_prompt == "add a health check endpoint"
    assert node.gitlink_error == ""


def test_start_gitlink_run_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().start_gitlink_run("ghost", "do something")


def test_start_gitlink_run_wrong_kind_raises_scene_error():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "not a gitlink node", True)
    with pytest.raises(SceneError):
        doc.start_gitlink_run(chat.id, "do something")


def test_complete_gitlink_run_no_changes_stays_draft():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)

    returned = doc.complete_gitlink_run(node.id, "## Gitlink Proposal\n\nNo changes needed.", [], "", None, "")

    assert returned is node
    assert node.gitlink_proposal_markdown == "## Gitlink Proposal\n\nNo changes needed."
    assert node.gitlink_pending_changes == []
    assert node.gitlink_change_state == "draft"
    assert node.gitlink_change_fingerprint is None
    assert node.gitlink_change_local_root is None, "an empty proposal must never leave a dangling local_root binding"


def test_complete_gitlink_run_with_changes_becomes_previewed_with_fingerprint():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    changes = [{"path": "a.py", "operation": "update", "reason": "fix bug", "content": "x = 1"}]

    returned = doc.complete_gitlink_run(
        node.id, "## Gitlink Proposal", changes, "diff text", "abc123fingerprint", "C:/checkout/repo",
    )

    assert returned is node
    assert node.gitlink_pending_changes == changes
    assert node.gitlink_preview_text == "diff text"
    assert node.gitlink_change_state == "previewed"
    assert node.gitlink_change_fingerprint == "abc123fingerprint"
    assert node.gitlink_change_local_root == "C:/checkout/repo", (
        "R5.3 post-review FIX 2: the local_root this run used must be bound to the approval"
    )


def test_complete_gitlink_run_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().complete_gitlink_run("ghost", "proposal", [], "", None, "")


def test_fail_gitlink_run_sets_error_and_is_a_silent_noop_for_a_deleted_node():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)

    returned = doc.fail_gitlink_run(node.id, "Gitlink generation failed: boom")
    assert returned is node
    assert node.gitlink_error == "Gitlink generation failed: boom"

    assert doc.fail_gitlink_run("ghost", "too late") is None


def test_fail_gitlink_run_does_not_clear_a_previously_staged_proposal():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    doc.complete_gitlink_run(node.id, "## Gitlink Proposal", changes, "diff", "fp1", "C:/checkout")

    doc.fail_gitlink_run(node.id, "a later re-run failed")

    assert node.gitlink_pending_changes == changes, "a failed re-run must not wipe a valid staged proposal"
    assert node.gitlink_change_state == "previewed"
    assert node.gitlink_change_fingerprint == "fp1"
    assert node.gitlink_error == "a later re-run failed"


def test_complete_gitlink_apply_sets_applied_and_clears_error():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    node.gitlink_change_state = "applying"
    node.gitlink_error = "stale"

    returned = doc.complete_gitlink_apply(node.id, 2)

    assert returned is node
    assert node.gitlink_change_state == "applied"
    assert node.gitlink_error == ""


def test_complete_gitlink_apply_clears_pending_changes_and_fingerprint_to_prevent_replay():
    """R5.3 post-review FIX 1 (CRITICAL): a successful Apply must invalidate
    the approval it just consumed - otherwise start_gitlink_apply's own
    fingerprint check would still pass on a second, replayed call, since
    nothing about the (already-applied) content changed. proposal_markdown/
    preview_text stay intact as a historical record of what was applied."""
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    doc.complete_gitlink_run(node.id, "## Gitlink Proposal", changes, "diff text", "fp1", "C:/checkout")
    node.gitlink_change_state = "applying"

    returned = doc.complete_gitlink_apply(node.id, 1)

    assert returned is node
    assert node.gitlink_change_state == "applied"
    assert node.gitlink_pending_changes == [], "the approval must be cleared so it cannot be replayed"
    assert node.gitlink_change_fingerprint is None
    assert node.gitlink_change_local_root is None, "a cleared approval must have no dangling bound fields"
    # Historical record of what was applied - deliberately left untouched.
    assert node.gitlink_proposal_markdown == "## Gitlink Proposal"
    assert node.gitlink_preview_text == "diff text"


def test_complete_gitlink_apply_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().complete_gitlink_apply("ghost", 1)


def test_fail_gitlink_apply_reverts_to_previewed_and_clears_fingerprint():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    doc.complete_gitlink_run(node.id, "## Gitlink Proposal", changes, "diff", "fp1", "C:/checkout")
    node.gitlink_change_state = "applying"

    returned = doc.fail_gitlink_apply(
        node.id, "The proposed change set changed after approval. Review it again before applying."
    )

    assert returned is node
    assert node.gitlink_change_state == "previewed"
    assert node.gitlink_change_fingerprint is None, "a stale approval must never be replayable"
    assert node.gitlink_change_local_root is None, "a cleared approval must have no dangling bound fields"
    assert node.gitlink_error == (
        "The proposed change set changed after approval. Review it again before applying."
    )
    # pending_changes/proposal_markdown themselves stay put - only the
    # fingerprint is invalidated, so the user can review and re-approve.
    assert node.gitlink_pending_changes == changes


def test_fail_gitlink_apply_is_a_silent_noop_for_a_deleted_node():
    assert SceneDocument().fail_gitlink_apply("ghost", "too late") is None


def test_scene_payload_gitlink_fields_default_correctly_for_a_fresh_node():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)

    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]

    assert row["gitlinkRepo"] == ""
    assert row["gitlinkBranch"] == ""
    assert row["gitlinkScopeMode"] == "selected"
    assert row["gitlinkLocalRoot"] == ""
    assert row["gitlinkRepoFilePaths"] == []
    assert row["gitlinkSelectedPaths"] == []
    assert row["gitlinkTaskPrompt"] == ""
    assert row["gitlinkContextStats"] == {}
    assert row["gitlinkContextSummary"] == ""
    # R5.3 post-review FIX 6: a fresh node's monotonic counter starts at 0.
    assert row["gitlinkContextVersion"] == 0
    assert row["gitlinkProposalMarkdown"] == ""
    assert row["gitlinkPendingChanges"] == []
    assert row["gitlinkPreviewText"] == ""
    assert row["gitlinkChangeFingerprint"] is None
    assert row["gitlinkChangeState"] == "draft"
    assert row["gitlinkError"] == ""
    # R5.3 post-review FIX 2: gitlink_change_local_root is plain internal
    # backend bookkeeping, like gitlink_context_xml/gitlink_imported_root -
    # it must NEVER appear on the wire.
    assert "gitlinkChangeLocalRoot" not in row


def test_scene_payload_gitlink_pending_changes_is_a_copy_not_the_live_list():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    doc.complete_gitlink_run(node.id, "proposal", changes, "diff", "fp1", "C:/checkout")

    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]
    row["gitlinkPendingChanges"][0]["path"] = "mutated.py"

    assert node.gitlink_pending_changes[0]["path"] == "a.py", (
        "mutating the payload copy must never mutate the live domain state"
    )


# -- R5.3: gitlink node - WS-intent level -------------------------------------


def _make_gitlink_plugins_bus():
    bus = SessionBus("gitlink-canvas-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    composer_document = ComposerDocument()
    bus.register_topic("app-composer", composer_document.payload)

    class _FakeDispatcher:
        pass

    fake_dispatcher = _FakeDispatcher()
    document = register_canvas(bus, notifications, fake_dispatcher, composer_document)
    return bus, notifications, document, fake_dispatcher


def test_set_gitlink_local_root_intent_publishes_scene():
    async def run():
        bus, notifications, document, _dispatcher = _make_gitlink_plugins_bus()
        parent = document.add_node(0, 0, "parent")
        node = document.add_gitlink_node(0, 0, parent.id)

        await bus.dispatch_intent("scene", "setGitlinkLocalRoot", [node.id, "C:/checkout"])

        assert document.nodes[node.id].gitlink_local_root == "C:/checkout"

    asyncio.run(run())


def test_fetch_gitlink_context_intent_returns_full_text():
    async def run():
        bus, notifications, document, _dispatcher = _make_gitlink_plugins_bus()
        parent = document.add_node(0, 0, "parent")
        node = document.add_gitlink_node(0, 0, parent.id)
        document.store_gitlink_context(
            node.id, scope_mode="selected", selected_paths=["a.py"],
            context_xml="<gitlink_context>full</gitlink_context>", context_stats={}, context_summary="s",
        )

        result = await bus.dispatch_intent("scene", "fetchGitlinkContext", [node.id])

        assert result == "<gitlink_context>full</gitlink_context>"

    asyncio.run(run())


def test_fetch_gitlink_repositories_intent_busy_guard_returns_empty_list_without_calling_dispatcher():
    class _FakeDispatcher:
        def __init__(self):
            self.called = False

        async def fetch_gitlink_repositories(self, **kwargs):
            self.called = True
            return ["should-not-be-reached/repo"]

    async def run():
        bus = SessionBus("gitlink-busy-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        document = register_canvas(bus, notifications, fake_dispatcher, composer_document)
        parent = document.add_node(0, 0, "parent")
        node = document.add_gitlink_node(0, 0, parent.id)
        node.pending_request_id = "already-busy"

        result = await bus.dispatch_intent("scene", "fetchGitlinkRepositories", [node.id])

        assert result == []
        assert fake_dispatcher.called is False
        assert notifications.visible is True
        assert notifications.msg_type == "info"

    asyncio.run(run())


def test_run_gitlink_change_set_intent_dispatches_with_correct_node_fields():
    class _FakeDispatcher:
        def __init__(self):
            self.calls = []

        async def start_gitlink_run(self, **kwargs):
            self.calls.append(kwargs)

    async def run():
        bus = SessionBus("gitlink-run-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        document = register_canvas(bus, notifications, fake_dispatcher, composer_document)
        parent = document.add_node(0, 0, "parent")
        node = document.add_gitlink_node(0, 0, parent.id)
        document.store_gitlink_repo_tree(node.id, "octocat/hello-world", "main", ["a.py"])
        document.store_gitlink_context(
            node.id, scope_mode="selected", selected_paths=["a.py"], context_xml="<x/>",
            context_stats={}, context_summary="s",
        )

        result = await bus.dispatch_intent("scene", "runGitlinkChangeSet", [node.id, "add a feature"])

        assert result == node.id
        assert document.nodes[node.id].gitlink_task_prompt == "add a feature"
        assert len(fake_dispatcher.calls) == 1
        call = fake_dispatcher.calls[0]
        assert call["repo"] == "octocat/hello-world"
        assert call["branch"] == "main"
        assert call["scope_mode"] == "selected"
        assert call["task_prompt"] == "add a feature"
        assert call["context_xml"] == "<x/>"
        assert call["context_summary"] == "s"
        assert callable(call["on_success"])
        assert callable(call["on_failure"])

    asyncio.run(run())


def test_apply_gitlink_changes_intent_calls_dispatcher_with_only_node_id_and_fingerprint():
    class _FakeDispatcher:
        def __init__(self):
            self.calls = []

        async def start_gitlink_apply(self, **kwargs):
            self.calls.append(kwargs)

    async def run():
        bus = SessionBus("gitlink-apply-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        document = register_canvas(bus, notifications, fake_dispatcher, composer_document)
        parent = document.add_node(0, 0, "parent")
        node = document.add_gitlink_node(0, 0, parent.id)
        document.set_gitlink_local_root(node.id, "C:/checkout")

        result = await bus.dispatch_intent("scene", "applyGitlinkChanges", [node.id, "fp-123"])

        assert result == node.id
        assert len(fake_dispatcher.calls) == 1
        call = fake_dispatcher.calls[0]
        assert call["client_fingerprint"] == "fp-123"
        assert call["local_root"] == "C:/checkout"
        assert callable(call["on_success"])
        assert callable(call["on_failure"])

    asyncio.run(run())


def test_cancel_gitlink_request_intent_calls_agent_dispatcher_cancel_gitlink():
    class _FakeDispatcher:
        def __init__(self):
            self.cancel_calls = []

        def cancel_gitlink(self, request_id):
            self.cancel_calls.append(request_id)
            return True

    async def run():
        bus = SessionBus("cancel-gitlink-request-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        register_canvas(bus, notifications, fake_dispatcher, composer_document)

        result = await bus.dispatch_intent("scene", "cancelGitlinkRequest", ["req-456"])

        assert result is None
        assert fake_dispatcher.cancel_calls == ["req-456"]

    asyncio.run(run())


# -- R5.3 post-review FIX 4: real concurrent interleaving, not a pre-set busy
# flag --------------------------------------------------------------------


def test_two_concurrent_run_gitlink_change_set_calls_for_the_same_node_only_one_reaches_the_agent(monkeypatch):
    """R5.3 post-review FIX 4(a)+(b): before this fix, node.pending_request_id
    was only claimed INSIDE start_gitlink_run's separately-scheduled _run()
    sub-task, and run_gitlink_change_set's own busy pre-check was separated
    from agents.py's claim by a real `await publish_scene()` - so two
    concurrent runGitlinkChangeSet calls for the SAME node could both pass
    every busy check before either one's claim ever landed, and both would
    reach the LLM.

    This drives TWO REAL coroutines through a genuine asyncio interleaving -
    both dispatch_intent(...) calls are fired via asyncio.gather without
    either being awaited to completion first, exactly the scenario the fix
    spec calls for - not the trivial pre-set-pending_request_id case the
    existing busy-guard tests already cover (e.g.
    test_gitlink_apply_busy_guard_blocks_concurrent_run_and_apply in
    test_agents.py). GitlinkAgent.get_response is reached via a REAL
    asyncio.to_thread hop (unmocked at that layer), which is itself a
    genuine yield point back to the event loop - so even a slow/blocking
    mock is unnecessary for real interleaving to occur here; the race this
    fix closes is entirely in the synchronous busy-check-and-claim ordering,
    which asyncio.gather's task scheduling exercises directly."""
    call_count = {"n": 0}

    def counting_get_response(self, payload):
        call_count["n"] += 1
        return {
            "summary": "s", "write_intent": "changes_ready", "rationale": "r", "notes": [],
            "files": [{"path": "a.py", "operation": "update", "reason": "x", "content": "y"}],
            "change_count": 1, "raw_response": "{}",
        }

    monkeypatch.setattr(agents_module.GitlinkAgent, "get_response", counting_get_response)

    async def run():
        bus, document, recorder, dispatcher = make_bus_with_dispatcher()
        parent = document.add_chat_node(0, 0, "root", True)
        node = document.add_gitlink_node(0, 0, parent.id)
        document.store_gitlink_repo_tree(node.id, "octocat/hello-world", "main", ["a.py"])
        document.store_gitlink_context(
            node.id, scope_mode="selected", selected_paths=["a.py"], context_xml="<x/>",
            context_stats={}, context_summary="s",
        )

        results = await asyncio.gather(
            bus.dispatch_intent("scene", "runGitlinkChangeSet", [node.id, "task A"]),
            bus.dispatch_intent("scene", "runGitlinkChangeSet", [node.id, "task B"]),
        )
        # The admitted call returns node_id (mirrors every other successful
        # scene-intent wrapper); the busy-rejected call returns None (mirrors
        # run_gitlink_change_set's own existing busy-rejection branch, same
        # sentinel shape as e.g. fetchGitlinkRepositories's busy guard).
        # Deterministic here: neither coroutine's own body has a genuine
        # suspension point before the busy claim, so asyncio's FIFO task
        # scheduling always lets the FIRST-created task ("task A") win.
        assert results == [node.id, None], "exactly one of the two concurrent calls must be admitted"

        entries = list(dispatcher._gitlink_requests.values())
        assert len(entries) == 1, "only ONE Run may ever be admitted for this node at a time"
        await entries[0]["task"]

        assert call_count["n"] == 1, "only ONE of the two concurrent calls may ever reach the LLM"
        assert document.nodes[node.id].gitlink_change_state == "previewed"
        assert dispatcher._gitlink_requests == {}
        assert document.nodes[node.id].pending_request_id is None, (
            "the busy slot must be fully released once the admitted Run finishes"
        )

    asyncio.run(run())


# -- R5.4: Py-Coder node ------------------------------------------------------


def test_add_pycoder_node_requires_valid_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_pycoder_node(0, 0, "ghost")


def test_add_pycoder_node_creates_child_with_defaults():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "wire up pycoder", True)
    node = doc.add_pycoder_node(10, 20, parent.id)
    assert node.kind == "pycoder"
    assert node.title == "Py-Coder"
    assert node.pycoder_mode == "ai_driven"
    assert node.pycoder_prompt == ""
    assert node.pycoder_code == ""
    assert node.pycoder_output == ""
    assert node.pycoder_analysis == ""
    assert node.pycoder_last_run_failed is False
    assert node.pycoder_awaiting_approval is False
    assert node.pycoder_error == ""
    assert any(e.source == parent.id and e.target == node.id for e in doc.edges.values())


def test_add_pycoder_node_requires_a_parent_id():
    doc = SceneDocument()
    with pytest.raises(TypeError):
        doc.add_pycoder_node(0, 0)


def test_pycoder_node_deletion_goes_through_the_generic_remove_nodes_path():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_pycoder_node(10, 10, parent.id)
    assert not hasattr(doc, "delete_pycoder_node"), (
        "pycoder nodes are not branch points - no special delete method"
    )
    doc.remove_nodes([node.id])
    assert node.id not in doc.nodes


def test_set_pycoder_mode_sets_the_field():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_pycoder_node(0, 0, parent.id)
    returned = doc.set_pycoder_mode(node.id, "manual")
    assert returned is node
    assert node.pycoder_mode == "manual"


def test_set_pycoder_mode_rejects_unknown_mode():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_pycoder_node(0, 0, parent.id)
    with pytest.raises(SceneError):
        doc.set_pycoder_mode(node.id, "turbo")


def test_set_pycoder_mode_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().set_pycoder_mode("ghost", "manual")


def test_start_pycoder_run_ai_driven_stores_prompt_and_clears_error():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_pycoder_node(0, 0, parent.id)
    node.pycoder_error = "a previous error"

    returned = doc.start_pycoder_run(node.id, "sort this list")

    assert returned is node
    assert node.pycoder_prompt == "sort this list"
    assert node.pycoder_error == ""


def test_start_pycoder_run_manual_stores_code_not_prompt():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_pycoder_node(0, 0, parent.id)
    doc.set_pycoder_mode(node.id, "manual")

    doc.start_pycoder_run(node.id, "print('hi')")

    assert node.pycoder_code == "print('hi')"
    assert node.pycoder_prompt == ""


def test_start_pycoder_run_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().start_pycoder_run("ghost", "x")


def test_start_pycoder_run_wrong_kind_raises_scene_error():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_gitlink_node(0, 0, parent.id)
    with pytest.raises(SceneError):
        doc.start_pycoder_run(node.id, "x")


def test_complete_pycoder_run_sets_all_fields_and_clears_approval_and_error():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_pycoder_node(0, 0, parent.id)
    node.pycoder_awaiting_approval = True
    node.pycoder_error = "stale error"

    returned = doc.complete_pycoder_run(node.id, "print(1)", "1", "analysis text", False)

    assert returned is node
    assert node.pycoder_code == "print(1)"
    assert node.pycoder_output == "1"
    assert node.pycoder_analysis == "analysis text"
    assert node.pycoder_last_run_failed is False
    assert node.pycoder_awaiting_approval is False
    assert node.pycoder_error == ""


def test_complete_pycoder_run_is_a_silent_noop_for_a_deleted_node():
    assert SceneDocument().complete_pycoder_run("ghost", "c", "o", "a", False) is None


def test_fail_pycoder_run_sets_error_clears_approval_but_preserves_output():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_pycoder_node(0, 0, parent.id)
    node.pycoder_code = "print(1)"
    node.pycoder_output = "1"
    node.pycoder_analysis = "old analysis"
    node.pycoder_awaiting_approval = True

    returned = doc.fail_pycoder_run(node.id, "execution timed out")

    assert returned is node
    assert node.pycoder_error == "execution timed out"
    assert node.pycoder_awaiting_approval is False
    # stale-while-revalidate: a failed run must not wipe out a previously
    # completed result.
    assert node.pycoder_code == "print(1)"
    assert node.pycoder_output == "1"
    assert node.pycoder_analysis == "old analysis"


def test_fail_pycoder_run_is_a_silent_noop_for_a_deleted_node():
    assert SceneDocument().fail_pycoder_run("ghost", "x") is None


def test_scene_payload_pycoder_fields_default_correctly_for_a_fresh_node():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_pycoder_node(0, 0, parent.id)
    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]
    assert row["pycoderMode"] == "ai_driven"
    assert row["pycoderPrompt"] == ""
    assert row["pycoderCode"] == ""
    assert row["pycoderOutput"] == ""
    assert row["pycoderAnalysis"] == ""
    assert row["pycoderLastRunFailed"] is False
    assert row["pycoderAwaitingApproval"] is False
    assert row["pycoderError"] == ""


# -- R5.4: Execution Sandbox node ---------------------------------------------


def test_add_code_sandbox_node_requires_valid_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_code_sandbox_node(0, 0, "ghost")


def test_add_code_sandbox_node_creates_child_with_defaults_and_mints_sandbox_id():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "wire up sandbox", True)
    node = doc.add_code_sandbox_node(10, 20, parent.id)
    assert node.kind == "code_sandbox"
    assert node.title == "Execution Sandbox"
    assert node.code_sandbox_sandbox_id, "a sandbox id must be minted at creation time"
    assert node.code_sandbox_requirements == ""
    assert node.code_sandbox_prompt == ""
    assert node.code_sandbox_code == ""
    assert node.code_sandbox_output == ""
    assert node.code_sandbox_analysis == ""
    assert node.code_sandbox_awaiting_approval is False
    assert node.code_sandbox_error == ""
    assert any(e.source == parent.id and e.target == node.id for e in doc.edges.values())


def test_add_code_sandbox_node_mints_a_different_id_per_node():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    a = doc.add_code_sandbox_node(0, 0, parent.id)
    b = doc.add_code_sandbox_node(0, 0, parent.id)
    assert a.code_sandbox_sandbox_id != b.code_sandbox_sandbox_id


def test_add_code_sandbox_node_requires_a_parent_id():
    doc = SceneDocument()
    with pytest.raises(TypeError):
        doc.add_code_sandbox_node(0, 0)


def test_code_sandbox_node_deletion_goes_through_the_generic_remove_nodes_path():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_code_sandbox_node(10, 10, parent.id)
    assert not hasattr(doc, "delete_code_sandbox_node")
    doc.remove_nodes([node.id])
    assert node.id not in doc.nodes


def test_set_code_sandbox_requirements_sets_the_field():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_code_sandbox_node(0, 0, parent.id)
    returned = doc.set_code_sandbox_requirements(node.id, "numpy\nrequests")
    assert returned is node
    assert node.code_sandbox_requirements == "numpy\nrequests"


def test_set_code_sandbox_requirements_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().set_code_sandbox_requirements("ghost", "numpy")


def test_start_code_sandbox_run_stores_prompt_and_clears_error_without_touching_code():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_code_sandbox_node(0, 0, parent.id)
    node.code_sandbox_code = "print('previous run')"
    node.code_sandbox_error = "a previous error"

    returned = doc.start_code_sandbox_run(node.id, "add a health check")

    assert returned is node
    assert node.code_sandbox_prompt == "add a health check"
    assert node.code_sandbox_error == ""
    assert node.code_sandbox_code == "print('previous run')", (
        "start_code_sandbox_run must not overwrite the existing code - the "
        "dispatch method decides generate-vs-reuse by reading it at call time"
    )


def test_start_code_sandbox_run_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().start_code_sandbox_run("ghost", "x")


def test_start_code_sandbox_run_wrong_kind_raises_scene_error():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_pycoder_node(0, 0, parent.id)
    with pytest.raises(SceneError):
        doc.start_code_sandbox_run(node.id, "x")


def test_complete_code_sandbox_run_sets_all_fields_and_clears_approval_and_error():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_code_sandbox_node(0, 0, parent.id)
    node.code_sandbox_awaiting_approval = True
    node.code_sandbox_approval_requirements = "numpy"
    node.code_sandbox_error = "stale error"

    returned = doc.complete_code_sandbox_run(node.id, "print(1)", "1", "analysis text")

    assert returned is node
    assert node.code_sandbox_code == "print(1)"
    assert node.code_sandbox_output == "1"
    assert node.code_sandbox_analysis == "analysis text"
    assert node.code_sandbox_awaiting_approval is False
    assert node.code_sandbox_approval_requirements == "", (
        "the frozen approval snapshot must be cleared alongside awaiting_approval"
    )
    assert node.code_sandbox_error == ""


def test_complete_code_sandbox_run_is_a_silent_noop_for_a_deleted_node():
    assert SceneDocument().complete_code_sandbox_run("ghost", "c", "o", "a") is None


def test_fail_code_sandbox_run_sets_error_clears_approval_but_preserves_output():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_code_sandbox_node(0, 0, parent.id)
    node.code_sandbox_code = "print(1)"
    node.code_sandbox_output = "1"
    node.code_sandbox_analysis = "old analysis"
    node.code_sandbox_awaiting_approval = True
    node.code_sandbox_approval_requirements = "numpy"

    returned = doc.fail_code_sandbox_run(node.id, "sandbox timed out")

    assert returned is node
    assert node.code_sandbox_error == "sandbox timed out"
    assert node.code_sandbox_awaiting_approval is False
    assert node.code_sandbox_approval_requirements == "", (
        "the frozen approval snapshot must be cleared alongside awaiting_approval"
    )
    assert node.code_sandbox_code == "print(1)"
    assert node.code_sandbox_output == "1"
    assert node.code_sandbox_analysis == "old analysis"


def test_fail_code_sandbox_run_is_a_silent_noop_for_a_deleted_node():
    assert SceneDocument().fail_code_sandbox_run("ghost", "x") is None


def test_scene_payload_code_sandbox_fields_default_correctly_and_excludes_sandbox_id():
    doc = SceneDocument()
    parent = doc.add_node(0, 0, "parent")
    node = doc.add_code_sandbox_node(0, 0, parent.id)
    row = {n["id"]: n for n in doc.scene_payload()["nodes"]}[node.id]
    assert "codeSandboxSandboxId" not in row, (
        "the internal sandbox directory-naming key must never reach the wire"
    )
    assert row["codeSandboxRequirements"] == ""
    assert row["codeSandboxPrompt"] == ""
    assert row["codeSandboxCode"] == ""
    assert row["codeSandboxOutput"] == ""
    assert row["codeSandboxAnalysis"] == ""
    assert row["codeSandboxAwaitingApproval"] is False
    assert row["codeSandboxApprovalRequirements"] == ""
    assert row["codeSandboxError"] == ""


# -- R5.4: Py-Coder / Execution Sandbox - WS-intent level ---------------------


def test_set_pycoder_mode_intent_publishes_scene():
    async def run():
        bus, document, recorder, _dispatcher = make_bus_with_dispatcher()
        parent = document.add_node(0, 0, "parent")
        node = document.add_pycoder_node(0, 0, parent.id)

        await bus.dispatch_intent("scene", "setPyCoderMode", [node.id, "manual"])

        assert document.nodes[node.id].pycoder_mode == "manual"

    asyncio.run(run())


def test_run_pycoder_intent_busy_node_refuses_without_calling_dispatcher():
    class _FakeDispatcher:
        def __init__(self):
            self.called = False

        async def start_pycoder_run(self, **kwargs):
            self.called = True

    async def run():
        bus = SessionBus("pycoder-busy-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        document = register_canvas(bus, notifications, fake_dispatcher, composer_document)
        parent = document.add_node(0, 0, "parent")
        node = document.add_pycoder_node(0, 0, parent.id)
        node.pending_request_id = "already-busy"

        result = await bus.dispatch_intent("scene", "runPyCoder", [node.id, "sort this"])

        assert result is None
        assert fake_dispatcher.called is False
        assert notifications.visible is True
        assert notifications.msg_type == "info"

    asyncio.run(run())


def test_run_pycoder_intent_dispatches_with_correct_node_fields():
    class _FakeDispatcher:
        def __init__(self):
            self.calls = []

        async def start_pycoder_run(self, **kwargs):
            self.calls.append(kwargs)

    async def run():
        bus = SessionBus("pycoder-run-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        document = register_canvas(bus, notifications, fake_dispatcher, composer_document)
        parent = document.add_node(0, 0, "parent")
        node = document.add_pycoder_node(0, 0, parent.id)

        result = await bus.dispatch_intent("scene", "runPyCoder", [node.id, "sort this list"])

        assert result == node.id
        assert document.nodes[node.id].pycoder_prompt == "sort this list"
        assert len(fake_dispatcher.calls) == 1
        call = fake_dispatcher.calls[0]
        assert call["mode"] == "ai_driven"
        assert call["prompt"] == "sort this list"
        assert callable(call["on_success"])
        assert callable(call["on_failure"])

    asyncio.run(run())


def test_cancel_pycoder_request_intent_calls_agent_dispatcher_cancel_pycoder():
    class _FakeDispatcher:
        def __init__(self):
            self.cancel_calls = []

        def cancel_pycoder(self, request_id):
            self.cancel_calls.append(request_id)
            return True

    async def run():
        bus = SessionBus("cancel-pycoder-request-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        register_canvas(bus, notifications, fake_dispatcher, composer_document)

        result = await bus.dispatch_intent("scene", "cancelPyCoderRequest", ["req-789"])

        assert result is None
        assert fake_dispatcher.cancel_calls == ["req-789"]

    asyncio.run(run())


def test_run_code_sandbox_intent_dispatches_with_correct_node_fields():
    class _FakeDispatcher:
        def __init__(self):
            self.calls = []

        async def start_code_sandbox_run(self, **kwargs):
            self.calls.append(kwargs)

    async def run():
        bus = SessionBus("sandbox-run-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        document = register_canvas(bus, notifications, fake_dispatcher, composer_document)
        parent = document.add_node(0, 0, "parent")
        node = document.add_code_sandbox_node(0, 0, parent.id)

        result = await bus.dispatch_intent("scene", "runCodeSandbox", [node.id, "generate a health check"])

        assert result == node.id
        assert document.nodes[node.id].code_sandbox_prompt == "generate a health check"
        assert len(fake_dispatcher.calls) == 1
        call = fake_dispatcher.calls[0]
        assert call["sandbox_id"] == document.nodes[node.id].code_sandbox_sandbox_id
        assert call["prompt"] == "generate a health check"
        assert call["existing_code"] == ""
        assert callable(call["on_success"])
        assert callable(call["on_failure"])

    asyncio.run(run())


def test_run_code_sandbox_intent_busy_node_refuses_without_calling_dispatcher():
    class _FakeDispatcher:
        def __init__(self):
            self.called = False

        async def start_code_sandbox_run(self, **kwargs):
            self.called = True

    async def run():
        bus = SessionBus("sandbox-busy-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        document = register_canvas(bus, notifications, fake_dispatcher, composer_document)
        parent = document.add_node(0, 0, "parent")
        node = document.add_code_sandbox_node(0, 0, parent.id)
        node.pending_request_id = "already-busy"

        result = await bus.dispatch_intent("scene", "runCodeSandbox", [node.id, "x"])

        assert result is None
        assert fake_dispatcher.called is False
        assert notifications.visible is True
        assert notifications.msg_type == "info"

    asyncio.run(run())


def test_cancel_code_sandbox_request_intent_calls_agent_dispatcher_cancel_code_sandbox():
    class _FakeDispatcher:
        def __init__(self):
            self.cancel_calls = []

        def cancel_code_sandbox(self, request_id):
            self.cancel_calls.append(request_id)
            return True

    async def run():
        bus = SessionBus("cancel-sandbox-request-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        register_canvas(bus, notifications, fake_dispatcher, composer_document)

        result = await bus.dispatch_intent("scene", "cancelCodeSandboxRequest", ["req-321"])

        assert result is None
        assert fake_dispatcher.cancel_calls == ["req-321"]

    asyncio.run(run())


def test_approve_code_execution_intent_calls_agent_dispatcher_approve():
    class _FakeDispatcher:
        def __init__(self):
            self.calls = []

        def approve_code_execution(self, request_id):
            self.calls.append(request_id)
            return True

    async def run():
        bus = SessionBus("approve-code-execution-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        register_canvas(bus, notifications, fake_dispatcher, composer_document)

        result = await bus.dispatch_intent("scene", "approveCodeExecution", ["req-abc"])

        assert result is None
        assert fake_dispatcher.calls == ["req-abc"]

    asyncio.run(run())


def test_deny_code_execution_intent_calls_agent_dispatcher_deny():
    class _FakeDispatcher:
        def __init__(self):
            self.calls = []

        def deny_code_execution(self, request_id):
            self.calls.append(request_id)
            return True

    async def run():
        bus = SessionBus("deny-code-execution-intent-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        fake_dispatcher = _FakeDispatcher()
        register_canvas(bus, notifications, fake_dispatcher, composer_document)

        result = await bus.dispatch_intent("scene", "denyCodeExecution", ["req-def"])

        assert result is None
        assert fake_dispatcher.calls == ["req-def"]

    asyncio.run(run())


def test_remove_nodes_disposes_the_repl_for_every_deleted_pycoder_node():
    """R5.4: a deleted Py-Coder node's REPL subprocess must not outlive it -
    uses the REAL AgentDispatcher (make_bus_with_dispatcher) so this proves
    the actual dispose_pycoder_repl wiring, not a mocked stand-in."""
    async def run():
        bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
        parent = document.add_node(0, 0, "parent")
        pycoder_node = document.add_pycoder_node(0, 0, parent.id)
        other_node = document.add_gitlink_node(0, 0, parent.id)
        # Populate a REPL for this node id, so there is something real to
        # tear down.
        repl = dispatcher.get_pycoder_repl(pycoder_node.id)
        assert pycoder_node.id in dispatcher._pycoder_repls

        await bus.dispatch_intent("scene", "removeNodes", [[pycoder_node.id, other_node.id]])

        assert pycoder_node.id not in document.nodes
        assert other_node.id not in document.nodes
        assert pycoder_node.id not in dispatcher._pycoder_repls, (
            "the REPL must be disposed once its owning pycoder node is deleted"
        )

    asyncio.run(run())


def test_remove_nodes_does_not_touch_the_repl_dict_when_no_pycoder_node_is_deleted():
    async def run():
        bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
        parent = document.add_node(0, 0, "parent")
        pycoder_node = document.add_pycoder_node(0, 0, parent.id)
        other_node = document.add_gitlink_node(0, 0, parent.id)
        dispatcher.get_pycoder_repl(pycoder_node.id)

        await bus.dispatch_intent("scene", "removeNodes", [[other_node.id]])

        assert other_node.id not in document.nodes
        assert pycoder_node.id in document.nodes
        assert pycoder_node.id in dispatcher._pycoder_repls, (
            "a still-live pycoder node's REPL must not be disposed just "
            "because a DIFFERENT node was deleted"
        )

    asyncio.run(run())


def test_remove_nodes_cancels_the_dispatchers_pycoder_request_when_deleted_mid_approval_pause(monkeypatch):
    """R5.4 post-review FIX 2: dispose_pycoder_repl (proven above) only tears
    down the REPL subprocess - it does nothing about a request genuinely
    parked on `await approval_future` in AgentDispatcher._pycoder_requests,
    which has NO timeout by design (the whole point is "wait for a human,
    however long that takes"). Deleting the node mid-pause must ALSO
    resolve/cancel that dispatcher-side request (mirrors a manual Cancel
    exactly - see AgentDispatcher.cancel_pycoder), closing the orphan window
    completely rather than leaving the future - and the asyncio.Task awaiting
    it - alive forever."""
    monkeypatch.setattr(
        agents_module.PyCoderExecutionAgent, "get_response",
        lambda self, history, prompt: "[TOOL:PYTHON]\nprint(1)\n[/TOOL]",
    )

    async def run():
        bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
        parent = document.add_node(0, 0, "parent")
        pycoder_node = document.add_pycoder_node(0, 0, parent.id)

        await bus.dispatch_intent("scene", "runPyCoder", [pycoder_node.id, "do something"])
        request_id, entry = next(iter(dispatcher._pycoder_requests.items()))

        # Let the pipeline genuinely reach the approval gate (a real
        # asyncio.to_thread hop for PyCoderExecutionAgent.get_response, then a
        # real `await approval_future` with nothing else ever resolving it) -
        # same polling idiom test_agents.py's own
        # test_code_sandbox_blank_prompt_with_existing_code_reuses_it_...
        # uses for the equivalent code_sandbox gate.
        for _ in range(200):
            if pycoder_node.pycoder_awaiting_approval or entry["task"].done():
                break
            await asyncio.sleep(0.005)
        assert pycoder_node.pycoder_awaiting_approval is True, "must genuinely be parked on the approval gate"
        assert request_id in dispatcher._pycoder_requests

        await bus.dispatch_intent("scene", "removeNodes", [[pycoder_node.id]])
        await entry["task"]

        assert pycoder_node.id not in document.nodes
        assert dispatcher._pycoder_requests == {}, (
            "the orphaned dispatcher-side request must be resolved and popped - "
            "not left parked on approval_future forever"
        )
        assert entry["task"].done(), "the background task must actually complete, not hang"

    asyncio.run(run())


def test_remove_nodes_cancels_the_dispatchers_code_sandbox_request_when_deleted_mid_approval_pause(monkeypatch):
    """R5.4 post-review FIX 2's Execution Sandbox twin - mirrors the pycoder
    test above exactly (same race, same fix, same asserted outcome)."""
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint(1)\n[/TOOL]",
    )

    async def run():
        bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
        parent = document.add_node(0, 0, "parent")
        sandbox_node = document.add_code_sandbox_node(0, 0, parent.id)

        await bus.dispatch_intent("scene", "runCodeSandbox", [sandbox_node.id, "do something"])
        request_id, entry = next(iter(dispatcher._code_sandbox_requests.items()))

        for _ in range(200):
            if sandbox_node.code_sandbox_awaiting_approval or entry["task"].done():
                break
            await asyncio.sleep(0.005)
        assert sandbox_node.code_sandbox_awaiting_approval is True, "must genuinely be parked on the approval gate"
        assert request_id in dispatcher._code_sandbox_requests

        await bus.dispatch_intent("scene", "removeNodes", [[sandbox_node.id]])
        await entry["task"]

        assert sandbox_node.id not in document.nodes
        assert dispatcher._code_sandbox_requests == {}, (
            "the orphaned dispatcher-side request must be resolved and popped - "
            "not left parked on approval_future forever"
        )
        assert entry["task"].done(), "the background task must actually complete, not hang"

    asyncio.run(run())


def test_code_sandbox_approval_requirements_snapshot_is_decoupled_from_the_live_draft_field(monkeypatch):
    """R5.4 CODESANDBOX FIX: closes the requirements-disclosure staleness
    race. AgentDispatcher.start_code_sandbox_run (backend/agents.py) reads
    requirements_manifest synchronously into a local `manifest` variable at
    the very top of its own _run(), BEFORE its one real await (the
    asyncio.to_thread call to SandboxGenerationAgent). A setCodeSandboxRequirements
    intent - ungated by any busy check - can land during that await window
    and change the LIVE node.code_sandbox_requirements field before the
    approval panel is ever shown. Uses the REAL AgentDispatcher
    (make_bus_with_dispatcher) driven through the real runCodeSandbox/
    setCodeSandboxRequirements WS intents, and genuinely parks on the
    approval gate (same polling idiom as
    test_remove_nodes_cancels_the_dispatchers_code_sandbox_request_when_deleted_mid_approval_pause
    above), proving node.code_sandbox_approval_requirements - the frozen
    snapshot this pending approval genuinely refers to - stays "numpy" even
    after a fresh setCodeSandboxRequirements changes the live draft field to
    "requests"."""
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint(1)\n[/TOOL]",
    )

    async def run():
        bus, document, _recorder, dispatcher = make_bus_with_dispatcher()
        parent = document.add_node(0, 0, "parent")
        sandbox_node = document.add_code_sandbox_node(0, 0, parent.id)

        await bus.dispatch_intent("scene", "setCodeSandboxRequirements", [sandbox_node.id, "numpy"])

        await bus.dispatch_intent("scene", "runCodeSandbox", [sandbox_node.id, "do something"])
        request_id, entry = next(iter(dispatcher._code_sandbox_requests.items()))

        for _ in range(200):
            if sandbox_node.code_sandbox_awaiting_approval or entry["task"].done():
                break
            await asyncio.sleep(0.005)
        assert sandbox_node.code_sandbox_awaiting_approval is True, "must genuinely be parked on the approval gate"
        assert sandbox_node.code_sandbox_approval_requirements == "numpy", (
            "the frozen snapshot must be populated the instant the approval gate opens"
        )

        # A fresh live-draft edit arrives WHILE this approval is still
        # pending - exactly the real race this fix closes.
        await bus.dispatch_intent("scene", "setCodeSandboxRequirements", [sandbox_node.id, "requests"])

        assert sandbox_node.code_sandbox_approval_requirements == "numpy", (
            "the disclosed approval snapshot must stay the frozen manifest this "
            "pending approval genuinely refers to, unaffected by a later live edit"
        )
        assert sandbox_node.code_sandbox_requirements == "requests", (
            "the live draft field must still reflect the user's newest edit, "
            "proving the two fields are genuinely decoupled"
        )

        # Deny, so the background task completes cleanly without touching a
        # real virtualenv.
        assert dispatcher.deny_code_execution(request_id) is True
        await entry["task"]
        assert sandbox_node.code_sandbox_approval_requirements == "", (
            "must be cleared once the approval resolves, mirroring "
            "code_sandbox_awaiting_approval's own clear"
        )

    asyncio.run(run())
