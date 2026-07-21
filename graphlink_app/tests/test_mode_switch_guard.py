"""Tests for the mode-switch guard during an active main chat request.

Partial coverage for the unsynchronized provider globals: the provider runtime is
module-level mutable global state in api_provider, swapped by initialize_* while worker
threads read it. The most user-reachable race was simply changing the mode combo while
a response was streaming in - nothing prevented it, so the in-flight request could
execute against a half-swapped provider. on_mode_changed() now refuses the switch while
_main_request_active, reverts the toolbar's displayed mode, and tells the user why. (Full
encapsulation of the provider globals remains open - this closes the one race a user can
trigger from the toolbar with no special timing.)

Phase 6 increment 2: the busy/no-op predicate on_mode_changed() used to check inline is
now graphlink_window.mode_switch_rejection_reason() - a pure function taking primitives
and returning a rejection reason or None, directly unit-tested below with zero Qt/mock
machinery. The 2 remaining QMessageBox.warning calls in mode-switching (unknown mode,
init failure) are gone too - both now route through notification_banner.show_message(),
matching the exact non-modal pattern the busy-guard case already used, so the ENTIRE
mode-switch flow is now free of native modal popups. The previously-uncovered rollback
branch (a real gap the module docstring's own "rollback" framing didn't actually test)
gets real coverage here for the first time.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, call

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import graphlink_window
from graphlink_window import mode_switch_rejection_reason


class TestModeSwitchRejectionReasonIsAPureFunction:
    """The extracted guard predicate itself - no window, no mocks, just
    primitives in and a reason (or None) out."""

    def test_rejects_a_real_switch_while_a_request_is_active(self):
        reason = mode_switch_rejection_reason(
            request_active=True, requested_mode="API Endpoint", current_mode="Ollama (Local)"
        )

        assert reason == "busy"

    def test_allows_reselecting_the_current_mode_even_while_active(self):
        reason = mode_switch_rejection_reason(
            request_active=True, requested_mode="Ollama (Local)", current_mode="Ollama (Local)"
        )

        assert reason is None

    def test_allows_any_switch_while_idle(self):
        reason = mode_switch_rejection_reason(
            request_active=False, requested_mode="API Endpoint", current_mode="Ollama (Local)"
        )

        assert reason is None


def _make_window(active, current_mode="Ollama (Local)"):
    window = MagicMock()
    window._main_request_active = active
    window.settings_manager.get_current_mode.return_value = current_mode
    return window


class TestSwitchBlockedWhileRequestActive:
    def test_switch_is_reverted_and_never_reinitializes_the_provider(self):
        window = _make_window(active=True)

        graphlink_window.ChatWindow.on_mode_changed(window, "API Endpoint")

        window._initialize_mode.assert_not_called()
        window.settings_manager.set_current_mode.assert_not_called()
        window.notification_banner.show_message.assert_called_once()
        message, _duration, level = window.notification_banner.show_message.call_args[0]
        assert "switch" in message.lower()
        assert level == "warning"
        # No native combo to revert anymore - republishing the toolbar's
        # unchanged currentMode is what "reverts" the displayed selection.
        window.toolbar_host.bridge.publish.assert_called_once()

    def test_reselecting_the_current_mode_while_active_is_a_no_op_not_a_warning(self):
        # A re-selection of the same mode is not a switch and must not nag the user.
        window = _make_window(active=True, current_mode="Ollama (Local)")

        graphlink_window.ChatWindow.on_mode_changed(window, "Ollama (Local)")

        window.notification_banner.show_message.assert_not_called()
        # Falls through to the normal path (same mode re-initialization is the
        # pre-existing behavior and stays unchanged).
        window._initialize_mode.assert_called_once()


class TestSwitchAllowedWhenIdle:
    def test_switch_proceeds_normally_when_no_request_is_active(self):
        window = _make_window(active=False)

        graphlink_window.ChatWindow.on_mode_changed(window, "API Endpoint")

        window._initialize_mode.assert_called_once_with("API Endpoint", show_dialogs=True)
        window.settings_manager.set_current_mode.assert_called_once_with("API Endpoint")
        window.reinitialize_agent.assert_called_once()
        window.toolbar_host.bridge.publish.assert_called_once()


class TestInitFailureRollsBackToTheSnapshot:
    """Previously uncovered entirely: the except-block rollback path. No
    QMessageBox is popped anymore - init failures surface through the same
    notification_banner the busy-guard case already uses, so the toolbar's
    own currentMode is the only thing that ever needs reverting."""

    def test_a_failed_initialize_reverts_settings_to_the_previous_mode(self):
        window = _make_window(active=False, current_mode="Ollama (Local)")
        window._initialize_mode.side_effect = [RuntimeError("boom"), True]

        graphlink_window.ChatWindow.on_mode_changed(window, "API Endpoint")

        # First call attempts the requested mode; second call is the rollback
        # re-initializing the previous (snapshotted) mode.
        assert window._initialize_mode.call_args_list == [
            call("API Endpoint", show_dialogs=True),
            call("Ollama (Local)", show_dialogs=False),
        ]
        window.settings_manager.set_current_mode.assert_called_once_with("Ollama (Local)")
        window.notification_banner.show_message.assert_called_once()
        message, _duration, level = window.notification_banner.show_message.call_args[0]
        assert "API Endpoint" in message
        assert level == "error"
        window.toolbar_host.bridge.publish.assert_called_once()

    def test_a_failed_rollback_is_swallowed_and_still_notifies_once(self):
        window = _make_window(active=False, current_mode="Ollama (Local)")
        window._initialize_mode.side_effect = RuntimeError("boom")

        graphlink_window.ChatWindow.on_mode_changed(window, "API Endpoint")  # must not raise

        window.notification_banner.show_message.assert_called_once()
        window.toolbar_host.bridge.publish.assert_called_once()

    def test_no_rollback_is_attempted_when_the_requested_mode_equals_the_current_one(self):
        window = _make_window(active=False, current_mode="Ollama (Local)")
        window._initialize_mode.side_effect = RuntimeError("boom")

        graphlink_window.ChatWindow.on_mode_changed(window, "Ollama (Local)")

        window._initialize_mode.assert_called_once_with("Ollama (Local)", show_dialogs=True)
        window.settings_manager.set_current_mode.assert_not_called()
