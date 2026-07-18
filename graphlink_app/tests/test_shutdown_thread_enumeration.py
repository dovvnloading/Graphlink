"""Tests for ChatWindow._iter_shutdown_threads()/_shutdown_background_threads().

Regression coverage for two related bugs in the hand-maintained shutdown thread list:

1. The shutdown list still named main_window.X_thread attributes (reasoning_thread,
   workflow_thread, graph_diff_thread, quality_gate_thread, code_review_thread,
   sandbox_thread, artifact_thread, gitlink_thread, and - once PyCoder got the same
   per-node fix, see test_per_node_worker_threads.py - code_exec_thread and
   pycoder_exec_thread) that were removed from the window entirely once those plugins
   moved to a per-node node.worker_thread attribute. They were harmless no-ops
   (getattr(self, name, None) always returned None) but misleading.

2. Because closeEvent()/_shutdown_background_threads() only ever checked window-level
   attributes, a running Code Sandbox/Artifact/PyCoder/Gitlink node's worker_thread was
   never waited on at shutdown - the app could quit mid-execution (e.g. abandoning a
   sandbox subprocess) instead of asking the user to wait, the way it already does for
   the window-level threads (chat, web, ...).

Uses plain fake objects (not MagicMock) as the `self` for these unbound-method calls
so that only attributes explicitly set below are considered present - a MagicMock
would auto-fabricate every attribute access, defeating the "ghost attribute" checks.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import graphlink_window


class _FakeScene:
    def __init__(self):
        self.code_sandbox_nodes = []
        self.artifact_nodes = []
        self.pycoder_nodes = []
        self.gitlink_nodes = []


class _FakeChatView:
    def __init__(self, scene):
        self._scene = scene

    def scene(self):
        return self._scene


class _FakeNode:
    def __init__(self, worker_thread=None):
        self.worker_thread = worker_thread


def _fake_worker(is_running=True):
    # spec restricts the mock to the interface CodeSandboxExecutionWorker/etc. actually
    # expose (isRunning/wait/stop, no cancel) - a bare MagicMock() auto-fabricates a
    # callable `cancel` attribute too, which _request_thread_shutdown prefers over
    # `stop` when both are present, masking which method the real worker would receive.
    worker = MagicMock(spec=["isRunning", "wait", "stop"])
    worker.isRunning.return_value = is_running
    return worker


class _FakeWindow:
    """Duck-typed stand-in for ChatWindow - binds the real unbound methods under
    test so their internal `self.foo()` / `self._iter_shutdown_threads()` calls
    resolve normally, without constructing a full QMainWindow-based ChatWindow."""

    _iter_shutdown_threads = graphlink_window.ChatWindow._iter_shutdown_threads
    _request_thread_shutdown = graphlink_window.ChatWindow._request_thread_shutdown
    _shutdown_background_threads = graphlink_window.ChatWindow._shutdown_background_threads

    def __init__(self, scene):
        self.chat_view = _FakeChatView(scene)


class TestIterShutdownThreadsHasNoGhostAttributes:
    def test_empty_window_yields_nothing(self):
        window = _FakeWindow(_FakeScene())
        assert list(window._iter_shutdown_threads()) == []

    def test_removed_plugin_thread_names_are_not_in_source(self):
        source = Path(graphlink_window.__file__).read_text(encoding="utf-8")
        for dead_attr in (
            "reasoning_thread",
            "workflow_thread",
            "graph_diff_thread",
            "quality_gate_thread",
            "code_review_thread",
            "sandbox_thread",
            "artifact_thread",
            "gitlink_thread",
            "code_exec_thread",
            "pycoder_exec_thread",
        ):
            assert dead_attr not in source, (
                f"'{dead_attr}' reappeared in graphlink_window.py - these plugins moved "
                f"to a per-node worker_thread attribute; there is no longer a "
                f"corresponding shared main_window attribute to check at shutdown."
            )


class TestPerNodeWorkerThreadsAreWaitedOnAtShutdown:
    def test_running_sandbox_node_worker_is_found(self):
        scene = _FakeScene()
        node = _FakeNode(worker_thread=_fake_worker())
        scene.code_sandbox_nodes.append(node)
        window = _FakeWindow(scene)

        results = list(window._iter_shutdown_threads())

        assert len(results) == 1
        label, worker, clear_ref = results[0]
        assert label == "code sandbox execution"
        assert worker is node.worker_thread

    def test_two_concurrent_sandbox_nodes_are_both_found(self):
        scene = _FakeScene()
        node_a = _FakeNode(worker_thread=_fake_worker())
        node_b = _FakeNode(worker_thread=_fake_worker())
        scene.code_sandbox_nodes.extend([node_a, node_b])
        window = _FakeWindow(scene)

        results = list(window._iter_shutdown_threads())

        assert {worker for _, worker, _ in results} == {node_a.worker_thread, node_b.worker_thread}

    def test_clear_ref_only_resets_its_own_node(self):
        scene = _FakeScene()
        node_a = _FakeNode(worker_thread=_fake_worker())
        node_b = _FakeNode(worker_thread=_fake_worker())
        scene.code_sandbox_nodes.extend([node_a, node_b])
        window = _FakeWindow(scene)

        for _, worker, clear_ref in window._iter_shutdown_threads():
            if worker is node_a.worker_thread:
                clear_ref()

        assert node_a.worker_thread is None
        assert node_b.worker_thread is not None

    def test_artifact_pycoder_and_gitlink_node_workers_are_also_found(self):
        scene = _FakeScene()
        scene.artifact_nodes.append(_FakeNode(worker_thread=_fake_worker()))
        scene.pycoder_nodes.append(_FakeNode(worker_thread=_fake_worker()))
        scene.gitlink_nodes.append(_FakeNode(worker_thread=_fake_worker()))
        window = _FakeWindow(scene)

        labels = {label for label, _, _ in window._iter_shutdown_threads()}

        assert labels == {"artifact workflow", "PyCoder execution", "Gitlink proposal"}

    def test_shutdown_background_threads_waits_for_and_clears_a_finishing_sandbox_worker(self):
        scene = _FakeScene()
        worker = _fake_worker()
        worker.wait.return_value = True
        node = _FakeNode(worker_thread=worker)
        scene.code_sandbox_nodes.append(node)
        window = _FakeWindow(scene)

        still_running = window._shutdown_background_threads(timeout_ms=100)

        assert still_running == []
        worker.stop.assert_called_once()
        assert node.worker_thread is None

    def test_shutdown_background_threads_reports_a_sandbox_worker_that_does_not_stop_in_time(self):
        scene = _FakeScene()
        worker = _fake_worker()
        worker.wait.return_value = False
        node = _FakeNode(worker_thread=worker)
        scene.code_sandbox_nodes.append(node)
        window = _FakeWindow(scene)

        still_running = window._shutdown_background_threads(timeout_ms=100)

        assert still_running == ["code sandbox execution"]
        assert node.worker_thread is worker


class TestDirectAttributeWorkersStillWork:
    def test_chat_thread_attribute_is_found_and_labeled(self):
        window = _FakeWindow(_FakeScene())
        window.chat_thread = _fake_worker()

        results = list(window._iter_shutdown_threads())

        assert len(results) == 1
        label, worker, clear_ref = results[0]
        assert label == "active chat request"
        assert worker is window.chat_thread

    def test_clear_ref_resets_the_window_attribute(self):
        window = _FakeWindow(_FakeScene())
        window.chat_thread = _fake_worker()

        _, _, clear_ref = next(iter(window._iter_shutdown_threads()))
        clear_ref()

        assert window.chat_thread is None

    def test_save_thread_has_no_clear_ref(self):
        window = _FakeWindow(_FakeScene())
        window.session_manager = type("_S", (), {"save_thread": _fake_worker()})()

        results = list(window._iter_shutdown_threads())

        assert len(results) == 1
        label, worker, clear_ref = results[0]
        assert label == "background save"
        assert clear_ref is None
