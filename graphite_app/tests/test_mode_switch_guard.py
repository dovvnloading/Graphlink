"""Tests for the mode-switch guard during an active main chat request.

Partial coverage for doc/ARCHITECTURE_REVIEW_FINDINGS.md #9: the provider runtime is
module-level mutable global state in api_provider, swapped by initialize_* while worker
threads read it. The most user-reachable race was simply changing the mode combo while
a response was streaming in - nothing prevented it, so the in-flight request could
execute against a half-swapped provider. on_mode_changed() now refuses the switch while
_main_request_active, reverts the combo, and tells the user why. (Full encapsulation of
the provider globals remains open - this closes the one race a user can trigger from
the toolbar with no special timing.)

Uses a MagicMock as `self` for the unbound-method call, with the small set of real
attributes the guard reads set explicitly.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import graphite_window


def _make_window(active, current_mode="Ollama (Local)", selected_mode="API Endpoint"):
    window = MagicMock()
    window._main_request_active = active
    window.settings_manager.get_current_mode.return_value = current_mode
    window.mode_combo.itemText.return_value = selected_mode
    return window


class TestSwitchBlockedWhileRequestActive:
    def test_switch_is_reverted_and_never_reinitializes_the_provider(self):
        window = _make_window(active=True)

        graphite_window.ChatWindow.on_mode_changed(window, 2)

        window._set_mode_combo_silently.assert_called_once_with("Ollama (Local)")
        window._initialize_mode.assert_not_called()
        window.settings_manager.set_current_mode.assert_not_called()
        window.notification_banner.show_message.assert_called_once()
        message = window.notification_banner.show_message.call_args[0][0]
        assert "switch" in message.lower()

    def test_reselecting_the_current_mode_while_active_is_a_no_op_not_a_warning(self):
        # Qt can fire currentIndexChanged for a programmatic re-set of the same mode;
        # that is not a switch and must not nag the user.
        window = _make_window(active=True, selected_mode="Ollama (Local)")

        graphite_window.ChatWindow.on_mode_changed(window, 0)

        window._set_mode_combo_silently.assert_not_called()
        # Falls through to the normal path (same mode re-initialization is the
        # pre-existing behavior and stays unchanged).
        window._initialize_mode.assert_called_once()


class TestSwitchAllowedWhenIdle:
    def test_switch_proceeds_normally_when_no_request_is_active(self):
        window = _make_window(active=False)

        graphite_window.ChatWindow.on_mode_changed(window, 2)

        window._initialize_mode.assert_called_once_with("API Endpoint", show_dialogs=True)
        window.settings_manager.set_current_mode.assert_called_once_with("API Endpoint")
        window.reinitialize_agent.assert_called_once()
