"""Plugin-node dispose lifecycle tests (audit finding A3 and follow-ups).

ConversationNode was one of the worker-owning plugin nodes with no dispose() at
all - its branch in ChatScene.deleteSelectedItems called no cleanup, so
deleting a node mid-generate orphaned the live QThread (invisible to
ChatWindow._iter_shutdown_threads once the node left the scene lists) and let
the worker's finished/error signals fire into a node no longer on the canvas.
ConversationNode now defines dispose() (cancels its ChatWorkerThread -
cancel() is that worker's cooperative-stop API), and the scene delete branch
calls it via the existing hasattr gate.

Follow-up bug-scan finding, part 1: dispose() alone only fired on the
deleteSelectedItems() path. ChatScene.clear() (New Chat / chat-switch) never
called dispose() on ANY plugin node except chart_nodes - it deleted the C++
object directly. _teardown_items_before_clear() now also calls dispose() on
every worker-owning node list, exactly matching the chart_nodes treatment it
already had, so a generation in flight when New Chat happens is stopped
deterministically - not dependent on Python's GC ever running.

Follow-up bug-scan finding, part 2 (discovered while verifying part 1's fix
would even matter): ConversationNode wires its worker's OWN finished/error/
status/cancelled signals in graphlink_window_actions.py via a lambda closing
over the node/thread (`lambda ..., node=the_node: ...`) on a CUSTOM Signal.
Empirically confirmed: PySide6's GC does not reclaim this shape (a
bound-method connection to the same signal is reclaimed fine), so as long as
those connections stood, BOTH the worker and the node were immortal for the
rest of the process - dispose()'s is_disposed guard was irrelevant because
nothing could ever collect the node to run a GC-time __del__ in the first
place. dispose() disconnecting the worker's signals breaks that cycle.

Note: this file used to also cover ArtifactNode/PyCoderNode/CodeSandboxNode/
GitlinkNode/WebNode dispose lifecycle (they shared the same bug and the same
fix). That coverage was removed along with those node classes as part of the
Qt-removal cleanup - they were reimplemented Qt-free in the new FastAPI+React
app. ConversationNode is untouched and remains fully in scope here.
"""

import gc
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_agents_core import ChatWorkerThread
from graphlink_conversation_node import ConversationNode
from graphlink_scene import ChatScene
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


class TestSceneClearDisposesEveryWorkerOwningNodeType:
    """The primary fix: _teardown_items_before_clear() must call dispose() on
    every worker-owning node list. ConversationNode is the only worker-owning
    node type remaining in this scene (Artifact/PyCoder/CodeSandbox were
    deleted along with their node classes) - this is what makes New Chat /
    chat-switch stop an in-flight generation deterministically, instead of
    depending on whether/when Python's GC ever collects the abandoned node."""

    def test_scene_clear_calls_dispose_on_conversation_nodes(self):
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = ConversationNode(parent)
        scene.addItem(node)
        scene.conversation_nodes.append(node)
        node.dispose = MagicMock(wraps=node.dispose)

        scene.clear()

        node.dispose.assert_called_once()

    def test_scene_clear_stops_a_running_worker_deterministically_no_gc_needed(self):
        # The concrete payoff: an in-flight generation is stopped the instant
        # New Chat happens, not "eventually, whenever gc.collect() runs".
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = ConversationNode(parent)
        scene.addItem(node)
        scene.conversation_nodes.append(node)
        worker = MagicMock()
        worker.isRunning.return_value = True
        node.worker_thread = worker

        scene.clear()

        worker.cancel.assert_called_once()
        assert node.is_disposed is True


class TestDisposeBreaksTheWindowActionsSignalCycle:
    """graphlink_window_actions.py wires each worker's OWN finished/error(/...)
    signals via a lambda closing over the node/thread on a custom Signal -
    empirically confirmed uncollectable by PySide6's GC (unlike the same
    shape connected to a bound method). Reproduces that exact wiring with the
    REAL worker class (no mocks - a mock would silently absorb the connect()
    call and hide the leak) and proves dispose() breaks the cycle: both the
    node and the worker become collectible afterward."""

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
