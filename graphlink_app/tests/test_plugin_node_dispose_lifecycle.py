"""Audit finding A3: ArtifactNode and ConversationNode were the two
worker-owning plugin nodes with no dispose() at all - their branches in
ChatScene.deleteSelectedItems called no cleanup, so deleting a node
mid-generate orphaned the live QThread (invisible to
ChatWindow._iter_shutdown_threads once the node left the scene lists) and
let the worker's finished/error signals fire into a node no longer on the
canvas. _handle_artifact_result/_handle_artifact_error were additionally the
only worker result handlers with no disposed-node guard.

Both nodes now define dispose() (Artifact stops its ArtifactWorkerThread;
Conversation cancels its ChatWorkerThread - cancel() is that worker's
cooperative-stop API), both scene delete branches call it via the existing
hasattr gate, and the artifact result handlers guard on is_disposed like
every sibling handler already did.

Follow-up bug-scan finding, part 1: dispose() alone only fired on the
deleteSelectedItems() path. ChatScene.clear() (New Chat / chat-switch)
never called dispose() on ANY plugin node except chart_nodes - it deleted
the C++ object directly. _teardown_items_before_clear() now also calls
dispose() on every worker-owning node list (artifact/conversation/pycoder/
code_sandbox/gitlink), exactly matching the chart_nodes treatment it already
had, so a generation in flight when New Chat happens is stopped
deterministically - not dependent on Python's GC ever running.

Follow-up bug-scan finding, part 2 (discovered while verifying part 1's fix
would even matter): ArtifactNode/ConversationNode/PyCoderNode/CodeSandboxNode
all wire their worker's OWN finished/error(/status/cancelled/log_update/
approval_requested) signals in graphlink_window_actions.py via a lambda
closing over the node/thread (`lambda ..., node=the_node: ...`) on a CUSTOM
Signal. Empirically confirmed: PySide6's GC does not reclaim this shape (a
bound-method connection to the same signal is reclaimed fine), so as long as
those connections stood, BOTH the worker and the node were immortal for the
rest of the process - dispose()'s is_disposed guard was irrelevant because
nothing could ever collect the node to run a GC-time __del__ in the first
place, and any __del__-based backstop (ArtifactNode's own, added alongside
this fix) could never fire either. GitlinkNode was the one sibling that
already disconnected its worker's signals in dispose() - matching that now
in the other four breaks the cycle. ArtifactNode also had a second,
independent instance of the same root pattern purely internal to its own UI
(instruction_input.submit_requested wired via a self-closing lambda,
unrelated to any worker thread) - fixed by using a bound method instead.
"""

import gc
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_agents_artifact import ArtifactWorkerThread
from graphlink_agents_code_sandbox import CodeSandboxExecutionWorker
from graphlink_agents_core import ChatWorkerThread
from graphlink_agents_pycoder import CodeExecutionWorker
from graphlink_conversation_node import ConversationNode
from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode
from graphlink_plugins.graphlink_plugin_code_sandbox import CodeSandboxNode
from graphlink_pycoder import PyCoderNode
from graphlink_scene import ChatScene
from graphlink_window_actions import WindowActionsMixin
import weakref


def _flush_deferred_deletes():
    # worker.deleteLater() (called by dispose()) posts a QEvent.DeferredDelete
    # rather than deleting synchronously - a live running app's event loop is
    # always pumping one, so this resolves near-instantly there, but this
    # isolated test never runs one. processEvents() alone was NOT sufficient
    # to flush it (empirically confirmed); sendPostedEvents targeting
    # DeferredDelete specifically is what actually processes it. This is a
    # benign QThread-teardown timing artifact of the test harness, not a
    # reference-cycle leak (confirmed via gc.get_referrers: zero Python-level
    # referrers on the "still alive" object before this flush).
    gc.collect()
    _APP.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def _make_scene():
    window = MagicMock()
    scene = ChatScene(window=window)
    window.chat_view.scene.return_value = scene
    return scene


