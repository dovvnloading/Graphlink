"""Tests for the per-node worker-thread fix.

Six plugins (Artifact, Workflow, Graph Diff, Quality Gate, Code Review, Gitlink) plus
Code Sandbox stored their running worker thread on a single main_window.X_thread
attribute shared across every node of that plugin type, in addition to (for Quality
Gate/Code Review/Gitlink/Code Sandbox) a proper per-node node.worker_thread attribute.
The shared attribute was pure dead weight for five of the seven (nothing else ever
read it) except for Code Sandbox, where stop_code_sandbox_node actually stopped
whatever main_window.sandbox_thread currently was - meaning clicking "stop" on one
sandbox node could stop a *different*, more-recently-started concurrent sandbox node's
execution instead of (or as well as) its own.

Graphlink-Reasoning got the same fix later, as part of its full redesign (see
doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.10): it predates graphlink_plugins/ entirely
so it never got task #26's fix along with the other six - it had the shared
main_window.reasoning_thread attribute AND no stop capability of any kind (no
node.worker_thread, no stop_reasoning_node method at all).

PyCoderNode had the same bug and was still unfixed as of
doc/ARCHITECTURE_REVIEW_FINDINGS.md #21: stop_pycoder_node(pycoder_node) took a specific
node argument but stopped main_window.code_exec_thread/pycoder_exec_thread - single
attributes shared across every PyCoderNode - so clicking "stop" on one node could stop a
different, more-recently-started concurrent PyCoderNode's execution instead of (or as
well as) its own. Fixed the same way as Code Sandbox: both CodeExecutionWorker (MANUAL
mode) and PyCoderExecutionWorker (AI_DRIVEN mode) are now stored on the owning
pycoder_node.worker_thread instead.

These tests use WindowActionsMixin directly (a bare mixin - the methods only need the
attributes they actually touch, so a minimal instance is enough) rather than a full
ChatWindow, which needs a running QApplication with a real settings_manager and more
app machinery than this fix is about.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import graphlink_window_actions
from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode
from graphlink_plugins.graphlink_plugin_code_sandbox import CodeSandboxNode
from graphlink_pycoder import PyCoderMode, PyCoderNode
from graphlink_window_actions import WindowActionsMixin


class _FakeMainWindow(WindowActionsMixin):
    pass


def _fake_worker_thread(is_running=True):
    thread = MagicMock()
    thread.isRunning.return_value = is_running
    return thread


class TestCodeSandboxStopOnlyTouchesItsOwnNode:
    def test_stopping_one_node_does_not_stop_a_different_concurrent_node(self):
        main_window = _FakeMainWindow()
        node_a = CodeSandboxNode(parent_node=None)
        node_b = CodeSandboxNode(parent_node=None)
        node_a.worker_thread = _fake_worker_thread()
        node_b.worker_thread = _fake_worker_thread()

        main_window.stop_code_sandbox_node(node_a)

        assert node_a.worker_thread is None
        node_b.worker_thread.stop.assert_not_called()
        node_b.worker_thread.isRunning.assert_not_called()

    def test_stopping_the_correct_node_actually_stops_its_thread(self):
        main_window = _FakeMainWindow()
        node = CodeSandboxNode(parent_node=None)
        thread = _fake_worker_thread()
        node.worker_thread = thread

        main_window.stop_code_sandbox_node(node)

        thread.stop.assert_called_once()
        assert node.worker_thread is None

    def test_main_window_has_no_shared_sandbox_thread_attribute_after_stop(self):
        main_window = _FakeMainWindow()
        node = CodeSandboxNode(parent_node=None)
        node.worker_thread = _fake_worker_thread()

        main_window.stop_code_sandbox_node(node)

        assert not hasattr(main_window, "sandbox_thread")


class TestArtifactStopMethods:
    def test_stop_artifact_node_stops_its_own_thread_and_resets_state(self):
        main_window = _FakeMainWindow()
        node = ArtifactNode(parent_node=None)
        thread = _fake_worker_thread()
        node.worker_thread = thread
        node.set_running_state(True)
        assert node.instruction_input.isReadOnly() is True  # sanity: "running" locks input

        main_window.stop_artifact_node(node)

        thread.stop.assert_called_once()
        assert node.worker_thread is None
        assert node.instruction_input.isReadOnly() is False  # set_running_state(False) ran


class TestPyCoderStopOnlyTouchesItsOwnNode:
    def test_stopping_one_node_does_not_stop_a_different_concurrent_node(self):
        main_window = _FakeMainWindow()
        node_a = PyCoderNode(parent_node=None, mode=PyCoderMode.MANUAL)
        node_b = PyCoderNode(parent_node=None, mode=PyCoderMode.MANUAL)
        node_a.worker_thread = _fake_worker_thread()
        node_b.worker_thread = _fake_worker_thread()

        main_window.stop_pycoder_node(node_a)

        assert node_a.worker_thread is None
        node_b.worker_thread.stop.assert_not_called()
        node_b.worker_thread.isRunning.assert_not_called()

    def test_stopping_the_correct_node_actually_stops_its_thread(self):
        main_window = _FakeMainWindow()
        node = PyCoderNode(parent_node=None, mode=PyCoderMode.AI_DRIVEN)
        thread = _fake_worker_thread()
        node.worker_thread = thread

        main_window.stop_pycoder_node(node)

        thread.stop.assert_called_once()
        assert node.worker_thread is None

    def test_new_pycoder_node_starts_with_no_worker_thread(self):
        node = PyCoderNode(parent_node=None)
        assert node.worker_thread is None

    def test_main_window_has_no_shared_pycoder_thread_attributes_after_stop(self):
        main_window = _FakeMainWindow()
        node = PyCoderNode(parent_node=None, mode=PyCoderMode.MANUAL)
        node.worker_thread = _fake_worker_thread()

        main_window.stop_pycoder_node(node)

        assert not hasattr(main_window, "code_exec_thread")
        assert not hasattr(main_window, "pycoder_exec_thread")


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
