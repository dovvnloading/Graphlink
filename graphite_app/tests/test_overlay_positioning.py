"""Regression test for ChatWindow/ChatView's duplicate overlay-positioning bug.

ChatWindow and ChatView each had their own _update_overlay_positions() method.
ChatWindow's version repositioned search_overlay/notification_banner/
token_counter_widget; ChatView's version separately stacked search_overlay/
control_widget/grid_control/font_control/minimap_widget, accounting for
search_overlay's current visibility when placing the rest of that stack.

show_search_overlay()/_close_search() (which toggle search_overlay's visibility)
only ever called ChatWindow's version. So toggling search while the Controls panel
(control_widget/grid_control/font_control) was already open never recalculated the
panel's Y position to make room for (or reclaim space from) the search bar - the
panel stayed at whatever Y it was given when it was first shown, and the search bar
(always rendered at the top) would land on top of it.

Fix: ChatWindow._update_overlay_positions() now also calls
self.chat_view._update_overlay_positions() so every trigger recalculates the full
stack, not just the subset ChatWindow's own version knows about.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphite_window


def _make_mock_window():
    mock_self = MagicMock()
    mock_self.chat_view.viewport.return_value = MagicMock(width=lambda: 1000, height=lambda: 800)
    mock_self.search_overlay.isVisible.return_value = False
    mock_self.notification_banner.isVisible.return_value = False
    mock_self.token_counter_widget.isVisible.return_value = False
    return mock_self


def test_chat_window_overlay_update_delegates_to_chat_view():
    mock_self = _make_mock_window()

    graphite_window.ChatWindow._update_overlay_positions(mock_self)

    mock_self.chat_view._update_overlay_positions.assert_called_once()


def test_delegation_happens_regardless_of_search_overlay_visibility():
    # The bug specifically manifested when search_overlay's visibility was toggled,
    # so cover both states explicitly rather than only the default (hidden) case.
    for visible in (True, False):
        mock_self = _make_mock_window()
        mock_self.search_overlay.isVisible.return_value = visible

        graphite_window.ChatWindow._update_overlay_positions(mock_self)

        mock_self.chat_view._update_overlay_positions.assert_called_once()
