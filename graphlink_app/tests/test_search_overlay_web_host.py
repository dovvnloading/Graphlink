"""Integration tests for SearchOverlayHost - Phase 5 increment 1.

Like DocumentViewerWebHost, this is an EMBEDDED host (no Window flag) - see
graphlink_search_overlay_web.py's module docstring for why it never goes
through a native closeEvent, confirmed (not assumed) below.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QWidget

import graphlink_web_island_host as wih
from graphlink_search_overlay_web import (
    SEARCH_OVERLAY_HEIGHT,
    SEARCH_OVERLAY_WIDTH,
    SearchOverlayHost,
)


class _FakeScene:
    def find_items(self, text):
        return []

    def update_search_highlight(self, matches):
        pass


class _FakeChatView:
    def scene(self):
        return _FakeScene()


def _clear_registry():
    wih._hosts.clear()


def _make_host() -> SearchOverlayHost:
    return SearchOverlayHost(_FakeChatView())


def _make_embedded_host():
    parent = QWidget()
    host = SearchOverlayHost(_FakeChatView(), parent=parent)
    return host, parent


def test_construction_registers_with_the_shared_shutdown_registry():
    _clear_registry()
    host = _make_host()

    assert host in wih._hosts


def test_construction_has_no_window_flag_it_is_an_embedded_child():
    host, _parent = _make_embedded_host()

    assert not (host.windowFlags() & Qt.WindowType.Tool)
    assert not (host.windowFlags() & Qt.WindowType.Window)


def test_construction_sizes_to_300x44_matching_the_legacy_widget():
    host = _make_host()

    assert host.width() == SEARCH_OVERLAY_WIDTH == 300
    assert host.height() == SEARCH_OVERLAY_HEIGHT == 44


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


class TestReposition:
    def test_moves_to_the_top_right_of_the_given_viewport(self):
        host = _make_host()

        class _Viewport:
            def width(self):
                return 1000

        host.reposition(_Viewport())

        assert host.pos().x() == 1000 - SEARCH_OVERLAY_WIDTH - 10
        assert host.pos().y() == 10


def test_close_event_is_the_inherited_base_default_never_invoked_by_qt_in_practice():
    """Documents the "no override needed" decision rather than assuming it -
    see DocumentViewerWebHost's identical test for the full rationale."""
    _clear_registry()
    host = _make_host()

    host.closeEvent(QCloseEvent())

    assert host.bridge.disposed is True
    assert host not in wih._hosts
