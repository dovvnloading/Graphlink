"""Bug-scan finding (worklist item 4): PyCoderNode's result/error handlers
were the one worker-result-handler family with no is_disposed guard at all -
every sibling (_handle_artifact_result/_handle_artifact_error,
_handle_code_sandbox_result/_handle_code_sandbox_error) already had one
(audit finding A3 for the artifact pair).

Concretely: manual-mode execution finishing kicks off a SECOND, window-level
thread (self.pycoder_agent_thread, a PyCoderAgentWorker with no stop() of its
own - an in-flight LLM call can't be cancelled early) to get an AI analysis of
the output. If the node is deleted (dispose()'d) while that second-stage
thread is still running, dispose() has no way to reach or stop it - it only
knows about the node's own worker_thread, not the window's separate
pycoder_agent_thread attribute. When the analysis result (or an error) later
arrives, _handle_pycoder_analysis_result/_handle_pycoder_error used to mutate,
reselect, and save-chat a node no longer on the canvas. The AI-driven mode's
single-stage result handler (_handle_ai_pycoder_result) had the identical gap.

All four handlers now guard on is_disposed, matching the established
sibling pattern exactly.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_pycoder import PyCoderNode
from graphlink_scene import ChatScene
from graphlink_window_actions import WindowActionsMixin


def _window():
    class _FakeWindow(WindowActionsMixin):
        pass

    return _FakeWindow()


def _disposed_node():
    node = MagicMock()
    node.is_disposed = True
    return node


class TestHandleCodeExecutionResultGuardsDisposedNodes:
    def test_ignores_a_disposed_node_and_never_starts_the_analysis_thread(self):
        window = _window()
        node = _disposed_node()

        with patch("graphlink_window_actions.PyCoderAgentWorker") as mock_worker_cls:
            window._handle_code_execution_result("output", node, [])

        node.set_output.assert_not_called()
        mock_worker_cls.assert_not_called()

    def test_ignores_none(self):
        window = _window()

        with patch("graphlink_window_actions.PyCoderAgentWorker") as mock_worker_cls:
            window._handle_code_execution_result("output", None, [])  # must not raise

        mock_worker_cls.assert_not_called()


class TestHandlePycoderAnalysisResultGuardsDisposedNodes:
    def test_ignores_a_disposed_node(self):
        window = _window()
        node = _disposed_node()

        window._handle_pycoder_analysis_result("analysis", node, [], "user msg")

        node.set_ai_analysis.assert_not_called()
        node.set_running_state.assert_not_called()

    def test_ignores_none(self):
        window = _window()

        window._handle_pycoder_analysis_result("analysis", None, [], "user msg")  # must not raise


class TestHandleAiPycoderResultGuardsDisposedNodes:
    def test_ignores_a_disposed_node(self):
        window = _window()
        node = _disposed_node()

        window._handle_ai_pycoder_result({"analysis": "a", "code": "c", "output": "o"}, node, [])

        node.set_code.assert_not_called()
        node.set_output.assert_not_called()
        node.set_ai_analysis.assert_not_called()

    def test_ignores_none(self):
        window = _window()

        window._handle_ai_pycoder_result({"analysis": "a", "code": "c", "output": "o"}, None, [])  # must not raise


class TestHandlePycoderErrorGuardsDisposedNodes:
    """Shared by all three PyCoder worker stages (manual execution,
    AI-driven generation, and the manual-mode analysis follow-up)."""

    def test_ignores_a_disposed_node(self):
        window = _window()
        node = _disposed_node()

        window._handle_pycoder_error("boom", node)

        node.set_ai_analysis.assert_not_called()
        node.set_running_state.assert_not_called()

    def test_ignores_none(self):
        window = _window()

        window._handle_pycoder_error("boom", None)  # must not raise


class TestDeleteMidManualExecutionEndToEnd:
    """The full scenario: node deleted (dispose()'d) while stage 1 (manual
    execution) is still running. When its finished signal arrives late, the
    node must be left completely untouched and stage 2 must never start."""

    def test_stage_one_finishing_after_dispose_does_not_touch_the_node_or_start_stage_two(self):
        window = MagicMock(spec=WindowActionsMixin)
        # Bind the real methods under test onto the MagicMock window so they
        # run for real while everything else on window stays a stub.
        window._handle_code_execution_result = WindowActionsMixin._handle_code_execution_result.__get__(window)

        scene = ChatScene(window=window)
        parent = scene.add_chat_node("parent", is_user=True)
        node = PyCoderNode(parent)
        scene.addItem(node)
        scene.pycoder_nodes.append(node)
        node.set_output("original output")
        node.setSelected(True)

        scene.deleteSelectedItems()
        assert node.is_disposed is True

        with patch("graphlink_window_actions.PyCoderAgentWorker") as mock_worker_cls:
            window._handle_code_execution_result("late output", node, [])

        assert node.get_output() == "original output"
        mock_worker_cls.assert_not_called()
