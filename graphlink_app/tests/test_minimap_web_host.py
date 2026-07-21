"""Integration tests for MinimapHost - Phase 6 increment 5.

Unlike every other host built this migration, height is NOT negotiated
here - it stays whatever ChatView._update_overlay_positions() externally
imposes, matching MinimapWidget's own identical externally-imposed-height
behavior. This host also starts VISIBLE by default (not hidden like every
picker/panel host) - the minimap is the app's normal default state; only
the grid/font/drag-speed panels start hidden.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt

import graphlink_web_island_host as wih
from graphlink_minimap_web import MINIMAP_WIDTH, MinimapHost


class _FakeScene(QObject):
    scene_changed = Signal()

    def __init__(self):
        super().__init__()
        self.nodes = []


class _FakeChatView:
    def __init__(self):
        self._scene = _FakeScene()

    def scene(self):
        return self._scene

    def _on_minimap_node_selected(self, node):
        pass


def _clear_registry():
    wih._hosts.clear()


def _make_host(parent=None) -> MinimapHost:
    return MinimapHost(_FakeChatView(), parent=parent)


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

    assert host.width() == MINIMAP_WIDTH == 40


def test_height_is_not_negotiated_apply_requested_height_is_unavailable():
    host = _make_host()

    try:
        host.apply_requested_height(500)
        raised = False
    except NotImplementedError:
        raised = True

    assert raised, "MinimapHost passes no min/max height - apply_requested_height must not be usable"


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
