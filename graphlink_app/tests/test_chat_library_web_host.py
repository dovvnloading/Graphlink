"""Integration tests for ChatLibraryWebHost - Phase 4 increment 4.

Like DocumentViewerWebHost, this is an EMBEDDED child host (no Window flag),
so there is no hide-not-teardown closeEvent test here. Its distinguishing
trait is construct-per-open: the native ChatLibraryDialog's closeEvent calls
prepare_for_shutdown() every cycle, so the shutdown-registry unregister path
is what matters and is covered below.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

import graphlink_web_island_host as wih
from graphlink_chat_library_web import ChatLibraryWebHost


class _FakeDatabase:
    def get_all_chats(self):
        return [(1, "A chat", "2026-07-01 09:30:00", "2026-07-05 14:00:00")]


class _FakeSessionManager:
    def __init__(self):
        self.db = _FakeDatabase()
        self.window = None


def _clear_registry():
    wih._hosts.clear()


def _make_host():
    return ChatLibraryWebHost(_FakeSessionManager(), library_dialog=None)


def _make_embedded_host():
    """A parentless QWidget is implicitly top-level in Qt - real usage always
    constructs this host inside the native dialog's layout, so the "no Window
    flag" assertion needs a real parent to mean anything. Returns (host,
    parent); the parent must stay referenced by the caller."""
    parent = QWidget()
    host = ChatLibraryWebHost(_FakeSessionManager(), library_dialog=None, parent=parent)
    return host, parent


def test_construction_registers_with_the_shared_shutdown_registry():
    _clear_registry()
    host = _make_host()

    assert host in wih._hosts


def test_construction_has_no_window_flag_it_is_an_embedded_child():
    host, _parent = _make_embedded_host()

    assert not (host.windowFlags() & Qt.WindowType.Tool)
    assert not (host.windowFlags() & Qt.WindowType.Window)


def test_on_theme_changed_is_inherited_and_republishes_the_rows():
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


def test_prepare_for_shutdown_is_idempotent_matching_construct_per_open_cycles():
    _clear_registry()
    host = _make_host()

    host.prepare_for_shutdown()
    host.prepare_for_shutdown()  # a second close cycle must not raise

    assert host not in wih._hosts