class TestArtifactNodeDispose:
    def test_dispose_stops_a_running_worker_and_clears_the_reference(self):
        node = ArtifactNode(parent_node=None)
        worker = MagicMock()
        worker.isRunning.return_value = True
        node.worker_thread = worker

        node.dispose()

        worker.stop.assert_called_once()
        assert node.worker_thread is None
        assert node.is_disposed is True

    def test_dispose_is_idempotent(self):
        node = ArtifactNode(parent_node=None)
        node.dispose()
        node.worker_thread = MagicMock()  # a second dispose must not touch this

        node.dispose()

        node.worker_thread.stop.assert_not_called()

    def test_scene_delete_path_disposes_the_node(self):
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = ArtifactNode(parent)
        scene.addItem(node)
        scene.artifact_nodes.append(node)
        worker = MagicMock()
        worker.isRunning.return_value = True
        node.worker_thread = worker
        node.setSelected(True)

        scene.deleteSelectedItems()

        worker.stop.assert_called_once()
        assert node.is_disposed is True
        assert node not in scene.artifact_nodes


class TestConversationNodeDispose:
    def test_dispose_cancels_a_running_worker_and_clears_the_reference(self):
        node = ConversationNode(parent_node=None)
        worker = MagicMock()
        worker.isRunning.return_value = True
        node.worker_thread = worker

        node.dispose()

        worker.cancel.assert_called_once()
        assert node.worker_thread is None
        assert node.is_disposed is True

    def test_dispose_is_idempotent(self):
        node = ConversationNode(parent_node=None)
        node.dispose()
        node.worker_thread = MagicMock()

        node.dispose()

        node.worker_thread.cancel.assert_not_called()

    def test_scene_delete_path_disposes_the_node(self):
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = ConversationNode(parent)
        scene.addItem(node)
        scene.conversation_nodes.append(node)
        worker = MagicMock()
        worker.isRunning.return_value = True
        node.worker_thread = worker
        node.setSelected(True)

        scene.deleteSelectedItems()

        worker.cancel.assert_called_once()
        assert node.is_disposed is True
        assert node not in scene.conversation_nodes


class TestArtifactNodeDelBackstop:
    """ChatScene.clear() (New Chat / chat-switch) never calls dispose() on
    plugin nodes - it deletes the C++ object directly, bypassing every
    hasattr(...).dispose() gate in deleteSelectedItems. dispose() alone (A3)
    therefore did nothing for that path; only a GC-time __del__ can still
    catch it, mirroring CodeSandboxNode.__del__."""

    def test_del_stops_a_running_worker_when_the_node_is_garbage_collected(self):
        node = ArtifactNode(parent_node=None)
        worker = MagicMock()
        worker.isRunning.return_value = True
        node.worker_thread = worker

        del node
        gc.collect()  # ArtifactNode/HoverAnimationMixin's timer.timeout->bound
        # method is a reference cycle - refcounting alone won't collect it.

        worker.stop.assert_called_once()

    def test_del_swallows_exceptions_raised_during_interpreter_shutdown(self):
        # During interpreter shutdown the underlying C++ QThread/QObject may
        # already be gone, so dispose()'s worker.isRunning() can itself raise
        # (audit finding B5's exact hazard) - __del__ must not propagate that.
        node = ArtifactNode(parent_node=None)
        worker = MagicMock()
        worker.isRunning.side_effect = RuntimeError("Internal C++ object already deleted")
        node.worker_thread = worker

        node.__del__()  # must not raise

    def test_del_after_an_explicit_dispose_does_not_touch_the_worker_again(self):
        node = ArtifactNode(parent_node=None)
        node.dispose()
        node.worker_thread = MagicMock()  # __del__'s dispose() must no-op

        node.__del__()

        node.worker_thread.stop.assert_not_called()

    def test_new_chat_mid_generate_end_to_end_stops_the_orphaned_worker(self):
        # The actual bug: start a generation, then New Chat (scene.clear())
        # before it finishes. Without __del__, nothing stops this worker -
        # scene.clear() has already emptied scene.artifact_nodes, so even
        # ChatWindow._iter_shutdown_threads at app-close can no longer find it.
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = ArtifactNode(parent)
        scene.addItem(node)
        scene.artifact_nodes.append(node)
        worker = MagicMock()
        worker.isRunning.return_value = True
        node.worker_thread = worker

        scene.clear()
        assert node not in scene.artifact_nodes
        del node
        gc.collect()

        worker.stop.assert_called_once()


