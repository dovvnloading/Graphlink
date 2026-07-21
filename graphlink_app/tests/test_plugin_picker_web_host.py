"""Integration tests for PluginPickerHost - Phase 6 increment 3.

Like ComposerPickerHost/ComposerContextHost, this is an EMBEDDED host (no
Window flag) - see graphlink_plugin_picker_web.py's module docstring for why
outside-click-close is reimplemented via WebIslandHost's
dismiss_on_outside_focus rather than the legacy popup's own
Qt.WindowType.Popup dismiss behavior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QWidget

import graphlink_web_island_host as wih
from graphlink_plugin_picker_bridge import PLUGIN_PICKER_MAX_HEIGHT
from graphlink_plugin_picker_web import PLUGIN_PICKER_WIDTH, PluginPickerHost


class _FakePluginPortal:
    def get_plugin_categories(self):
        return []

    def execute_plugin(self, plugin_name):
        pass


def _clear_registry():
    wih._hosts.clear()


def _make_host(parent=None) -> PluginPickerHost:
    return PluginPickerHost(_FakePluginPortal(), parent=parent)


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

    assert host.width() == PLUGIN_PICKER_WIDTH == 520


def test_construction_starts_hidden():
    host = _make_host()

    assert host.isVisible() is False


def test_height_negotiation_bounds_are_wired_to_the_bridge():
    host = _make_host()

    host.bridge.resize(9999)

    assert host.height() == PLUGIN_PICKER_MAX_HEIGHT


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


def test_reposition_with_no_anchor_does_nothing():
    host = _make_host()

    host.reposition(None)  # must not raise


def test_reposition_with_no_parent_does_nothing():
    host = PluginPickerHost(_FakePluginPortal())

    class _FakeAnchor:
        def mapToGlobal(self, point):
            return point

        def height(self):
            return 30

    host.reposition(_FakeAnchor())  # must not raise, no parentWidget()


class TestDismissOnOutsideFocus:
    def test_visible_and_focus_moves_elsewhere_hides_the_host(self):
        parent = QWidget()
        parent.resize(800, 600)
        parent.show()
        host = _make_host(parent)
        other = QWidget(parent)
        other.show()

        host.setVisible(True)
        host._on_outside_focus_changed(None, other)

        assert host.isVisible() is False

    def test_focus_moving_to_the_hosts_own_web_view_does_not_hide_it(self):
        host = _make_host()
        host.setVisible(True)

        host._on_outside_focus_changed(None, host.web_view)

        assert host.isVisible() is True


class TestReposition:
    def test_positions_below_the_anchor_within_the_viewport(self):
        parent = QWidget()
        parent.resize(1000, 800)
        parent.show()
        host = _make_host(parent)
        host.resize(PLUGIN_PICKER_WIDTH, 200)

        class _FakeAnchor:
            def __init__(self, rect):
                self._rect = rect

            def mapToGlobal(self, point):
                return QPoint(self._rect.x() + point.x(), self._rect.y() + point.y())

            def height(self):
                return self._rect.height()

            def size(self):
                return QSize(self._rect.width(), self._rect.height())

        anchor = _FakeAnchor(QRect(100, 200, 60, 30))

        host.reposition(anchor)

        assert host.pos().y() > 200

    def test_clamps_into_the_available_screen_geometry(self):
        parent = QWidget()
        parent.resize(1000, 800)
        parent.show()
        host = _make_host(parent)
        host.resize(PLUGIN_PICKER_WIDTH, 200)

        class _FakeAnchor:
            def mapToGlobal(self, point):
                return QPoint(5000, 5000)

            def height(self):
                return 30

            def size(self):
                return QSize(60, 30)

        host.reposition(_FakeAnchor())  # must not raise, and must not place off-screen

        global_pos = host.mapToGlobal(QPoint(0, 0))
        screen = host.screen()
        if screen is not None:
            available = screen.availableGeometry()
            assert global_pos.x() <= available.right()
            assert global_pos.y() <= available.bottom()
