"""Integration tests for PinOverlayHost - Phase 5 increment 1.

Like DocumentViewerWebHost, this is an EMBEDDED host (no Window flag) - see
graphlink_pin_overlay_web.py's module docstring for why toggle_pin_overlay
uses setVisible(False) rather than .close(), sidestepping the closeEvent-
teardown risk class entirely rather than adding another override.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtWidgets import QWidget

import graphlink_web_island_host as wih
from graphlink_navigation_pins import NavigationPinStore
from graphlink_pin_overlay_web import (
    PIN_OVERLAY_WIDTH,
    PinOverlayHost,
)
from graphlink_pin_overlay_bridge import PIN_OVERLAY_MAX_HEIGHT, PIN_OVERLAY_MIN_HEIGHT


class _FakeScene(QObject):
    selectionChanged = Signal()

    def __init__(self):
        super().__init__()
        self.pin_store = NavigationPinStore()

    def selectedItems(self):
        return []

    def _navigation_pin_item(self, pin_id):
        return None


class _FakeChatView:
    def __init__(self):
        self._scene = _FakeScene()

    def scene(self):
        return self._scene


class _FakeController:
    draft = None


def _clear_registry():
    wih._hosts.clear()


def _make_host() -> PinOverlayHost:
    return PinOverlayHost(_FakeChatView(), _FakeController())


def test_construction_registers_with_the_shared_shutdown_registry():
    _clear_registry()
    host = _make_host()

    assert host in wih._hosts


def test_construction_has_no_window_flag_it_is_an_embedded_child():
    parent = QWidget()
    host = PinOverlayHost(_FakeChatView(), _FakeController(), parent=parent)

    assert not (host.windowFlags() & Qt.WindowType.Tool)
    assert not (host.windowFlags() & Qt.WindowType.Window)


def test_construction_sizes_to_fixed_width_400():
    host = _make_host()

    assert host.width() == PIN_OVERLAY_WIDTH == 400


def test_construction_starts_hidden():
    host = _make_host()

    assert host.isVisible() is False


def test_height_negotiation_bounds_are_wired_to_the_bridge():
    host = _make_host()

    host.bridge.resize(9999)

    assert host.height() == PIN_OVERLAY_MAX_HEIGHT


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


class TestShowForAnchorAndReposition:
    def test_show_for_anchor_shows_the_host_below_the_anchor(self):
        parent = QWidget()
        parent.resize(800, 600)
        # An embedded child's isVisible() depends on its whole ancestor chain
        # being shown - see DocumentViewerWebHost's drive script for the same
        # Qt behavior encountered against the real app.
        parent.show()
        host = PinOverlayHost(_FakeChatView(), _FakeController(), parent=parent)
        anchor = QWidget(parent)
        anchor.move(50, 20)
        anchor.resize(80, 24)

        host.show_for_anchor(anchor)

        assert host.isVisible() is True
        assert host.pos().y() == 20 + 24 + 6

    def test_reposition_without_an_anchor_does_nothing(self):
        host = _make_host()

        host.reposition()  # must not raise


class TestClosedSignal:
    def test_hiding_emits_closed(self):
        host = _make_host()
        host.setVisible(True)
        seen = []
        host.closed.connect(lambda: seen.append(True))

        host.setVisible(False)

        assert seen == [True]


def test_edit_pin_and_show_pin_context_menu_facades_exist_for_canvas_call_sites():
    """ChatWindow.edit_navigation_pin()/show_navigation_pin_context_menu()
    call these exact method names on whatever self.pin_overlay currently is -
    a real regression here would silently break canvas-triggered pin editing
    even though nothing about the list panel itself would look wrong."""
    host = _make_host()

    assert callable(host.edit_pin)
    assert callable(host.show_pin_context_menu)
    # None-pin guard, matching the legacy PinOverlay.show_pin_context_menu:
    host.show_pin_context_menu(None)  # must not raise
