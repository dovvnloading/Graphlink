"""Artifact's background worker thread (Phase 7 prerequisite, increment 2;
Qt-removal plan R5.2 split) - extracted from
graphlink_plugins/graphlink_plugin_artifact.py, mirroring the same split
graphlink_agents_pycoder.py already did for PyCoder.

R5.2: `ArtifactAgent` itself has since moved OUT of this file into
graphlink_artifact_agent.py, for the exact same reason R4.2 moved
`ChatAgent`/`ChatWorker`/`resolve_branch_system_prompt` out of
graphlink_agents_core.py into graphlink_chat_agent.py (see that module's own
docstring): this file's unconditional `from PySide6.QtCore import QThread,
Signal` - needed only by `ArtifactWorkerThread` below - meant importing
anything from it, including the Qt-free `ArtifactAgent`, pulled PySide6 into
the process. That made `ArtifactAgent` unimportable from backend/ despite
containing zero Qt code itself. `ArtifactAgent` is re-exported here unchanged
so `ArtifactWorkerThread` (which constructs one internally) and the one
legacy production call site (graphlink_window_actions.py's
execute_artifact_node, which constructs an `ArtifactWorkerThread` and assigns
it onto the node as `node.worker_thread`) keep working unmodified - only the
class's true home moved.
"""

from PySide6.QtCore import QThread, Signal

from graphlink_artifact_agent import ArtifactAgent

__all__ = ["ArtifactAgent", "ArtifactWorkerThread"]


class ArtifactWorkerThread(QThread):
    finished = Signal(str, str)  # new_document, ai_message
    error = Signal(str)

    def __init__(self, current_artifact, history):
        super().__init__()
        self.current_artifact = current_artifact
        self.history = history
        self.agent = ArtifactAgent()
        self._is_running = True

    def run(self):
        try:
            if not self._is_running: return
            new_doc, ai_msg = self.agent.get_response(self.current_artifact, self.history)
            if self._is_running:
                self.finished.emit(new_doc, ai_msg)
        except Exception as e:
            if self._is_running:
                self.error.emit(str(e))
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False
