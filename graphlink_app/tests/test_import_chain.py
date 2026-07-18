"""Import-chain regression tests.

Guards against two classes of bug found while resolving the dual root-shim/package
import paths, where core files reached plugin classes through root-level shims while
the portal imported the package directly:

1. A real circular import surfaced when graphlink_plugin_context_menu.py moved into
   graphlink_plugins/: graphlink_plugins/__init__.py used to eagerly import every
   plugin submodule, so importing *anything* from the package (even a name with no
   plugin dependencies, like PluginNodeContextMenu) pulled in the full plugin
   dependency graph - which circled back through graphlink_connections.py to
   graphlink_pycoder.py, the very module trying to do the import. Fixed by making
   graphlink_plugins/__init__.py import nothing (nothing in the codebase relied on its
   re-exports anyway - every consumer already imports from the specific submodule it
   needs).
2. The 9 root-level compatibility shims (graphlink_plugin_artifact.py etc.) were
   deleted once every consumer was confirmed to import via the graphlink_plugins.*
   package path instead.

These tests import each entry point in a fresh subprocess-like fashion (via plain
import statements at module scope, which pytest itself will fail to collect this file
if any of them raise) covering every module known to sit on one side or the other of
that cycle.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_conversation_node  # noqa: F401 - imports graphlink_plugins.graphlink_plugin_context_menu
import graphlink_html_view  # noqa: F401
import graphlink_plugins  # noqa: F401
import graphlink_pycoder  # noqa: F401 - this is the module that was on the circular side
import graphlink_scene  # noqa: F401
import graphlink_web  # noqa: F401
import graphlink_window  # noqa: F401
import graphlink_window_actions  # noqa: F401
import graphlink_window_navigation  # noqa: F401
from graphlink_plugins.graphlink_plugin_context_menu import PluginNodeContextMenu
from graphlink_session import deserializers, scene_index, serializers


def test_graphlink_plugins_package_has_no_eager_reexports():
    # If this package's __init__.py ever goes back to eagerly importing every plugin
    # submodule, the circular import described in the module docstring comes back.
    assert not hasattr(graphlink_plugins, "ArtifactNode"), (
        "graphlink_plugins/__init__.py appears to eagerly re-export plugin classes "
        "again - this previously caused a circular import via graphlink_pycoder.py. "
        "The package __init__.py must stay import-free."
    )


def test_root_level_plugin_shim_files_are_gone():
    app_root = Path(__file__).resolve().parents[1]
    shim_names = [
        "graphlink_plugin_artifact.py",
        "graphlink_plugin_code_review.py",
        "graphlink_plugin_code_sandbox.py",
        "graphlink_plugin_context_menu.py",
        "graphlink_plugin_gitlink.py",
        "graphlink_plugin_graph_diff.py",
        "graphlink_plugin_picker.py",
        "graphlink_plugin_portal.py",
        "graphlink_plugin_quality_gate.py",
        "graphlink_plugin_workflow.py",
        # Not "shims" in the same sense (these predated graphlink_plugins/ entirely
        # rather than being a re-export left behind after a package move), but the
        # same "root-level plugin implementation file should no longer exist" check
        # applies now that Graphlink-Reasoning moved into graphlink_plugins/reasoning/
        # and graphlink_plugins/graphlink_plugin_reasoning.py.
        "graphlink_reasoning.py",
        "graphlink_agents_reasoning.py",
    ]
    for name in shim_names:
        assert not (app_root / name).exists(), f"{name} should have been removed from the app root"


def test_plugin_node_context_menu_importable_from_package():
    assert PluginNodeContextMenu is not None
