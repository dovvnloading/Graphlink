"""Plugin package for concrete plugin nodes and plugin UI infrastructure.

Deliberately has no eager re-exports: every consumer in this codebase imports
directly from the specific submodule it needs (e.g. `from graphite_plugins.
graphite_plugin_artifact import ArtifactNode`), and eagerly importing every plugin
here just to populate this package's own namespace creates needless circular-import
risk - any submodule of this package that itself needs a shared, non-plugin-specific
module (e.g. graphite_plugin_context_menu.py depends on nothing plugin-specific, but
importing *any* name from this package used to run this file first, which pulled in
every plugin's full dependency graph before that one small import could complete).
"""
