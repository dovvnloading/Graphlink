"""Tests for the seed_prompt() protocol (Phase 2 of doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md).

graphite_window_actions.py's _seed_plugin_prompt used to be an 11-branch isinstance
chain that had to be hand-edited every time a seedable plugin was added - each branch
knew which private widget attribute to poke, and some also had to remember to call a
node-specific _on_X_changed() side-effect afterwards. That knowledge now lives on each
node class as a seed_prompt(text) method instead, and the dispatcher just calls it.

These tests construct each seedable node headlessly (QApplication only, no scene/main
window needed) and verify seed_prompt() reproduces the exact widget-and-side-effect
behavior the old isinstance branch had for that node type.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphite_web import WebNode
from graphite_pycoder import PyCoderNode
from graphite_plugins.graphite_plugin_code_sandbox import CodeSandboxNode
from graphite_plugins.graphite_plugin_artifact import ArtifactNode
from graphite_conversation_node import ConversationNode
from graphite_html_view import HtmlViewNode
from graphite_plugins.graphite_plugin_gitlink import GitlinkNode


@pytest.mark.parametrize(
    "node_cls, get_widget_text",
    [
        (WebNode, lambda n: n.query_input.toPlainText()),
        (PyCoderNode, lambda n: n.prompt_input.toPlainText()),
        (CodeSandboxNode, lambda n: n.prompt_input.toPlainText()),
        (ArtifactNode, lambda n: n.instruction_input.toPlainText()),
        (ConversationNode, lambda n: n.message_input.text()),
        (HtmlViewNode, lambda n: n.html_input.toPlainText()),
        (GitlinkNode, lambda n: n.task_input.toPlainText()),
    ],
)
def test_seed_prompt_sets_the_expected_widget_text(node_cls, get_widget_text):
    node = node_cls(parent_node=None)
    node.seed_prompt("hello world")
    assert get_widget_text(node) == "hello world"


def test_web_node_query_state_updates():
    node = WebNode(parent_node=None)
    node.seed_prompt("search this")
    assert node.query == "search this"


def test_gitlink_node_task_prompt_state_updates():
    node = GitlinkNode(parent_node=None)
    node.seed_prompt("make this change")
    assert node.task_prompt == "make this change"


def test_every_seedable_registry_spec_has_a_working_seed_prompt():
    # Cross-check against PLUGIN_REGISTRY directly, so a future plugin marked
    # seedable=True without implementing seed_prompt() fails here immediately
    # instead of silently doing nothing when a workflow/quality-gate recommendation
    # tries to seed it.
    from graphite_plugins.graphite_plugin_portal import PLUGIN_REGISTRY

    for spec in PLUGIN_REGISTRY.values():
        if not spec.seedable or spec.node_cls is None:
            continue
        node = spec.node_cls(parent_node=None)
        assert callable(getattr(node, "seed_prompt", None)), (
            f"{spec.key!r} is marked seedable=True but {spec.node_cls.__name__} has no "
            f"seed_prompt() method."
        )
        node.seed_prompt("registry cross-check")
