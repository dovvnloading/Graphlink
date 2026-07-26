"""Shared pytest setup for graphlink_app's test suite.

Qt allows exactly one application instance per process. Several test modules need to
construct real QGraphicsObject/QWidget-based plugin nodes, which requires a full
QApplication (not just a QCoreApplication) to already exist - if a QCoreApplication is
created first, later widget construction can hang instead of raising a clear error.
Creating the one shared QApplication here, before any test module is imported,
guarantees every test module sees the same, correctly-typed singleton.
"""

import sys
from pathlib import Path

# R7.2: the flat modules under graphlink_app/ (still ~89 of them, plus every
# remaining Qt widget/bridge file) need graphlink_app/ itself on sys.path -
# unchanged from before. The 33 modules R7.2 relocated (api_provider,
# graphlink_task_config, graphlink_plugins/, ...) now sit at the repo root
# instead, a sibling of graphlink_app/, so this suite additionally needs the
# repo root on sys.path or every bare `import api_provider` etc. across this
# test package fails collection.
_GRAPHLINK_APP_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _GRAPHLINK_APP_DIR.parent
sys.path.insert(0, str(_GRAPHLINK_APP_DIR))
sys.path.insert(0, str(_REPO_ROOT))

from PySide6.QtWidgets import QApplication

QApplication.instance() or QApplication([])
