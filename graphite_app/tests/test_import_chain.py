"""Import-chain regression tests (Phase 5).

Guards against two classes of bug found while resolving the dual root-shim/package
import paths (doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 1.5):

1. A real circular import surfaced when graphite_plugin_context_menu.py moved into
   graphite_plugins/: graphite_plugins/__init__.py used to eagerly import every
   plugin submodule, so importing *anything* from the package (even a name with no
   plugin dependencies, like PluginNodeContextMenu) pulled in the full plugin
   dependency graph - which circled back through graphite_connections.py to
   graphite_pycoder.py, the very module trying to do the import. Fixed by making
   graphite_plugins/__init__.py import nothing (nothing in the codebase relied on its
   re-exports anyway - every consumer already imports from the specific submodule it
   needs).
2. The 9 root-level compatibility shims (graphite_plugin_artifact.py etc.) were
   deleted once every consumer was confirmed to import via the graphite_plugins.*
   package path instead.

These tests import each entry point in a fresh subprocess-like fashion (via plain
import statements at module scope, which pytest itself will fail to collect this file
if any of them raise) covering every module known to sit on one side or the other of
that cycle.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphite_conversation_node  # noqa: F401 - imports graphite_plugins.graphite_plugin_context_menu
import graphite_html_view  # noqa: F401
import graphite_plugins  # noqa: F401
import graphite_pycoder  # noqa: F401 - this is the module that was on the circular side
import graphite_reasoning  # noqa: F401
import graphite_scene  # noqa: F401
import graphite_web  # noqa: F401
import graphite_window  # noqa: F401
import graphite_window_actions  # noqa: F401
import graphite_window_navigation  # noqa: F401
from graphite_plugins.graphite_plugin_context_menu import PluginNodeContextMenu
from graphite_session import deserializers, scene_index, serializers


def test_graphite_plugins_package_has_no_eager_reexports():
    # If this package's __init__.py ever goes back to eagerly importing every plugin
    # submodule, the circular import described in the module docstring comes back.
    assert not hasattr(graphite_plugins, "ArtifactNode"), (
        "graphite_plugins/__init__.py appears to eagerly re-export plugin classes "
        "again - this previously caused a circular import via graphite_pycoder.py. "
        "See doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 1.5."
    )


def test_root_level_plugin_shim_files_are_gone():
    app_root = Path(__file__).resolve().parents[1]
    shim_names = [
        "graphite_plugin_artifact.py",
        "graphite_plugin_code_review.py",
        "graphite_plugin_code_sandbox.py",
        "graphite_plugin_context_menu.py",
        "graphite_plugin_gitlink.py",
        "graphite_plugin_graph_diff.py",
        "graphite_plugin_picker.py",
        "graphite_plugin_portal.py",
        "graphite_plugin_quality_gate.py",
        "graphite_plugin_workflow.py",
    ]
    for name in shim_names:
        assert not (app_root / name).exists(), f"{name} should have been removed from the app root"


def test_plugin_node_context_menu_importable_from_package():
    assert PluginNodeContextMenu is not None
