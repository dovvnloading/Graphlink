"""Integration tests for DragSpeedHost - Phase 6 increment 5.

Like GridControlHost/FontControlHost, this is an EMBEDDED host (no Window
flag) - matching the legacy control_widget's own plain,
no-outside-click-dismiss behavior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

import graphlink_web_island_host as wih
from graphlink_drag_speed_bridge import DRAG_SPEED_MAX_HEIGHT
from graphlink_drag_speed_web import DRAG_SPEED_WIDTH, DragSpeedHost


class _FakeChatView:
    def __init__(self):
        self._drag_factor = 1.0


def _clear_registry():
    wih._hosts.clear()


def _make_host(parent=None) -> DragSpeedHost:
    return DragSpeedHost(_FakeChatView(), parent=parent)


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

    assert host.width() == DRAG_SPEED_WIDTH == 220


def test_construction_starts_hidden():
    host = _make_host()

    assert host.isVisible() is False


def test_height_negotiation_bounds_are_wired_to_the_bridge():
    host = _make_host()

    host.bridge.resize(9999)

    assert host.height() == DRAG_SPEED_MAX_HEIGHT


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
