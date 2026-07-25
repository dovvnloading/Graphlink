"""Tests for ChatWindow._iter_shutdown_threads()/_shutdown_background_threads().

Regression coverage for a bug in the hand-maintained shutdown thread list:
it still named main_window.X_thread attributes (reasoning_thread,
workflow_thread, graph_diff_thread, quality_gate_thread, code_review_thread,
sandbox_thread, artifact_thread, gitlink_thread, code_exec_thread, and
pycoder_exec_thread) that were removed from the window entirely once those
plugins moved to a per-node node.worker_thread attribute. They were harmless
no-ops (getattr(self, name, None) always returned None) but misleading.

R5-closeout: the per-node Code Sandbox/Artifact/PyCoder/Gitlink worker-thread
enumeration this file used to also cover was removed along with those legacy
Qt node classes themselves (ported to the Qt-free backend/frontend stack) -
graphlink_scene.py no longer has code_sandbox_nodes/artifact_nodes/
pycoder_nodes/gitlink_nodes lists, and graphlink_window.py's
_iter_shutdown_threads() no longer walks them. Only the window-level-attribute
coverage remains relevant.

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
    pass


class _FakeChatView:
    def __init__(self, scene):
        self._scene = scene

    def scene(self):
        return self._scene


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
