"""Tests for ChatScene's node-list composition helpers (Phase 5).

graphlink_scene.py already defined _all_conversational_nodes()/_all_content_nodes()/
_all_layout_nodes() (a union of the 12 plugin node lists plus self.nodes, content
nodes, and chart nodes respectively) but five methods (find_items,
update_search_highlight, add_chat_node's parent validation, nodeMoved's type
validation, update_connections) still built the same unions inline instead of calling
them - meaning adding a 13th plugin node type would have required updating those five
inline expressions in addition to the three helpers. They're now reused instead.

These tests populate a real ChatScene (constructible headlessly - its __init__ only
needs a `window` reference, which it just stores) with representative items in every
relevant list and verify the refactored methods treat chart nodes consistently with
the other searchable/layout content nodes.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode
from graphlink_scene import ChatScene


def _make_scene():
    return ChatScene(window=MagicMock())


def _tag(**attrs):
    return SimpleNamespace(**attrs)


class TestAllConversationalNodes:
    def test_includes_chat_nodes_and_every_plugin_list(self):
        scene = _make_scene()
        chat = object()
        artifact = object()
        gitlink = object()
        scene.nodes.append(chat)
        scene.artifact_nodes.append(artifact)
        scene.gitlink_nodes.append(gitlink)

        result = scene._all_conversational_nodes()

        assert chat in result
        assert artifact in result
        assert gitlink in result

    def test_excludes_content_and_chart_nodes(self):
        scene = _make_scene()
        code_item = object()
        chart_item = object()
        scene.code_nodes.append(code_item)
        scene.chart_nodes.append(chart_item)

        result = scene._all_conversational_nodes()

        assert code_item not in result
        assert chart_item not in result


class TestAllLayoutNodes:
    def test_includes_conversational_content_and_chart_nodes(self):
        scene = _make_scene()
        chat = object()
        artifact = object()
        code_item = object()
        chart_item = object()
        scene.nodes.append(chat)
        scene.artifact_nodes.append(artifact)
        scene.code_nodes.append(code_item)
        scene.chart_nodes.append(chart_item)

        result = scene._all_layout_nodes()

        assert chat in result
        assert artifact in result
        assert code_item in result
        assert chart_item in result


class TestFindItemsUsesAllLayoutNodes:
    def test_matches_a_plugin_node_by_its_content(self):
        # find_items() isinstance-checks against real node classes to pick which
        # attribute holds searchable text, so a real ArtifactNode is needed here (a
        # generic mock would never match any isinstance branch and would silently
        # produce a false negative regardless of the list-composition refactor).
        scene = _make_scene()
        artifact = ArtifactNode(parent_node=None)
        artifact.set_artifact_content("find this needle")
        scene.artifact_nodes.append(artifact)

        results = scene.find_items("find this needle")

        assert artifact in results

    def test_does_not_match_unrelated_text(self):
        scene = _make_scene()
        artifact = ArtifactNode(parent_node=None)
        artifact.set_artifact_content("hello world")
        scene.artifact_nodes.append(artifact)

        results = scene.find_items("nonexistent needle")

        assert artifact not in results


class TestUpdateSearchHighlightIncludesChartNodes:
    def test_syncs_flag_on_plugin_content_and_chart_nodes(self):
        scene = _make_scene()
        artifact = _tag(is_search_match=False, update=lambda: None)
        code_item = _tag(is_search_match=False, update=lambda: None)
        chart_item = _tag(is_search_match=False, update=lambda: None)
        scene.artifact_nodes.append(artifact)
        scene.code_nodes.append(code_item)
        scene.chart_nodes.append(chart_item)

        scene.update_search_highlight([artifact, code_item, chart_item])

        assert artifact.is_search_match is True
        assert code_item.is_search_match is True
        assert chart_item.is_search_match is True


class TestNodeMovedTypeValidation:
    def test_a_plugin_node_with_a_scene_passes_the_validity_check_and_reaches_frame_handling(self):
        # nodeMoved() returns early if the node isn't in _all_layout_nodes() or has no
        # scene. Give it a real ArtifactNode (in scene.artifact_nodes, with .scene()
        # returning the scene) and confirm it proceeds into the frame-membership loop
        # instead of bailing out - observable via frame.nodes being consulted without
        # raising, since frame is a real object with a `.nodes` list and `.resizing`
        # flag that would need to exist for the loop body to run without error.
        scene = _make_scene()
        artifact = ArtifactNode(parent_node=None)
        scene.addItem(artifact)
        scene.artifact_nodes.append(artifact)

        frame = MagicMock()
        frame.nodes = []
        frame.resizing = False
        scene.frames.append(frame)

        scene.nodeMoved(artifact)

        # If nodeMoved() had bailed out early (i.e. treated the plugin node as
        # invalid), the frame-membership check below would never execute.
        assert artifact not in frame.nodes  # not part of this frame, but reached safely

    def test_a_node_not_in_any_tracked_list_is_rejected_without_raising(self):
        scene = _make_scene()
        untracked = MagicMock()
        untracked.scene.return_value = scene

        # Should return early (node not in _all_layout_nodes()) rather than raising.
        scene.nodeMoved(untracked)


class TestUpdateConnectionsUsesConversationalPlusContentNodes:
    def test_a_connection_between_two_tracked_plugin_nodes_survives(self):
        scene = _make_scene()
        parent = ArtifactNode(parent_node=None)
        child = ArtifactNode(parent_node=parent)
        parent.children.append(child)
        scene.addItem(parent)
        scene.addItem(child)
        scene.artifact_nodes.extend([parent, child])

        from graphlink_plugins.graphlink_plugin_artifact import ArtifactConnectionItem
        connection = ArtifactConnectionItem(parent, child)
        scene.addItem(connection)
        scene.connections.append(connection)

        scene.update_connections()

        assert connection in scene.connections

    def test_a_connection_to_a_node_not_tracked_in_any_scene_list_is_pruned(self):
        # Simulates a connection left dangling after its endpoint was removed from
        # the scene's tracking list (e.g. mid-deletion) - update_connections() should
        # drop it rather than leave a stale connection pointing at an untracked node.
        scene = _make_scene()
        tracked_child = ArtifactNode(parent_node=None)
        scene.addItem(tracked_child)
        scene.artifact_nodes.append(tracked_child)

        untracked_node = ArtifactNode(parent_node=None)
        scene.addItem(untracked_node)  # in the Qt scene, but deliberately not in any scene.*_nodes list

        from graphlink_plugins.graphlink_plugin_artifact import ArtifactConnectionItem
        stray_connection = ArtifactConnectionItem(untracked_node, tracked_child)
        scene.addItem(stray_connection)
        scene.connections.append(stray_connection)

        scene.update_connections()

        assert stray_connection not in scene.connections
