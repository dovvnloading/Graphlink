"""Phase 7 prerequisite increment 2: ArtifactAgent/ArtifactWorkerThread
extracted out of graphlink_plugins/graphlink_plugin_artifact.py into a new
graphlink_agents_artifact.py, mirroring the split graphlink_agents_pycoder.py
already did for PyCoder.

A pure relocation, not a rewrite - both classes were already Qt-free/
Qt-only-for-threading and self-contained; ArtifactNode never referenced
either by name. This file proves the module boundary landed exactly where
the recon said it would: the new module holds both classes with the
underlying import graph intact, the old module holds neither, and the one
production construction site (graphlink_window_actions.execute_artifact_node)
still gets a real, working ArtifactWorkerThread from the new location.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_agents_artifact
import graphlink_plugins.graphlink_plugin_artifact as plugin_artifact_module
from graphlink_agents_artifact import ArtifactAgent, ArtifactWorkerThread


class TestModuleBoundary:
    def test_the_new_module_holds_both_classes(self):
        assert graphlink_agents_artifact.ArtifactAgent is ArtifactAgent
        assert graphlink_agents_artifact.ArtifactWorkerThread is ArtifactWorkerThread

    def test_the_worker_threads_own_class_attribute_still_resolves_to_the_new_module(self):
        # ArtifactWorkerThread constructs its own ArtifactAgent internally -
        # confirm that reference resolves within the new module, not a stale
        # import of the old (now nonexistent) location.
        worker = ArtifactWorkerThread("doc", [])
        assert isinstance(worker.agent, ArtifactAgent)
        assert type(worker.agent).__module__ == "graphlink_agents_artifact"

    def test_neither_class_remains_on_the_plugin_module(self):
        assert not hasattr(plugin_artifact_module, "ArtifactAgent")
        assert not hasattr(plugin_artifact_module, "ArtifactWorkerThread")

    def test_the_plugin_modules_own_classes_are_unaffected(self):
        # ArtifactNode/ArtifactConnectionItem/ArtifactInstructionInput stay put -
        # only the agent/worker moved.
        assert hasattr(plugin_artifact_module, "ArtifactNode")
        assert hasattr(plugin_artifact_module, "ArtifactConnectionItem")
        assert hasattr(plugin_artifact_module, "ArtifactInstructionInput")

    def test_the_plugin_module_no_longer_imports_api_provider_or_config_directly(self):
        # Both became dead once ArtifactAgent.get_response (the only consumer
        # in this file) moved out - a real dead-import cleanup, not just a
        # class move.
        assert not hasattr(plugin_artifact_module, "api_provider")
        assert not hasattr(plugin_artifact_module, "config")

    def test_the_plugin_module_no_longer_imports_qthread(self):
        # QThread's only use in this file was as ArtifactWorkerThread's base class.
        assert not hasattr(plugin_artifact_module, "QThread")


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
