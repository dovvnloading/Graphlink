"""Integration tests for ToolbarHost - Phase 6 increment 1.

Unlike every other Phase 5/6 host so far (small, corner/anchor-positioned
boxes), this one is full-window-width permanent chrome - see
graphlink_toolbar_web.py's module docstring for why min_height == max_height
gives it the same Expanding-horizontal/Fixed-vertical size policy every
negotiated-height host already gets, without needing any real content-driven
negotiation (a single toolbar row never resizes).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QSizePolicy, QWidget
from PySide6.QtCore import Qt

import graphlink_web_island_host as wih
from graphlink_toolbar_web import TOOLBAR_HEIGHT, ToolbarHost


class _FakeWindow:
    class _PinOverlay:
        def isVisible(self):
            return False

    class _SettingsManager:
        def get_current_mode(self):
            return "Ollama (Local)"

    pin_overlay = _PinOverlay()
    settings_manager = _SettingsManager()


def _clear_registry():
    wih._hosts.clear()


def _make_host(parent=None) -> ToolbarHost:
    return ToolbarHost(_FakeWindow(), parent=parent)


def test_construction_registers_with_the_shared_shutdown_registry():
    _clear_registry()
    host = _make_host()

    assert host in wih._hosts


def test_construction_has_no_window_flag_it_is_an_embedded_child():
    parent = QWidget()
    host = _make_host(parent)

    assert not (host.windowFlags() & Qt.WindowType.Tool)
    assert not (host.windowFlags() & Qt.WindowType.Window)


def test_construction_has_a_fixed_height_and_expands_horizontally():
    host = _make_host()

    assert host.height() == TOOLBAR_HEIGHT
    policy = host.sizePolicy()
    assert policy.horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert policy.verticalPolicy() == QSizePolicy.Policy.Fixed


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


def test_bridges_parent_is_the_host_itself():
    host = _make_host()

    assert host.bridge.parent() is host
