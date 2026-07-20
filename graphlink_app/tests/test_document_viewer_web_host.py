"""Integration tests for DocumentViewerWebHost - Phase 4 increment 3.

Unlike test_about_web_host.py/test_help_web_host.py, there is deliberately NO
"hide-not-teardown closeEvent override" test class here: this host is a
plain embedded child QFrame (added directly to content_layout, no Window
flag), so Qt never actually delivers it a native closeEvent - confirmed by
direct code recon, not assumed. The test below instead documents that
WebIslandHost's base closeEvent (real teardown) is left untouched on
purpose, since nothing ever calls it in practice.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QWidget

import graphlink_web_island_host as wih
from graphlink_document_viewer_web import DOCUMENT_VIEWER_WIDTH, DocumentViewerWebHost


def _clear_registry():
    wih._hosts.clear()


def _make_host() -> DocumentViewerWebHost:
    return DocumentViewerWebHost()


def _make_embedded_host() -> tuple[DocumentViewerWebHost, QWidget]:
    """A parentless QWidget is implicitly top-level in Qt (it gets Window
    flags automatically, regardless of what any subclass sets) - real usage
    always constructs this host with a parent (content_layout.addWidget()),
    so the "no Window flag" assertion needs a real parent to mean anything.
    Returns (host, parent) - the parent must stay referenced by the caller,
    or PySide6 garbage-collects the C++ side and takes the child with it."""
    parent = QWidget()
    host = DocumentViewerWebHost(parent=parent)
    return host, parent


def test_construction_registers_with_the_shared_shutdown_registry():
    _clear_registry()
    host = _make_host()

    assert host in wih._hosts


def test_construction_has_no_window_flag_unlike_the_floating_islands():
    host, _parent = _make_embedded_host()

    assert not (host.windowFlags() & Qt.WindowType.Tool)
    assert not (host.windowFlags() & Qt.WindowType.Window)


def test_construction_sizes_to_fixed_width_500_with_no_forced_height():
    host = _make_host()

    assert host.width() == DOCUMENT_VIEWER_WIDTH == 500
    assert host.minimumHeight() == 0
    assert host.maximumHeight() >= 16777215 - 1  # Qt's QWIDGETSIZE_MAX, i.e. "unbounded"


def test_construction_starts_hidden():
    host = _make_host()

    assert host.isVisible() is False


def test_on_theme_changed_is_inherited_and_republishes():
    host = _make_host()
    states = []
    host.bridge.stateChanged.connect(states.append)
    count_before = len(states)

    host.on_theme_changed()

    assert len(states) == count_before + 1


def test_prepare_for_shutdown_disposes_bridge_and_unregisters():
    _clear_registry()
    host = _make_host()

    host.prepare_for_shutdown()

    assert host not in wih._hosts
    assert host.bridge.disposed is True


class TestSetDocumentContentFacade:
    def test_forwards_to_the_bridge_and_publishes(self):
        host = _make_host()
        states = []
        host.bridge.stateChanged.connect(states.append)

        host.set_document_content("## Reasoning\n\nsome thinking text")

        import json

        assert json.loads(states[-1])["content"] == "## Reasoning\n\nsome thinking text"


def test_close_event_is_the_inherited_base_default_never_invoked_by_qt_in_practice():
    """Documents the "no override needed" decision rather than assuming it:
    calling closeEvent() directly still tears down (WebIslandHost's base
    behavior, unmodified), but real Qt only ever delivers closeEvent to a
    top-level window - this host has no Window flag, so that path is
    unreachable through normal use. show/hide always goes through
    setVisible() alone (see graphlink_window.py's show_document_view/
    hide_document_view)."""
    _clear_registry()
    host = _make_host()

    host.closeEvent(QCloseEvent())

    assert host.bridge.disposed is True
    assert host not in wih._hosts
