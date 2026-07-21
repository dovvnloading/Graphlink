"""Integration tests for FontControlHost - Phase 6 increment 4.

Like GridControlHost, this is an EMBEDDED host (no Window flag) - matching
the legacy FontControl QWidget's own plain, no-outside-click-dismiss
behavior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

import graphlink_web_island_host as wih
from graphlink_font_control_bridge import FONT_CONTROL_MAX_HEIGHT
from graphlink_font_control_web import FONT_CONTROL_WIDTH, FontControlHost


class _FakeScene:
    def setFontFamily(self, family):
        pass

    def setFontSize(self, size):
        pass

    def setFontColor(self, color):
        pass


class _FakeChatView:
    def scene(self):
        return _FakeScene()


def _clear_registry():
    wih._hosts.clear()


def _make_host(parent=None) -> FontControlHost:
    return FontControlHost(_FakeChatView(), parent=parent)


def test_construction_registers_with_the_shared_shutdown_registry():
    _clear_registry()
    host = _make_host()

    assert host in wih._hosts


def test_construction_has_no_window_flag_it_is_an_embedded_child():
    parent = QWidget()
    host = _make_host(parent)

    assert not (host.windowFlags() & Qt.WindowType.Tool)
    assert not (host.windowFlags() & Qt.WindowType.Window)


def test_construction_sizes_to_fixed_width():
    host = _make_host()

    assert host.width() == FONT_CONTROL_WIDTH == 220


def test_construction_starts_hidden():
    host = _make_host()

    assert host.isVisible() is False


def test_height_negotiation_bounds_are_wired_to_the_bridge():
    host = _make_host()

    host.bridge.resize(9999)

    assert host.height() == FONT_CONTROL_MAX_HEIGHT


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
