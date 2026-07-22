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

import sys
from pathlib import Path

BACKEND_VERSION = "0.1.0"

# The surviving Qt-free domain modules (GridViewSettings, NavigationPinStore,
# payload dataclasses, session layer, ...) live flat inside graphlink_app/
# and import each other by bare name - the same path convention the Qt entry
# point used. The backend consumes them under that convention until the R7
# cutover restructures the tree. Qt-free-ness of everything imported from
# there is enforced per-module by test_no_qt_anywhere.py's zero-tolerance
# rule the moment it lands in backend/ imports.
_GRAPHLINK_APP_DIR = str(Path(__file__).resolve().parents[1] / "graphlink_app")
if _GRAPHLINK_APP_DIR not in sys.path:
    sys.path.insert(0, _GRAPHLINK_APP_DIR)