class TestArtifactHandlersGuardDisposedNodes:
    def _window(self):
        class _FakeWindow(WindowActionsMixin):
            pass

        return _FakeWindow()

    def test_result_handler_ignores_a_disposed_node(self):
        window = self._window()
        node = MagicMock()
        node.is_disposed = True

        # Guard missing => set_artifact_content is called (and the handler
        # would then crash on the fake window's absent save_chat).
        window._handle_artifact_result("new doc", "a message", node)

        node.set_artifact_content.assert_not_called()
        node.add_chat_message.assert_not_called()

    def test_result_handler_ignores_none(self):
        window = self._window()

        window._handle_artifact_result("new doc", "a message", None)  # must not raise

    def test_error_handler_ignores_a_disposed_node(self):
        window = self._window()
        node = MagicMock()
        node.is_disposed = True

        window._handle_artifact_error("boom", node)

        node.add_chat_message.assert_not_called()

    def test_delete_mid_generate_end_to_end_does_not_touch_the_removed_node(self):
        # The full A3 scenario: node deleted while its worker runs; the
        # worker's finished callback then arrives. The node must be left
        # completely untouched.
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = ArtifactNode(parent)
        scene.addItem(node)
        scene.artifact_nodes.append(node)
        worker = MagicMock()
        worker.isRunning.return_value = True
        node.worker_thread = worker
        node.setSelected(True)
        scene.deleteSelectedItems()

        window = self._window()
        before = node.get_artifact_content()
        window._handle_artifact_result("late result", "late message", node)

        assert node.get_artifact_content() == before


class TestSceneClearDisposesEveryWorkerOwningNodeType:
    """The primary fix: _teardown_items_before_clear() must call dispose() on
    every worker-owning node list, not just chart_nodes. This is what makes
    New Chat / chat-switch stop an in-flight generation deterministically,
    instead of depending on whether/when Python's GC ever collects the
    abandoned node."""

    def test_scene_clear_calls_dispose_on_all_five_worker_owning_node_types(self):
        scene = _make_scene()
        nodes = []
        for list_name, node_cls in (
            ("artifact_nodes", ArtifactNode),
            ("conversation_nodes", ConversationNode),
            ("pycoder_nodes", PyCoderNode),
            ("code_sandbox_nodes", CodeSandboxNode),
        ):
            parent = scene.add_chat_node(f"parent-{list_name}", is_user=True)
            node = node_cls(parent)
            scene.addItem(node)
            getattr(scene, list_name).append(node)
            node.dispose = MagicMock(wraps=node.dispose)
            nodes.append(node)

        scene.clear()

        for node in nodes:
            node.dispose.assert_called_once()

    def test_scene_clear_stops_a_running_worker_deterministically_no_gc_needed(self):
        # The concrete payoff: an in-flight generation is stopped the instant
        # New Chat happens, not "eventually, whenever gc.collect() runs".
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = ArtifactNode(parent)
        scene.addItem(node)
        scene.artifact_nodes.append(node)
        worker = MagicMock()
        worker.isRunning.return_value = True
        node.worker_thread = worker

        scene.clear()

        worker.stop.assert_called_once()
        assert node.is_disposed is True


