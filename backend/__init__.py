"""Graphlink backend (Qt-removal plan R0, doc/QT_REMOVAL_PLAN.md).

The Python half of the target architecture: FastAPI + a WebSocket event bus
carrying full-state snapshots and intents between the domain services and the
single React SPA. This package is the direct successor of the QWebChannel
IslandBridge layer and deliberately preserves its wire semantics (versioned
full-state envelopes, named intents) so the existing island payload schemas
and generated TS types retarget without redesign.

Zero Qt imports are permitted anywhere under this package - enforced by
graphlink_app/tests/test_no_qt_anywhere.py.
"""

BACKEND_VERSION = "0.1.0"
