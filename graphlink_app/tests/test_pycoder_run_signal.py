"""Phase 7 prerequisite increment 3: PyCoderNode's run-dispatch normalized to
the request-signal contract.

PyCoderNode was the ONE plugin node whose run/generate dispatch reached
directly through the canvas (`self.scene().window.execute_pycoder_node(self)`)
instead of emitting a Signal like every other node (WebNode.run_clicked,
CodeSandboxNode.sandbox_requested, ConversationNode.ai_request_sent,
GitlinkNode.gitlink_requested, ArtifactNode.artifact_requested). A real,
material correction to the original recon surfaced while implementing this:
a bare `Signal` class attribute requires the class to be a QObject, which
`QGraphicsItem` (PyCoderNode's old base class) is NOT - every other plugin
node already extends `QGraphicsObject` (QGraphicsItem + QObject) for exactly
this reason. This increment therefore also changes PyCoderNode's base class
from QGraphicsItem to QGraphicsObject, matching the other 6 node types
exactly, before it can declare `run_clicked = Signal(object)` at all.

The `setCurrentNode` reach-through in mousePressEvent is a separate,
codebase-wide idiom on every node type and is explicitly OUT of scope here -
only the run/generate dispatch changes.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QGraphicsObject

_APP = QApplication.instance() or QApplication([])

from graphlink_pycoder import PyCoderMode, PyCoderNode
from graphlink_scene import ChatScene
from graphlink_session.deserializers import SceneDeserializer
from graphlink_session.serializers import SceneSerializer


def _make_window_and_scene():
    window = MagicMock()
    scene = ChatScene(window=window)
    window.chat_view.scene.return_value = scene
    return window, scene


class TestBaseClassPrerequisite:
    def test_pycoder_node_is_now_a_qgraphicsobject_matching_every_other_plugin_node(self):
        node = PyCoderNode(parent_node=None)

        assert isinstance(node, QGraphicsObject)

    def test_pycoder_node_declares_the_run_clicked_signal(self):
        node = PyCoderNode(parent_node=None)

        assert hasattr(node, "run_clicked")


class TestRunDispatchContract:
    def test_run_click_emits_the_signal_with_the_node(self):
        node = PyCoderNode(parent_node=None)
        received = []
        node.run_clicked.connect(received.append)

        node._on_run_clicked()

        assert received == [node]

    def test_run_click_handler_does_not_reach_through_the_scene_directly(self):
        # Regression guard for the exact bug this increment fixes: if a scene
        # is present with a `window` attribute, _on_run_clicked must NOT call
        # anything on it directly - it must go through the signal only.
        node = PyCoderNode(parent_node=None)
        fake_window = MagicMock()
        fake_scene = MagicMock()
        fake_scene.window = fake_window
        node.scene = MagicMock(return_value=fake_scene)

        node._on_run_clicked()

        fake_window.execute_pycoder_node.assert_not_called()

    def test_run_click_switches_to_the_terminal_tab_when_manual_and_idle(self):
        node = PyCoderNode(parent_node=None, mode=PyCoderMode.MANUAL)
        node.is_running = False
        node.tabs.setCurrentIndex = MagicMock()

        node._on_run_clicked()

        node.tabs.setCurrentIndex.assert_called_once_with(1)

    def test_run_click_switches_to_the_code_tab_when_ai_driven_and_idle(self):
        node = PyCoderNode(parent_node=None, mode=PyCoderMode.AI_DRIVEN)
        node.is_running = False
        node.tabs.setCurrentIndex = MagicMock()

        node._on_run_clicked()

        node.tabs.setCurrentIndex.assert_called_once_with(0)

    def test_run_click_does_not_switch_tabs_while_already_running(self):
        # Preserves the exact pre-existing side-effect condition (not self.is_running) -
        # only the dispatch mechanism changed, not this behavior.
        node = PyCoderNode(parent_node=None)
        node.is_running = True
        node.tabs.setCurrentIndex = MagicMock()

        node._on_run_clicked()

        node.tabs.setCurrentIndex.assert_not_called()

    def test_run_click_still_emits_while_running_so_the_window_can_stop_it(self):
        # execute_pycoder_node's own existing branch (stop if already running)
        # depends on the signal still firing even when is_running is True.
        node = PyCoderNode(parent_node=None)
        node.is_running = True
        received = []
        node.run_clicked.connect(received.append)

        node._on_run_clicked()

        assert received == [node]


class TestDeserializerWiresTheSignal:
    def test_restored_node_has_run_clicked_connected_to_the_window_slot(self):
        window, scene = _make_window_and_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = PyCoderNode(parent, mode=PyCoderMode.MANUAL)
        node.set_code("print('hi')")
        scene.addItem(node)
        scene.pycoder_nodes.append(node)
        parent_payload = SceneSerializer(window).serialize_node(parent, [parent, node])
        node_payload = SceneSerializer(window).serialize_node(node, [parent, node])

        target_window, target_scene = _make_window_and_scene()
        target_window.execute_pycoder_node = MagicMock()
        deserializer = SceneDeserializer(target_window)
        restored_parent = deserializer.deserialize_node(0, parent_payload, {})
        deserializer.deserialize_node(1, node_payload, {0: restored_parent})

        assert len(target_scene.pycoder_nodes) == 1
        restored = target_scene.pycoder_nodes[0]
        restored.run_clicked.emit(restored)

        target_window.execute_pycoder_node.assert_called_once_with(restored)
