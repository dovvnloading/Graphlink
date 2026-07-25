"""Regression coverage: dead shared-thread attributes must not reappear in
graphlink_window_actions.py.

Several plugins (Artifact, Workflow, Graph Diff, Quality Gate, Code Review,
Gitlink, Code Sandbox, Reasoning, PyCoder) used to store their running worker
thread on a single main_window.X_thread attribute shared across every node of
that plugin type, in addition to (for some of them) a proper per-node
node.worker_thread attribute. The shared attribute was pure dead weight for
most of them, but for Code Sandbox (and later PyCoder) it was an active bug:
stop_code_sandbox_node/stop_pycoder_node actually stopped whichever
main_window.sandbox_thread/pycoder_exec_thread currently was - meaning
clicking "stop" on one node could stop a *different*, more-recently-started
concurrent node's execution instead of (or as well as) its own.

The per-node test coverage for Artifact, Code Sandbox, and PyCoder that used
to live in this file was removed along with those node classes as part of the
Qt-removal cleanup (they were reimplemented Qt-free in the new FastAPI+React
app), and their stop_artifact_node/stop_code_sandbox_node/stop_pycoder_node
methods no longer exist on WindowActionsMixin at all. The remaining test below
is generic - it scans graphlink_window_actions.py's source text for the dead
attribute names directly, not any node class - and still protects the
plugins that remain in this legacy app (Workflow, Graph Diff, Quality Gate,
Code Review, Reasoning): a regression here would mean a shared-thread
attribute crept back in for one of them.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import graphlink_window_actions


class TestNoDeadSharedThreadAttributesRemainInSource:
    def test_window_actions_source_has_no_shared_thread_assignments(self):
        source = Path(graphlink_window_actions.__file__).read_text(encoding="utf-8")
        for dead_attr in [
            "self.artifact_thread",
            "self.workflow_thread",
            "self.graph_diff_thread",
            "self.quality_gate_thread",
            "self.code_review_thread",
            "self.gitlink_thread",
            "self.sandbox_thread",
            "self.reasoning_thread",
            "self.code_exec_thread",
            "self.pycoder_exec_thread",
        ]:
            assert dead_attr not in source, (
                f"{dead_attr} reappeared in graphlink_window_actions.py - this was removed "
                f"because it was a single attribute shared across every node of that "
                f"plugin type, which for Code Sandbox caused stop_code_sandbox_node to "
                f"stop the wrong node's thread when two ran concurrently."
            )
