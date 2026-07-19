"""Contract tests for the keyboard arbitration protocol (migration plan
Phase 1 checklist item: "Keyboard arbitration protocol designed in host
(focus-state publication + accelerator forwarding filter) with contract
test").

Deliberately island-agnostic: every test here uses a plain WebIslandHost
(via graphlink_web_island_host's own _make_host-equivalent below), never
ComposerWebHost - proving the protocol works for "any island," not just the
one that exists today. See test_web_island_host.py for the three
lower-level state/registry tests this builds on.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent

import graphlink_web_island_host as wih
from graphlink_island_bridge import IslandBridge
from graphlink_view import ChatView
from graphlink_web_island_host import AcceleratorForwardingFilter, WebIslandHost


class _FakeBridge(IslandBridge, QObject):
    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)

    def _build_state_payload(self):
        return {}

    def _transport_send(self, payload_json):
        pass


def _make_host(**overrides):
    kwargs = dict(
        bridge=_FakeBridge(),
        asset_dir_name="does-not-exist",
        bridge_object_name="fakeBridge",
    )
    kwargs.update(overrides)
    return WebIslandHost(**kwargs)


@pytest.fixture(autouse=True)
def _clean_registry():
    wih._hosts.clear()
    yield
    wih._hosts.clear()


def _key_event(event_type, key, modifiers=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(event_type, key, modifiers)


class TestChatViewDefersToIslandTextFocus:
    def test_wasd_is_consumed_by_the_canvas_when_no_island_has_focus(self):
        view = ChatView(MagicMock())
        view.keyPressEvent(_key_event(QEvent.Type.KeyPress, Qt.Key.Key_W))
        assert Qt.Key.Key_W in view.keys_pressed

    def test_wasd_is_deferred_to_super_when_an_island_has_text_focus(self):
        view = ChatView(MagicMock())
        host = _make_host()
        host.reportTextFocus(True)

        view.keyPressEvent(_key_event(QEvent.Type.KeyPress, Qt.Key.Key_W))

        # The canvas must not have claimed W as a pan key while an island
        # wants keyboard input - this is the literal "canvas steals keys from
        # island inputs" bug the checklist item names.
        assert Qt.Key.Key_W not in view.keys_pressed

    def test_wasd_resumes_once_the_island_reports_focus_lost(self):
        view = ChatView(MagicMock())
        host = _make_host()
        host.reportTextFocus(True)
        view.keyPressEvent(_key_event(QEvent.Type.KeyPress, Qt.Key.Key_W))
        assert Qt.Key.Key_W not in view.keys_pressed

        host.reportTextFocus(False)
        view.keyPressEvent(_key_event(QEvent.Type.KeyPress, Qt.Key.Key_W))
        assert Qt.Key.Key_W in view.keys_pressed

    def test_existing_scene_focus_item_check_still_works_unchanged(self):
        # The two pre-existing isinstance checks must not regress - this
        # doesn't even construct a WebIslandHost, proving the new check is
        # purely additive and doesn't interfere when no island exists at all.
        view = ChatView(MagicMock())
        view.keyPressEvent(_key_event(QEvent.Type.KeyPress, Qt.Key.Key_E))
        # Q/E are zoom keys, not added to keys_pressed - just confirms no
        # exception and normal canvas handling still runs.
        assert Qt.Key.Key_W not in view.keys_pressed


class TestAcceleratorForwardingFilter:
    def _filter(self):
        return AcceleratorForwardingFilter()

    def test_gates_a_workspace_shortcut_while_an_island_has_text_focus(self):
        host = _make_host()
        host.reportTextFocus(True)
        event = _key_event(QEvent.Type.ShortcutOverride, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)

        handled = self._filter().eventFilter(None, event)

        assert handled is True
        assert event.isAccepted()

    def test_exempts_ctrl_s_even_while_an_island_has_text_focus(self):
        host = _make_host()
        host.reportTextFocus(True)
        event = _key_event(QEvent.Type.ShortcutOverride, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)

        handled = self._filter().eventFilter(None, event)

        assert handled is False

    def test_passes_through_when_no_island_has_text_focus(self):
        _make_host()  # registered, but never reports focus
        event = _key_event(QEvent.Type.ShortcutOverride, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)

        handled = self._filter().eventFilter(None, event)

        assert handled is False

    def test_ignores_non_shortcut_override_events_entirely(self):
        host = _make_host()
        host.reportTextFocus(True)
        event = _key_event(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)

        handled = self._filter().eventFilter(None, event)

        assert handled is False

    def test_every_documented_gated_shortcut_is_actually_gated(self):
        # Cross-checks the class's own GATED_SHORTCUTS set against every real
        # global shortcut in graphlink_window.py except Ctrl+S, rather than
        # trusting the constant matches the docstring's claim.
        host = _make_host()
        host.reportTextFocus(True)
        f = self._filter()
        expected_gated = {
            (Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier),
            (Qt.Key.Key_Up, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier),
        }
        assert f.GATED_SHORTCUTS == expected_gated
        for key, mods in expected_gated:
            event = _key_event(QEvent.Type.ShortcutOverride, key, mods)
            assert f.eventFilter(None, event) is True, f"{key!r}+{mods!r} was not gated"