class TestDisposeBreaksTheWindowActionsSignalCycle:
    """graphlink_window_actions.py wires each worker's OWN finished/error(/...)
    signals via a lambda closing over the node/thread on a custom Signal -
    empirically confirmed uncollectable by PySide6's GC (unlike the same
    shape connected to a bound method). Reproduces that exact wiring with the
    REAL worker classes (no mocks - a mock would silently absorb the
    connect() call and hide the leak) and proves dispose() breaks the cycle:
    both the node and the worker become collectible afterward."""

    def test_artifact_node_and_worker_collect_after_dispose(self):
        node = ArtifactNode(parent_node=None)
        worker = ArtifactWorkerThread("current doc", [])
        node.worker_thread = worker
        # Exact shape of execute_artifact_node's wiring (graphlink_window_actions.py).
        worker.finished.connect(lambda doc, msg, n=node: None)
        worker.error.connect(lambda err, n=node: None)
        worker.finished.connect(lambda _doc, _msg, thread=worker, n=node: None)
        worker.error.connect(lambda _err, thread=worker, n=node: None)

        node.dispose()

        node_ref, worker_ref = weakref.ref(node), weakref.ref(worker)
        del node, worker
        _flush_deferred_deletes()
        assert node_ref() is None, "ArtifactNode still alive - dispose() did not break the cycle"
        assert worker_ref() is None, "ArtifactWorkerThread still alive - dispose() did not break the cycle"

    def test_conversation_node_and_worker_collect_after_dispose(self):
        node = ConversationNode(parent_node=None)
        worker = ChatWorkerThread(agent=MagicMock(), conversation_history=[], current_node=None)
        node.worker_thread = worker
        # Exact shape of handle_conversation_node_request's wiring.
        worker.finished.connect(lambda msg, n=node, thread=worker: None)
        worker.status.connect(lambda *_: None)  # the one bound-method-safe connection in production
        worker.error.connect(lambda err, n=node, thread=worker: None)
        worker.cancelled.connect(lambda n=node, thread=worker: None)
        worker.finished.connect(lambda _msg, n=node, thread=worker: None)
        worker.error.connect(lambda _err, n=node, thread=worker: None)
        worker.cancelled.connect(lambda n=node, thread=worker: None)

        node.dispose()

        node_ref, worker_ref = weakref.ref(node), weakref.ref(worker)
        del node, worker
        _flush_deferred_deletes()
        assert node_ref() is None, "ConversationNode still alive - dispose() did not break the cycle"
        assert worker_ref() is None, "ChatWorkerThread still alive - dispose() did not break the cycle"

    def test_pycoder_node_and_manual_worker_collect_after_dispose(self):
        node = PyCoderNode(parent_node=None)
        worker = CodeExecutionWorker("print(1)", repl=MagicMock())
        node.worker_thread = worker
        # Exact shape of run_pycoder_node's manual-mode wiring.
        worker.finished.connect(lambda output, history=[]: None)
        worker.error.connect(lambda error_msg, n=node: None)
        worker.finished.connect(lambda _output, thread=worker, n=node: None)
        worker.error.connect(lambda _error, thread=worker, n=node: None)

        node.dispose()

        node_ref, worker_ref = weakref.ref(node), weakref.ref(worker)
        del node, worker
        _flush_deferred_deletes()
        assert node_ref() is None, "PyCoderNode still alive - dispose() did not break the cycle"
        assert worker_ref() is None, "CodeExecutionWorker still alive - dispose() did not break the cycle"

    def test_code_sandbox_node_and_worker_collect_after_dispose(self):
        node = CodeSandboxNode(parent_node=None)
        worker = CodeSandboxExecutionWorker("sandbox-1", "", [], "")
        node.worker_thread = worker
        # Exact shape of the sandbox run path's wiring.
        worker.log_update.connect(lambda *_: None)  # bound-method-safe in production
        worker.terminal_chunk.connect(lambda *_: None)  # bound-method-safe in production
        worker.approval_requested.connect(lambda code, reqs, w=worker, n=node: None)
        worker.finished.connect(lambda result, n=node, history=[], mode="generate": None)
        worker.error.connect(lambda error_msg, n=node: None)
        worker.finished.connect(lambda _result, thread=worker, n=node: None)
        worker.error.connect(lambda _error, thread=worker, n=node: None)

        node.dispose()

        node_ref, worker_ref = weakref.ref(node), weakref.ref(worker)
        del node, worker
        _flush_deferred_deletes()
        assert node_ref() is None, "CodeSandboxNode still alive - dispose() did not break the cycle"
        assert worker_ref() is None, "CodeSandboxExecutionWorker still alive - dispose() did not break the cycle"
