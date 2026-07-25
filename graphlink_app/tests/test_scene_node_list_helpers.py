"""Tests for ChatScene's node-list composition helpers.

graphlink_scene.py already defined _all_conversational_nodes()/_all_content_nodes()/
_all_layout_nodes() (a union of the plugin node lists plus self.nodes, content
nodes, and chart nodes respectively) but five methods (find_items,
update_search_highlight, add_chat_node's parent validation, nodeMoved's type
validation, update_connections) still built the same unions inline instead of calling
them - meaning adding a new plugin node type would have required updating those five
inline expressions in addition to the three helpers. They're now reused instead.

These tests populate a real ChatScene (constructible headlessly - its __init__ only
needs a `window` reference, which it just stores) with representative items in every
relevant list and verify the refactored methods treat chart nodes consistently with
the other searchable/layout content nodes.

Note: this file originally also covered ArtifactNode/GitlinkNode-specific cases
(find_items matching real plugin-node content, nodeMoved validating a real plugin
node, update_connections pruning a plugin-node connection). ArtifactNode and
GitlinkNode were removed as part of the Qt-to-web rewrite (superseded by Qt-free
FastAPI+React implementations), and graphlink_scene.py no longer tracks
artifact_nodes/gitlink_nodes lists at all, so those cases were removed along with
them. Coverage for the other node-list categories (chat nodes, code nodes, chart
nodes, and generic untracked-node rejection) is unaffected and kept below.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_scene import ChatScene


def _make_scene():
    return ChatScene(window=MagicMock())


def _tag(**attrs):
    return SimpleNamespace(**attrs)


class TestAllConversationalNodes:
    def test_includes_chat_nodes(self):
        scene = _make_scene()
        chat = object()
        scene.nodes.append(chat)

        result = scene._all_conversational_nodes()

        assert chat in result

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
        code_item = object()
        chart_item = object()
        scene.nodes.append(chat)
        scene.code_nodes.append(code_item)
        scene.chart_nodes.append(chart_item)

        result = scene._all_layout_nodes()

        assert chat in result
        assert code_item in result
        assert chart_item in result


class TestUpdateSearchHighlightIncludesChartNodes:
    def test_syncs_flag_on_content_and_chart_nodes(self):
        scene = _make_scene()
        code_item = _tag(is_search_match=False, update=lambda: None)
        chart_item = _tag(is_search_match=False, update=lambda: None)
        scene.code_nodes.append(code_item)
        scene.chart_nodes.append(chart_item)

        scene.update_search_highlight([code_item, chart_item])

        assert code_item.is_search_match is True
        assert chart_item.is_search_match is True


class TestNodeMovedTypeValidation:
    def test_a_node_not_in_any_tracked_list_is_rejected_without_raising(self):
        scene = _make_scene()
        untracked = MagicMock()
        untracked.scene.return_value = scene

        # Should return early (node not in _all_layout_nodes()) rather than raising.
        scene.nodeMoved(untracked)
