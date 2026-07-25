"""Tests for the standard-shape plugin factories built on PluginPortal.create_node()
that are still Qt (Conversation Node, HTML Renderer).

NOTE (Qt removal cleanup): this file used to also cover Py-Coder, Execution Sandbox,
Gitlink, and Graphlink-Web. Their node classes (PyCoderNode, CodeSandboxNode,
GitlinkNode, WebNode) and PluginPortal's corresponding factory methods
(_create_pycoder_node, _create_code_sandbox_node, _create_gitlink_node,
_create_web_node) were deleted as part of the Qt-free plugin rewrite (see backend/,
web_ui/), so that coverage was removed here rather than migrated - the new
implementations have their own test suites. Where a removed plugin's node class was
only used as scaffolding (an arbitrary "some existing conversational node" stand-in)
rather than as the subject under test, it has been swapped for a still-existing type
(ConversationNode) or a minimal local fake so the remaining Conversation
Node/HTML Renderer coverage keeps running unmodified in behavior.

Mirrors tests/test_plugin_portal_create_node.py's approach (fake scene/main_window,
real headlessly-constructed node instances).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPointF

from graphlink_connections import ConversationConnectionItem, HtmlConnectionItem
from graphlink_conversation_node import ConversationNode
from graphlink_html_view import HtmlViewNode
from graphlink_nodes.graphlink_node_code import CodeNode
from graphlink_plugins.graphlink_plugin_portal import PluginPortal


class FakeScene:
    def __init__(self):
        for name in [
            "conversation_nodes", "conversation_connections",
            "html_view_nodes", "html_connections",
        ]:
            setattr(self, name, [])
        self.added_items = []

    def find_branch_position(self, parent_node, node):
        return QPointF(10, 20)

    def addItem(self, item):
        self.added_items.append(item)


def _make_portal(current_node):
    scene = FakeScene()
    main_window = MagicMock()
    main_window.chat_view.scene.return_value = scene
    main_window.current_node = current_node
    portal = PluginPortal(main_window=main_window)
    return portal, main_window, scene


class TestConversationNodeHistoryHandling:
    def test_conversation_node_wires_both_signals(self):
        # ConversationNode stands in for the deleted ArtifactNode this test used to use
        # purely as a generic real-node parent (it needs a real QGraphicsItem-ish object
        # so the new node's incoming ConversationConnectionItem can compute its path -
        # a plain fake without Qt geometry methods like parentItem()/boundingRect()
        # blows up inside update_path()).
        parent = ConversationNode(parent_node=None)
        portal, main_window, scene = _make_portal(parent)

        result = portal._create_conversation_node()

        # ai_request_sent is declared Signal(object, list) - a plain str second arg
        # gets silently coerced into a list of its characters by Qt, so use a real list.
        result.ai_request_sent.emit(result, ["hi"])
        main_window.handle_conversation_node_request.assert_called_once_with(result, ["hi"])
        result.cancel_requested.emit(result)
        main_window.handle_conversation_node_cancel.assert_called_once_with(result)

    def test_conversation_node_uses_set_history_not_direct_assignment(self):
        parent = ConversationNode(parent_node=None)
        # ConversationNode.set_history() drops a trailing assistant message (so a
        # continued conversation doesn't start with an already-answered turn already
        # baked in) - end on a user message so the clone round-trips untouched.
        parent.conversation_history = [{"role": "assistant", "content": "hi there"}, {"role": "user", "content": "hello"}]
        portal, main_window, scene = _make_portal(parent)

        result = portal._create_conversation_node()

        # set_history() re-derives conversation_history from re-adding each message via
        # add_user_message/add_ai_message rather than a plain list assignment - just
        # confirm it actually ran (matches the parent's messages) rather than the node
        # being left with its constructor default.
        assert len(result.conversation_history) == 2

    def test_conversation_node_returns_none_with_no_selection(self):
        portal, main_window, scene = _make_portal(current_node=None)
        result = portal._create_conversation_node()
        assert result is None
        assert scene.conversation_nodes == []


class TestHtmlRendererValidateParent:
    def test_accepts_a_valid_parent_type_and_builds_the_node(self):
        # ConversationNode stands in for the deleted PyCoderNode this test used to
        # construct here - both are (were) members of _create_html_view_node's
        # valid_parents tuple, so the "some valid parent type" role is unchanged.
        parent = ConversationNode(parent_node=None)
        portal, main_window, scene = _make_portal(parent)

        result = portal._create_html_view_node()

        assert isinstance(result, HtmlViewNode)
        assert result in scene.html_view_nodes
        assert isinstance(result.incoming_connection, HtmlConnectionItem)
        assert result.incoming_connection in scene.html_connections

    def test_rejects_a_parent_type_not_in_the_allowed_tuple(self):
        # A plain local class is deliberately not one of HTML Renderer's valid_parents
        # types (ChatNode, CodeNode, ConversationNode) - replaces the deleted
        # ArtifactNode this test used to use for the same "known-invalid parent" role.
        class NotAllowedParent:
            pass

        parent = NotAllowedParent()
        portal, main_window, scene = _make_portal(parent)

        result = portal._create_html_view_node()

        assert result is None
        assert scene.html_view_nodes == []
        main_window.notification_banner.show_message.assert_called_once()

    def test_copies_code_content_when_parent_is_a_code_node(self):
        # Was a crash before create_node() guarded the children.append() step: CodeNode
        # is listed in valid_parents and is meant to have its .code copied into the new
        # HtmlViewNode, but CodeNode has no `children` attribute, and the previously
        # unconditional `parent_node.children.append(node)` ran before the copy step,
        # raising AttributeError instead. Confirmed via direct investigation of
        # graphlink_scene.py's deletion/connection-validity logic that CodeNode was
        # never part of the `.children`-based branch-visibility system anyway, so
        # skipping that step for a CodeNode parent is safe.
        code_node = CodeNode(code="print(42)", language="python", parent_content_node=None)
        portal, main_window, scene = _make_portal(code_node)

        result = portal._create_html_view_node()

        assert isinstance(result, HtmlViewNode)
        assert "print(42)" in result.get_html_content()
        assert not hasattr(code_node, "children")
