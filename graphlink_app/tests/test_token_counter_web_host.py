"""Integration tests for TokenCounterWebHost - Phase 5 increment 4.

Phase-open recon flagged the token counter as the one island with no
dedicated host class at all - a bare, un-subclassed WebIslandHost was
constructed directly in graphlink_window.py, with its position computed
inline rather than via a self-owned reposition() method. This class finishes
applying the pattern every other island already has.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt

import graphlink_web_island_host as wih
from graphlink_token_counter_web import (
    TOKEN_COUNTER_HEIGHT,
    TOKEN_COUNTER_WIDTH,
    TokenCounterWebHost,
)


def _clear_registry():
    wih._hosts.clear()


def _make_host(parent=None) -> TokenCounterWebHost:
    return TokenCounterWebHost(parent=parent)


def test_construction_registers_with_the_shared_shutdown_registry():
    _clear_registry()
    host = _make_host()

    assert host in wih._hosts


def test_construction_has_no_window_flag_it_is_an_embedded_child():
    parent = QWidget()
    host = _make_host(parent)

    assert not (host.windowFlags() & Qt.WindowType.Tool)
    assert not (host.windowFlags() & Qt.WindowType.Window)


def test_construction_sizes_to_the_fixed_150x90():
    host = _make_host()

    assert (host.width(), host.height()) == (TOKEN_COUNTER_WIDTH, TOKEN_COUNTER_HEIGHT) == (150, 90)


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
    def test_positions_bottom_left_with_padding(self):
        host = _make_host()

        class _FakeViewport:
            def height(self):
                return 800

        host.reposition(_FakeViewport())

        assert host.pos().x() == 10
        assert host.pos().y() == 800 - 90 - 10

    def test_clamps_to_the_top_padding_on_a_very_short_viewport(self):
        host = _make_host()

        class _FakeViewport:
            def height(self):
                return 50

        host.reposition(_FakeViewport())

        assert host.pos().y() == 10
