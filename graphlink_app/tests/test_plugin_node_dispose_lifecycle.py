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
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_conversation_node import ConversationNode
from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode
from graphlink_scene import ChatScene
from graphlink_window_actions import WindowActionsMixin


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
