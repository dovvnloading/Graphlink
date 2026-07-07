"""Tests for PluginPortal.create_node() and its first consumer, _create_artifact_node.

doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md Phase 2: create_node() is the generic single-parent
plugin node factory meant to replace the ~15-line skeleton duplicated across most
_create_X_node methods (resolve/validate parent, construct, wire into children,
position, register with the scene). _create_artifact_node is migrated onto it here as
the first proof; the other 12 _create_X_node methods are migrated one at a time in
later phases, not all at once.

These tests use a lightweight fake scene/main_window rather than real Qt scene/window
objects (which need a running app with a loaded chat) - only the handful of attributes
create_node() actually touches are faked.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtCore import QPointF

from graphite_plugins.graphite_plugin_artifact import ArtifactConnectionItem, ArtifactNode
from graphite_plugins.graphite_plugin_portal import PluginPortal


class FakeScene:
    def __init__(self):
        self.artifact_nodes = []
        self.artifact_connections = []
        self.added_items = []

    def find_branch_position(self, parent_node, node):
        return QPointF(10, 20)

    def addItem(self, item):
        self.added_items.append(item)


def _make_portal_and_parent():
    scene = FakeScene()
    parent = ArtifactNode(parent_node=None)

    main_window = MagicMock()
    main_window.chat_view.scene.return_value = scene
    main_window.current_node = parent

    portal = PluginPortal(main_window=main_window)
    return portal, main_window, scene, parent


def test_create_artifact_node_builds_the_node_and_connection():
    portal, main_window, scene, parent = _make_portal_and_parent()

    result = portal._create_artifact_node()

    assert isinstance(result, ArtifactNode)
    assert result in parent.children
    assert result in scene.artifact_nodes
    assert isinstance(result.incoming_connection, ArtifactConnectionItem)
    assert result.incoming_connection in scene.artifact_connections
    assert result in scene.added_items
    assert result.incoming_connection in scene.added_items


def test_create_artifact_node_positions_via_find_branch_position():
    portal, main_window, scene, parent = _make_portal_and_parent()
    result = portal._create_artifact_node()
    assert result.pos() == QPointF(10, 20)


def test_create_artifact_node_wires_artifact_requested_to_main_window():
    portal, main_window, scene, parent = _make_portal_and_parent()
    result = portal._create_artifact_node()

    result.artifact_requested.emit(result)

    main_window.execute_artifact_node.assert_called_once_with(result)


def test_create_artifact_node_wires_stop_requested_to_main_window():
    # stop_requested is what makes the node's dual-purpose button's "stop" state
    # actually do something (see doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.1/§31) -
    # this locks in that _create_artifact_node wires it alongside artifact_requested.
    portal, main_window, scene, parent = _make_portal_and_parent()
    result = portal._create_artifact_node()

    result.stop_requested.emit(result)

    main_window.stop_artifact_node.assert_called_once_with(result)


def test_create_artifact_node_warns_and_returns_none_with_no_selection():
    portal, main_window, scene, parent = _make_portal_and_parent()
    main_window.current_node = None

    result = portal._create_artifact_node()

    assert result is None
    assert scene.artifact_nodes == []
    main_window.notification_banner.show_message.assert_called_once()
    warning_text = main_window.notification_banner.show_message.call_args[0][0]
    assert "select a node to branch from" in warning_text


def test_create_artifact_node_warns_and_returns_none_for_invalid_parent():
    portal, main_window, scene, parent = _make_portal_and_parent()

    class NotConversational:
        pass

    main_window.current_node = NotConversational()

    result = portal._create_artifact_node()

    assert result is None
    assert scene.artifact_nodes == []
    warning_text = main_window.notification_banner.show_message.call_args[0][0]
    assert "valid conversational node" in warning_text


def test_create_node_no_selection_message_used_when_invalid_parent_message_omitted():
    scene = FakeScene()
    parent = ArtifactNode(parent_node=None)
    main_window = MagicMock()
    main_window.chat_view.scene.return_value = scene
    main_window.current_node = MagicMock(spec=[])  # no 'children' attribute

    portal = PluginPortal(main_window=main_window)
    result = portal.create_node(
        node_cls=ArtifactNode,
        connection_cls=ArtifactConnectionItem,
        scene_nodes=scene.artifact_nodes,
        scene_connections=scene.artifact_connections,
        resolve_branch_parent=False,
        no_selection_message="only message provided",
    )

    assert result is None
    main_window.notification_banner.show_message.assert_called_once_with("only message provided", 5000, "warning")
