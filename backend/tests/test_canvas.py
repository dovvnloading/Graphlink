"""Canvas domain tests (Qt-removal plan R1): scene document invariants,
intent surface, grid payload compatibility with the generated validator's
shape, and snapshot publishing."""

import asyncio
import base64
from unittest.mock import patch

import pytest

# Importing any backend.* submodule runs backend/__init__.py first, which
# puts graphlink_app/ on sys.path - these must come before the bare
# top-level graphlink_app imports below (api_provider, graphlink_task_config)
# for this module to import cleanly when run standalone, not just as part of
# a larger session where some earlier-collected module already did this.
from backend.agents import AgentDispatcher
from backend.canvas import (
    DRAG_FACTOR_MAX,
    DRAG_FACTOR_MIN,
    SceneDocument,
    SceneError,
    register_canvas,
)
from backend.composer import ComposerDocument
from backend.events import SessionBus
from backend.notifications import NotificationState

import api_provider
import graphlink_task_config as task_config


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


def test_send_conversation_message_intent_appends_and_fires_the_same_deferred_notice():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addNode", [0, 0, "parent"])
        node_id = await bus.dispatch_intent("scene", "addConversationNode", [10, 10, parent_id])

        returned_id = await bus.dispatch_intent(
            "scene", "sendConversationMessage", [node_id, "what is this graph about?"]
        )
        assert returned_id == node_id
        assert document.nodes[node_id].history == [
            {"role": "user", "content": "what is this graph about?"}
        ]

        notice = await bus.publish("notification")
        assert notice["visible"] is True
        assert notice["message"] == "AI response generation lands in a follow-up increment."
        assert recorder.topics_seen().count("scene") == 3, "all three mutations publish (addNode, addConversationNode, sendConversationMessage)"

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
