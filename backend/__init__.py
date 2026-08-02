"""Graphlink backend (Qt-removal plan R0, doc/QT_REMOVAL_PLAN.md).

The Python half of the target architecture: FastAPI + a WebSocket event bus
carrying full-state snapshots and intents between the domain services and the
single React SPA. This package is the direct successor of the QWebChannel
IslandBridge layer and deliberately preserves its wire semantics (versioned
full-state envelopes, named intents) so the existing island payload schemas
and generated TS types retarget without redesign.

Zero Qt imports are permitted anywhere under this package - enforced by
tests/test_no_qt_anywhere.py.
"""

BACKEND_VERSION = "0.1.0"

# R7.2: the Qt-free domain modules backend/ depends on (api_provider,
# graphlink_task_config, graphlink_settings_store, the graphlink_plugins/ package,
# ...) used to live inside graphlink_app/, reached via a sys.path.insert here.
# They now sit as ordinary siblings of this package at the repo root, so no
# path manipulation is needed: whatever already put this package's own parent
# directory on sys.path (running graphlink_desktop.py, or `python -m pytest`
# from repo root) already makes those siblings importable by the exact same
# bare names, with zero changes to their own cross-imports. Qt-free-ness of
# everything imported from there is still enforced per-module by
# test_no_qt_anywhere.py's zero-tolerance rule the moment it lands in
# backend/ imports.
