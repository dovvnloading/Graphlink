"""Integration tests for SettingsWebHost - Phase 3 increment 8's real-shell
wiring. Nothing previously constructed a real SettingsWebHost end-to-end;
test_settings_bridge*.py only exercise the bridge in isolation. These tests
close that gap, mirroring test_composer_web_host.py's pattern for the
shutdown-registry/theme-republish contract every WebIslandHost subclass
shares, plus this host's own close-guard and positioning methods.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox, QWidget

import graphlink_config as config
import graphlink_web_island_host as wih
from graphlink_licensing import SettingsManager
from graphlink_settings_web import SettingsWebHost


def _clear_registry():
    wih._hosts.clear()


def _make_host(tmp_path, main_window=None) -> SettingsWebHost:
    return SettingsWebHost(SettingsManager(tmp_path / "session.dat"), main_window=main_window)


class _FakeWorker:
    """Duck-typed stand-in for a QThread worker - only isRunning()/wait()/
    cancel() are ever called on a running worker by SettingsWebHost's close
    guard, so a real QThread isn't needed to exercise that logic."""

    def __init__(self, finishes_within_wait=False):
        self._running = True
        self._finishes_within_wait = finishes_within_wait
        self.cancel_called = False

    def isRunning(self):
        return self._running

    def cancel(self):
        self.cancel_called = True

    def wait(self, timeout_ms):
        if self._finishes_within_wait:
            self._running = False
            return True
        return False


def test_construction_registers_with_the_shared_shutdown_registry(tmp_path):
    _clear_registry()
    host = _make_host(tmp_path)

    assert host in wih._hosts


def test_window_flags_are_a_persistent_frameless_tool_window(tmp_path):
    host = _make_host(tmp_path)

    flags = host.windowFlags()

    assert flags & Qt.WindowType.Tool
    assert flags & Qt.WindowType.FramelessWindowHint


def test_construction_sizes_to_820x560(tmp_path):
    host = _make_host(tmp_path)

    assert host.width() == 820
    assert host.height() == 560


def test_on_theme_changed_is_inherited_and_republishes(tmp_path):
    host = _make_host(tmp_path)
    states = []
    host.bridge.stateChanged.connect(states.append)
    count_before = len(states)

    host.on_theme_changed()

    assert len(states) == count_before + 1


def test_prepare_for_shutdown_disposes_bridge_and_unregisters(tmp_path):
    _clear_registry()
    host = _make_host(tmp_path)

    host.prepare_for_shutdown()

    assert host not in wih._hosts
    assert host.bridge.disposed is True


class TestSetCurrentSectionByMode:
    def test_maps_a_known_mode_to_its_own_section(self, tmp_path):
        host = _make_host(tmp_path)
        states = []
        host.bridge.stateChanged.connect(states.append)

        host.set_current_section_by_mode(config.MODE_OLLAMA_LOCAL)

        assert host.bridge._active_section == config.MODE_OLLAMA_LOCAL

    def test_falls_back_to_general_for_an_unrecognized_mode(self, tmp_path):
        host = _make_host(tmp_path)
        host.set_current_section_by_mode(config.MODE_API_ENDPOINT)

        host.set_current_section_by_mode("Not A Real Mode")

        assert host.bridge._active_section == "General"


class TestShowForAnchor:
    def test_shows_the_panel_and_resizes_to_820x560(self, tmp_path):
        host = _make_host(tmp_path)
        anchor = QWidget()
        anchor.resize(80, 24)

        host.show_for_anchor(anchor)

        assert host.isVisible() is True
        assert host.width() == 820
        assert host.height() == 560


class TestCloseGuard:
    def test_close_succeeds_when_no_workers_are_running(self, tmp_path):
        host = _make_host(tmp_path)
        event = QCloseEvent()

        host.closeEvent(event)

        assert event.isAccepted() is True

    def test_close_is_blocked_while_a_worker_is_still_running_after_the_wait(self, tmp_path, monkeypatch):
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
        host = _make_host(tmp_path)
        worker = _FakeWorker(finishes_within_wait=False)
        host.bridge._ollama_scan_worker = worker
        event = QCloseEvent()

        host.closeEvent(event)

        assert event.isAccepted() is False
        assert worker.cancel_called is True

    def test_close_succeeds_once_a_running_worker_finishes_within_the_wait(self, tmp_path):
        host = _make_host(tmp_path)
        worker = _FakeWorker(finishes_within_wait=True)
        host.bridge._api_worker = worker
        event = QCloseEvent()

        host.closeEvent(event)

        assert event.isAccepted() is True

    def test_the_close_guard_checks_all_four_bridge_workers_not_just_three(self, tmp_path, monkeypatch):
        """Ports a real fix, not just a feature: the legacy SettingsDialog's
        own _iter_running_workers() omitted ApiModelLoadWorker from its
        3-of-4 tracking list (recorded in the Phase 3 scope note). This
        proves the new host's close guard checks the 4th worker too."""
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
        host = _make_host(tmp_path)
        worker = _FakeWorker(finishes_within_wait=False)
        host.bridge._api_worker = worker
        event = QCloseEvent()

        host.closeEvent(event)

        assert event.isAccepted() is False


class TestCloseIsHideNotTeardown:
    """Regression guard for the real bug increment 10's drive found: routing
    close through WebIslandHost.closeEvent ran prepare_for_shutdown(),
    permanently disposing the bridge and killing the page - so the Settings
    button's toggle-close left every REOPEN showing a dead panel (reachable
    as the default path since the increment-9 flip). Close must hide only;
    true teardown belongs to the shutdown registry at app exit."""

    def test_close_does_not_dispose_the_bridge_or_unregister_the_host(self, tmp_path):
        _clear_registry()
        host = _make_host(tmp_path)
        event = QCloseEvent()

        host.closeEvent(event)

        assert event.isAccepted() is True
        assert host.bridge.disposed is False
        assert host in wih._hosts

    def test_the_bridge_still_publishes_after_a_close_reopen_cycle(self, tmp_path):
        host = _make_host(tmp_path)
        host.closeEvent(QCloseEvent())

        states = []
        host.bridge.stateChanged.connect(states.append)
        host.bridge.ready()

        assert len(states) == 1

    def test_prepare_for_shutdown_still_tears_down_after_a_close(self, tmp_path):
        # The app-exit path must remain intact: a hide-style close first,
        # then the registry-driven teardown still disposes for real.
        _clear_registry()
        host = _make_host(tmp_path)
        host.closeEvent(QCloseEvent())

        host.prepare_for_shutdown()

        assert host.bridge.disposed is True
        assert host not in wih._hosts
