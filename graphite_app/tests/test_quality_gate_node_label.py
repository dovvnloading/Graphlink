"""Tests for graphite_plugins.quality_gate.scoring._node_label (Phase 5).

Mirrors graphite_plugin_graph_diff._node_label's migration (see
tests/test_graph_diff_node_label.py): this map was already complete/correct (unlike
Graph Diff's, which was missing CodeReviewNode) but was still an independently
hand-maintained copy of the same class-name -> display-name knowledge PLUGIN_REGISTRY
now owns. Migrated onto graphite_plugin_portal.get_display_name_for_node() for the
same reason - one less place for this to drift out of sync in the future.

_node_label itself now lives in graphite_plugins.quality_gate.scoring (extracted out of
graphite_plugin_quality_gate.py along with QualityGateAnalyzer - see
doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.7).

Note: QUALITY_GATE_PLUGIN_ICONS/QUALITY_GATE_ALLOWED_PLUGINS (a separate map used for
the recommendation-card UI, not this text-label function) were deliberately NOT
migrated - their icon values differ from the registry's on purpose/by prior drift
(e.g. "Py-Coder" is "fa5s.code" here vs the registry's "fa5s.laptop-code"), so touching
them would be a visible UI change, not a behavior-preserving refactor.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphite_nodes.graphite_node_chat import ChatNode
from graphite_plugins.graphite_plugin_artifact import ArtifactNode
from graphite_plugins.graphite_plugin_code_review import CodeReviewNode
from graphite_plugins.graphite_plugin_graph_diff import GraphDiffNode
from graphite_plugins.graphite_plugin_portal import PLUGIN_REGISTRY
from graphite_plugins.quality_gate.scoring import _node_label


def _instantiate(node_cls):
    if node_cls is GraphDiffNode:
        return node_cls(ArtifactNode(parent_node=None), ArtifactNode(parent_node=None))
    return node_cls(parent_node=None)


def test_code_review_node_resolves_to_its_registered_display_name():
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
