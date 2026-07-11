"""Tests confirming ChatScene.delete_chat_node() cascades to attached content nodes.

doc/ARCHITECTURE_REVIEW_FINDINGS.md #72 flagged send_message()'s attachment-failure path
as a possible orphaned-node bug: if an early attachment (e.g. an image) succeeds and
creates a child ImageNode/DocumentNode, then a *later* attachment in the same message
fails to read, send_message() calls delete_chat_node(user_node) and returns - the worry
was that the already-created child content nodes might be left behind since "whether
those are cascaded depends on scene delete logic; the cleanup contract is implicit."

Traced the actual code: delete_chat_node() calls remove_associated_content_nodes() as
its first step, which does remove any Code/Document/Image/Thinking node whose
parent_content_node is the node being deleted (along with the connection linking it) -
exactly the case send_message() creates. This was already correct; the finding's
"whether cascaded" question is answered "yes" here, with a test, since it previously had
none (finding #72's own point about the cleanup contract being implicit/unverified).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphite_scene import ChatScene


def _make_scene():
    return ChatScene(window=MagicMock())


class TestDeleteChatNodeCascadesToImageAndDocumentChildren:
    def test_deleting_a_user_node_removes_its_already_created_image_child(self):
        # Mirrors send_message(): an image attachment is processed and added as a
        # child of the new user node before a later attachment fails.
        scene = _make_scene()
        user_node = scene.add_chat_node("User message with attachments", is_user=True)
        image_node = scene.add_image_node(b"fake-image-bytes", user_node, prompt="")

        assert image_node in scene.image_nodes
        assert len(scene.image_connections) == 1

        scene.delete_chat_node(user_node)

        assert image_node not in scene.image_nodes
        assert image_node not in scene.items()
        assert scene.image_connections == []
        assert user_node not in scene.nodes

    def test_deleting_a_user_node_removes_its_already_created_document_child(self):
        scene = _make_scene()
        user_node = scene.add_chat_node("User message with attachments", is_user=True)
        doc_node = scene.add_document_node("notes.txt", "file content", user_node)

        assert doc_node in scene.document_nodes
        assert len(scene.document_connections) == 1

        scene.delete_chat_node(user_node)

        assert doc_node not in scene.document_nodes
        assert doc_node not in scene.items()
        assert scene.document_connections == []

    def test_deleting_a_user_node_removes_multiple_already_created_attachment_children(self):
        # The realistic send_message() scenario: several attachments succeed (one
        # image, one document) before a later one fails and triggers the delete.
        scene = _make_scene()
        user_node = scene.add_chat_node("User message with several attachments", is_user=True)
        image_node = scene.add_image_node(b"fake-image-bytes", user_node, prompt="")
        doc_node = scene.add_document_node("notes.txt", "file content", user_node)

        scene.delete_chat_node(user_node)

        assert scene.image_nodes == []
        assert scene.document_nodes == []
        assert scene.image_connections == []
        assert scene.document_connections == []
        assert list(scene.items()) == []

    def test_content_nodes_belonging_to_a_different_node_are_not_touched(self):
        scene = _make_scene()
        node_a = scene.add_chat_node("Node A", is_user=True)
        node_b = scene.add_chat_node("Node B", is_user=True)
        image_on_a = scene.add_image_node(b"a-bytes", node_a, prompt="")
        image_on_b = scene.add_image_node(b"b-bytes", node_b, prompt="")

        scene.delete_chat_node(node_a)

        assert image_on_a not in scene.image_nodes
        assert image_on_b in scene.image_nodes
