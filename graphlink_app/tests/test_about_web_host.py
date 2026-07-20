"""Integration tests for AboutWebHost - Phase 4 increment 1's first real,
constructed-end-to-end web host. Mirrors test_settings_web_host.py's
pattern for the shutdown-registry/theme-republish contract every
WebIslandHost subclass shares, plus this host's own hide-not-teardown
closeEvent (applied here from day one, not found by a drive afterward the
way SettingsWebHost's identical bug was).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent

import graphlink_web_island_host as wih
from graphlink_about_web import ABOUT_HEIGHT, ABOUT_WIDTH, AboutWebHost


def _clear_registry():
    wih._hosts.clear()


def _make_host() -> AboutWebHost:
    return AboutWebHost()


def test_construction_registers_with_the_shared_shutdown_registry():
    _clear_registry()
    host = _make_host()

    assert host in wih._hosts


def test_window_flags_are_a_frameless_non_modal_tool_window():
    host = _make_host()

    flags = host.windowFlags()

    assert flags & Qt.WindowType.Tool
    assert flags & Qt.WindowType.FramelessWindowHint


def test_construction_sizes_to_420x420():
    host = _make_host()

    assert host.width() == ABOUT_WIDTH
    assert host.height() == ABOUT_HEIGHT


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
    """Regression guard for the exact bug class SettingsWebHost was found
    to have (doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md, Phase 3 increment
    10): WebIslandHost's default closeEvent treats close as app teardown
    (bridge disposed, page stopped) - correct for a permanent child-widget
    island, wrong for a closable, reopenable top-level window like this
    one. Applied here proactively."""

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


class TestShowCenteredOverParent:
    def test_shows_the_panel_and_keeps_its_size(self):
        host = _make_host()

        host.show_centered_over_parent()

        assert host.isVisible() is True
        assert host.width() == ABOUT_WIDTH
        assert host.height() == ABOUT_HEIGHT

    def test_does_not_raise_without_a_parent_or_a_primary_screen(self):
        host = _make_host()
        assert host.parent() is None

        # Must not raise even with no parent widget to center against.
        host.show_centered_over_parent()
