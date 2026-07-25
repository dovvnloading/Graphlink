"""Phase 7 prerequisite increment 2: ArtifactAgent/ArtifactWorkerThread
extracted out of graphlink_plugins/graphlink_plugin_artifact.py into a new
graphlink_agents_artifact.py, mirroring the split graphlink_agents_pycoder.py
already did for PyCoder.

R5.2: ArtifactAgent has since moved AGAIN, out of graphlink_agents_artifact.py
into a Qt-free graphlink_artifact_agent.py (mirroring R4.2's chat-agent
split), because graphlink_agents_artifact.py's own `from PySide6.QtCore
import QThread, Signal` (needed only by ArtifactWorkerThread) pulled Qt into
any importer, including ArtifactAgent despite it containing zero Qt code.
graphlink_agents_artifact.py re-exports ArtifactAgent unchanged for backward
compatibility.

R5-closeout: graphlink_plugins/graphlink_plugin_artifact.py (ArtifactNode)
was deleted once the Artifact plugin was fully ported to the Qt-free
backend/frontend stack. The tests that asserted things about that module
(ArtifactNode/ArtifactConnectionItem staying put, dead imports being gone
from it) no longer have a subject and were removed with it. The tests below
still protect real, currently-live code: ArtifactAgent's Qt-free home and
ArtifactWorkerThread's re-export/construction contract.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_agents_artifact
import graphlink_artifact_agent
from graphlink_agents_artifact import ArtifactAgent, ArtifactWorkerThread


class TestModuleBoundary:
    def test_the_new_module_holds_both_classes(self):
        assert graphlink_agents_artifact.ArtifactAgent is ArtifactAgent
        assert graphlink_agents_artifact.ArtifactWorkerThread is ArtifactWorkerThread

    def test_artifact_agent_real_home_is_the_qt_free_module_and_the_old_module_only_re_exports_it(self):
        # R5.2: ArtifactAgent's real home is now graphlink_artifact_agent.py
        # (Qt-free forever); graphlink_agents_artifact.py's own ArtifactAgent
        # name is the SAME object, re-exported for backward compatibility,
        # not a second independent class.
        assert graphlink_agents_artifact.ArtifactAgent is graphlink_artifact_agent.ArtifactAgent

    def test_the_worker_threads_own_class_attribute_still_resolves_to_the_new_module(self):
        # ArtifactWorkerThread constructs its own ArtifactAgent internally -
        # confirm that reference resolves to ArtifactAgent's real (Qt-free)
        # home, not a stale import of an old (now nonexistent) location.
        worker = ArtifactWorkerThread("doc", [])
        assert isinstance(worker.agent, ArtifactAgent)
        assert type(worker.agent).__module__ == "graphlink_artifact_agent"


class TestOwnershipContractUnchanged:
    def test_construction_site_shape_is_unchanged_only_the_import_path_moved(self):
        # graphlink_window_actions.execute_artifact_node constructs
        # ArtifactWorkerThread(current_doc, trimmed_history) and assigns it onto
        # node.worker_thread - reproduce that exact call shape against the new
        # module and confirm it still behaves identically (both positional
        # args accepted, agent auto-constructed, not yet running).
        worker = ArtifactWorkerThread("current document text", [{"role": "user", "content": "hi"}])

        assert worker.current_artifact == "current document text"
        assert worker.history == [{"role": "user", "content": "hi"}]
        assert worker.isRunning() is False
        assert worker._is_running is True

    def test_stop_flips_the_running_flag_without_starting_the_thread(self):
        worker = ArtifactWorkerThread("doc", [])

        worker.stop()

        assert worker._is_running is False
