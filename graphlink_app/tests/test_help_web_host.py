"""Integration tests for HelpWebHost - Phase 4 increment 2. Mirrors
test_about_web_host.py's pattern for the shutdown-registry/theme-republish
contract every WebIslandHost subclass shares, plus this host's own
hide-not-teardown closeEvent (applied here from day one, matching About's
and Settings' precedent) and show_for_anchor's screen-clamp positioning
(copied verbatim from the legacy HelpDialog).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QWidget

import graphlink_web_island_host as wih
from graphlink_help_web import HELP_HEIGHT, HELP_WIDTH, HelpWebHost


def _clear_registry():
    wih._hosts.clear()


def _make_host() -> HelpWebHost:
    return HelpWebHost()


def test_construction_registers_with_the_shared_shutdown_registry():
    _clear_registry()
    host = _make_host()

    assert host in wih._hosts


def test_window_flags_are_a_frameless_non_modal_tool_window():
    host = _make_host()

    flags = host.windowFlags()

    assert flags & Qt.WindowType.Tool
    assert flags & Qt.WindowType.FramelessWindowHint


def test_construction_sizes_to_900x620():
    host = _make_host()

    assert host.width() == HELP_WIDTH
    assert host.height() == HELP_HEIGHT


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


class TestCloseIsHideNotTeardown:
    def test_close_does_not_dispose_the_bridge_or_unregister_the_host(self):
        _clear_registry()
        host = _make_host()
        event = QCloseEvent()

        host.closeEvent(event)

        assert event.isAccepted() is True
        assert host.bridge.disposed is False
        assert host in wih._hosts

    def test_the_bridge_still_publishes_after_a_close_reopen_cycle(self):
        host = _make_host()
        host.closeEvent(QCloseEvent())

        states = []
        host.bridge.stateChanged.connect(states.append)
        host.bridge.ready()

        assert len(states) == 1

    def test_prepare_for_shutdown_still_tears_down_after_a_close(self):
        _clear_registry()
        host = _make_host()
        host.closeEvent(QCloseEvent())

        host.prepare_for_shutdown()

        assert host.bridge.disposed is True
        assert host not in wih._hosts


class TestShowForAnchor:
    def test_shows_the_panel_and_resizes_to_900x620(self):
        host = _make_host()
        anchor = QWidget()
        anchor.resize(80, 24)

        host.show_for_anchor(anchor)

        assert host.isVisible() is True
        assert host.width() == HELP_WIDTH
        assert host.height() == HELP_HEIGHT
