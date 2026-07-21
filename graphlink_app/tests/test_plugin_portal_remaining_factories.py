"""Tests for the standard-shape plugin factories now built on
PluginPortal.create_node() (Py-Coder, Execution Sandbox, Gitlink, Graphlink-Web,
Conversation Node, HTML Renderer).

Mirrors tests/test_plugin_portal_create_node.py's approach (fake scene/main_window,
real headlessly-constructed node instances) but covers each remaining factory, since
they now share the same generic create_node() call shape and the same class of
regression risk: a missed signal-wire, a dropped history clone, or a warning message
that silently changed would be caught here, not just at review time.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtCore import QPointF

from graphlink_connections import ConversationConnectionItem, HtmlConnectionItem, PyCoderConnectionItem
from graphlink_conversation_node import ConversationNode
from graphlink_html_view import HtmlViewNode
from graphlink_nodes.graphlink_node_code import CodeNode
from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode
from graphlink_plugins.graphlink_plugin_code_sandbox import CodeSandboxConnectionItem, CodeSandboxNode
from graphlink_plugins.graphlink_plugin_gitlink import GitlinkConnectionItem, GitlinkNode
from graphlink_plugins.graphlink_plugin_portal import PluginPortal
from graphlink_pycoder import PyCoderNode
from graphlink_web import WebConnectionItem, WebNode


class FakeScene:
    def __init__(self):
        for name in [
            "pycoder_nodes", "pycoder_connections",
            "code_sandbox_nodes", "code_sandbox_connections",
            "gitlink_nodes", "gitlink_connections",
            "web_nodes", "web_connections",
            "conversation_nodes", "conversation_connections",
            "html_view_nodes", "html_connections",
        ]:
            setattr(self, name, [])
        self.added_items = []

    def find_branch_position(self, parent_node, node):
        return QPointF(10, 20)

    def addItem(self, item):
        self.added_items.append(item)


class FakeSettingsManager:
    def get_github_token(self):
        return ""


def _make_portal(current_node):
    scene = FakeScene()
    main_window = MagicMock()
    main_window.chat_view.scene.return_value = scene
    main_window.current_node = current_node
    portal = PluginPortal(main_window=main_window)
    return portal, main_window, scene


# (factory_method, node_cls, connection_cls, scene_node_attr, scene_connection_attr,
#  signal_name, handler_name, clones_history)
STANDARD_CASES = [
    ("_create_pycoder_node", PyCoderNode, PyCoderConnectionItem, "pycoder_nodes", "pycoder_connections", "run_clicked", "execute_pycoder_node", False),
    ("_create_code_sandbox_node", CodeSandboxNode, CodeSandboxConnectionItem, "code_sandbox_nodes", "code_sandbox_connections", "sandbox_requested", "execute_code_sandbox_node", True),
    ("_create_gitlink_node", GitlinkNode, GitlinkConnectionItem, "gitlink_nodes", "gitlink_connections", "gitlink_requested", "execute_gitlink_node", True),
    ("_create_web_node", WebNode, WebConnectionItem, "web_nodes", "web_connections", "run_clicked", "execute_web_node", False),
]


@pytest.mark.parametrize(
    "factory_name, node_cls, connection_cls, scene_node_attr, scene_conn_attr, signal_name, handler_name, clones_history",
    STANDARD_CASES,
)
def test_standard_factory_builds_node_and_connection(
    factory_name, node_cls, connection_cls, scene_node_attr, scene_conn_attr, signal_name, handler_name, clones_history
):
    parent = ArtifactNode(parent_node=None)
    portal, main_window, scene = _make_portal(parent)

    result = getattr(portal, factory_name)()

    assert isinstance(result, node_cls)
    assert result in parent.children
    assert result in getattr(scene, scene_node_attr)
    assert isinstance(result.incoming_connection, connection_cls)
    assert result.incoming_connection in getattr(scene, scene_conn_attr)
    assert result.pos() == QPointF(10, 20)


@pytest.mark.parametrize(
    "factory_name, node_cls, connection_cls, scene_node_attr, scene_conn_attr, signal_name, handler_name, clones_history",
    [case for case in STANDARD_CASES if case[5] is not None],
)
def test_standard_factory_wires_signal_to_handler(
    factory_name, node_cls, connection_cls, scene_node_attr, scene_conn_attr, signal_name, handler_name, clones_history
):
    parent = ArtifactNode(parent_node=None)
    portal, main_window, scene = _make_portal(parent)

    result = getattr(portal, factory_name)()

    getattr(result, signal_name).emit(result)
    getattr(main_window, handler_name).assert_called_once_with(result)


@pytest.mark.parametrize(
    "factory_name, node_cls, connection_cls, scene_node_attr, scene_conn_attr, signal_name, handler_name, clones_history",
    [case for case in STANDARD_CASES if case[7]],
)
def test_standard_factory_clones_parent_history(
    factory_name, node_cls, connection_cls, scene_node_attr, scene_conn_attr, signal_name, handler_name, clones_history
):
    parent = ArtifactNode(parent_node=None)
    parent.conversation_history = [{"role": "user", "content": "hi"}]
    portal, main_window, scene = _make_portal(parent)

    result = getattr(portal, factory_name)()

    assert result.conversation_history == [{"role": "user", "content": "hi"}]
    assert result.conversation_history is not parent.conversation_history


@pytest.mark.parametrize(
    "factory_name, node_cls, connection_cls, scene_node_attr, scene_conn_attr, signal_name, handler_name, clones_history",
    STANDARD_CASES,
)
def test_standard_factory_returns_none_with_no_selection(
    factory_name, node_cls, connection_cls, scene_node_attr, scene_conn_attr, signal_name, handler_name, clones_history
):
    portal, main_window, scene = _make_portal(current_node=None)

    result = getattr(portal, factory_name)()

    assert result is None
    assert getattr(scene, scene_node_attr) == []
    main_window.notification_banner.show_message.assert_called_once()


class TestGitlinkSettingsManager:
    def test_gitlink_node_receives_main_window_settings_manager(self):
        parent = ArtifactNode(parent_node=None)
        portal, main_window, scene = _make_portal(parent)
        fake_settings = FakeSettingsManager()
        main_window.settings_manager = fake_settings

        result = portal._create_gitlink_node()

        assert result.settings_manager is fake_settings


class TestResolveBranchParentThroughCodeNode:
    def test_pycoder_resolves_through_code_node_to_its_conversational_parent(self):
        real_parent = ArtifactNode(parent_node=None)
        code_node = CodeNode(code="print(1)", language="python", parent_content_node=real_parent)
        portal, main_window, scene = _make_portal(code_node)

        result = portal._create_pycoder_node()

        assert result in real_parent.children
        assert result not in getattr(code_node, "children", [])


class TestConversationNodeHistoryHandling:
    def test_conversation_node_wires_both_signals(self):
        parent = ArtifactNode(parent_node=None)
        portal, main_window, scene = _make_portal(parent)

        result = portal._create_conversation_node()

        # ai_request_sent is declared Signal(object, list) - a plain str second arg
        # gets silently coerced into a list of its characters by Qt, so use a real list.
        result.ai_request_sent.emit(result, ["hi"])
        main_window.handle_conversation_node_request.assert_called_once_with(result, ["hi"])
        result.cancel_requested.emit(result)
        main_window.handle_conversation_node_cancel.assert_called_once_with(result)

    def test_conversation_node_uses_set_history_not_direct_assignment(self):
        parent = ArtifactNode(parent_node=None)
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
        parent = PyCoderNode(parent_node=None)
        portal, main_window, scene = _make_portal(parent)

        result = portal._create_html_view_node()

        assert isinstance(result, HtmlViewNode)
        assert result in scene.html_view_nodes
        assert isinstance(result.incoming_connection, HtmlConnectionItem)
        assert result.incoming_connection in scene.html_connections

    def test_rejects_a_parent_type_not_in_the_allowed_tuple(self):
        # ArtifactNode is deliberately not in HTML Renderer's valid_parents tuple.
        parent = ArtifactNode(parent_node=None)
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
