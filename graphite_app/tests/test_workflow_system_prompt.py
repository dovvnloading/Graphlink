"""Tests that WorkflowArchitectAgent.SYSTEM_PROMPT's "Allowed plugins:" bullet list is
generated from WORKFLOW_ALLOWED_PLUGINS rather than a second, hand-typed copy (see
doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 1.4/4.7).

The two lists silently drifting apart (the prompt's hand-typed bullets missing "Code
Review Agent" after it was added to WORKFLOW_PLUGIN_ICONS/WORKFLOW_ALLOWED_PLUGINS) was
a real, already-fixed bug - see tests/test_plugin_registry.py for the registry-level
guard. This file guards the prompt-text generation itself so a future added/removed
plugin can't reintroduce the same drift.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphite_plugins.graphite_plugin_workflow import (
    WORKFLOW_ALLOWED_PLUGINS,
    WorkflowArchitectAgent,
)


def test_every_allowed_plugin_appears_as_a_bullet_in_the_system_prompt():
    for plugin_name in WORKFLOW_ALLOWED_PLUGINS:
        assert f"- {plugin_name}" in WorkflowArchitectAgent.SYSTEM_PROMPT


def test_bullet_count_matches_allowed_plugin_count_exactly():
    # Guards against the bullet list silently containing extra/stale entries that
    # individually match the substring check above but don't correspond 1:1 with
    # WORKFLOW_ALLOWED_PLUGINS.
    bullet_lines = [
        line for line in WorkflowArchitectAgent.SYSTEM_PROMPT.splitlines() if line.startswith("- ")
    ]
    assert len(bullet_lines) == len(WORKFLOW_ALLOWED_PLUGINS)


def test_no_leftover_template_marker():
    assert "__ALLOWED_PLUGINS_BULLETS__" not in WorkflowArchitectAgent.SYSTEM_PROMPT


def test_json_shape_braces_survive_the_template_substitution():
    # The substitution is a plain str.replace(), not str.format()/an f-string, so the
    # JSON example's literal braces must come through completely unescaped.
    assert '"recommended_plugins": [' in WorkflowArchitectAgent.SYSTEM_PROMPT
    assert WorkflowArchitectAgent.SYSTEM_PROMPT.rstrip().endswith("}")
