"""Tests for graphite_plugin_graph_diff._node_label (Phase 2 of the plugin refactor).

_node_label used to hardcode its own class-name -> display-name map, independently of
graphite_plugin_portal.py's registration and graphite_plugin_quality_gate.py's own copy
of the same map. That map was missing CodeReviewNode entirely (CodeReviewNode is a valid
Branch Lens comparison source per portal.py's valid_sources tuple) - comparing a branch
that ended in a Code Review Agent node would have shown the raw class name
"CodeReviewNode" in the transcript instead of "Code Review Agent". _node_label now
delegates to graphite_plugin_portal.get_display_name_for_node() (PLUGIN_REGISTRY),
which fixes that gap, plus keeps a small residual map for core node types (like
ChatNode) that aren't registered plugins.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphite_nodes.graphite_node_chat import ChatNode
from graphite_plugins.graphite_plugin_artifact import ArtifactNode
from graphite_plugins.graphite_plugin_code_review import CodeReviewNode
from graphite_plugins.graphite_plugin_gitlink import GitlinkNode
from graphite_plugins.graphite_plugin_graph_diff import GraphDiffNode, _node_label
from graphite_plugins.graphite_plugin_portal import PLUGIN_REGISTRY


def _instantiate(node_cls):
    # GraphDiffNode alone has a different creation contract (two source nodes instead
    # of a single parent_node) - see PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.5.
    if node_cls is GraphDiffNode:
        return node_cls(ArtifactNode(parent_node=None), ArtifactNode(parent_node=None))
    return node_cls(parent_node=None)


def test_code_review_node_resolves_to_its_registered_display_name():
    # This is the bug: previously fell back to the raw class name "CodeReviewNode"
    # because the old hand-copied map never included it.
    node = CodeReviewNode(parent_node=None)
    assert _node_label(node) == "Code Review Agent"


def test_chat_node_uses_the_non_plugin_label():
    node = ChatNode(text="hi", is_user=True)
    assert _node_label(node) == "Chat Node"


def test_every_registered_plugin_node_resolves_via_node_label():
    for spec in PLUGIN_REGISTRY.values():
        if spec.node_cls is None:
            continue
        node = _instantiate(spec.node_cls)
        assert _node_label(node) == spec.display_name


def test_unregistered_class_falls_back_to_class_name():
    class SomeUnregisteredNode:
        pass

    assert _node_label(SomeUnregisteredNode()) == "SomeUnregisteredNode"
