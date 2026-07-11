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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

QApplication.instance() or QApplication([])
