"""Integration tests for ComposerPickerHost - Phase 5 increment 3.

Like PinOverlayHost/DocumentViewerWebHost, this is an EMBEDDED host (no
Window flag) - see graphlink_composer_picker_web.py's module docstring for
why outside-click-close is reimplemented via WebIslandHost's
dismiss_on_outside_focus rather than the legacy popup's own app-wide
MouseButtonPress eventFilter.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QWidget

import graphlink_web_island_host as wih
from graphlink_composer_picker_bridge import COMPOSER_PICKER_MAX_HEIGHT
from graphlink_composer_picker_web import COMPOSER_PICKER_WIDTH, ComposerPickerHost


class _FakeComposerBridge:
    def route_snapshot(self):
        return {"provider": "Ollama", "modelId": "", "modelOptions": [], "reasoning": {"level": "Thinking", "options": []}}

    def selectModel(self, model_id):
        pass

    def setReasoningLevel(self, level):
        pass


def _clear_registry():
    wih._hosts.clear()


def _make_host(parent=None) -> ComposerPickerHost:
    return ComposerPickerHost(_FakeComposerBridge(), parent=parent)


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

    assert host.width() == COMPOSER_PICKER_WIDTH == 400


def test_construction_starts_hidden():
    host = _make_host()

    assert host.isVisible() is False


def test_height_negotiation_bounds_are_wired_to_the_bridge():
    host = _make_host()

    host.bridge.resize(9999)

    assert host.height() == COMPOSER_PICKER_MAX_HEIGHT


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


def test_reposition_with_no_composer_or_viewport_does_nothing():
    host = _make_host()

    host.reposition(None, None)  # must not raise


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

    def test_focus_becoming_none_does_not_hide_it(self):
        host = _make_host()
        host.setVisible(True)

        host._on_outside_focus_changed(host.web_view, None)

        assert host.isVisible() is True

    def test_hiding_tears_down_the_focus_connection_without_error(self):
        host = _make_host()
        host.setVisible(True)

        host.setVisible(False)  # must not raise disconnecting focusChanged
        host.setVisible(True)  # must not double-connect either


class TestReposition:
    def test_positions_above_the_composer_within_the_viewport(self):
        parent = QWidget()
        parent.resize(1000, 800)
        parent.show()
        host = _make_host(parent)
        host.resize(360, 200)

        class _FakeWidget:
            def __init__(self, rect):
                self._rect = rect

            def mapToGlobal(self, point):
                return QPoint(self._rect.x() + point.x(), self._rect.y() + point.y())

            def size(self):
                return QSize(self._rect.width(), self._rect.height())

        composer = _FakeWidget(QRect(100, 700, 800, 72))
        viewport = _FakeWidget(QRect(0, 0, 1000, 800))

        host.reposition(composer, viewport)

        assert host.pos().y() < 700
