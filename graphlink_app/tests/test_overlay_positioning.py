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

Phase 5 increment 1: search_overlay's own positioning moved from an inline
.move() call here to overlay_coordinator.reposition_all() (see
graphlink_overlay_coordinator.py) - ChatView still needs search_overlay's
height/visibility to stack control_widget/grid_control/font_control below it,
now via a direct reference (_search_overlay_host) instead of a findChild()
probe, but no longer repositions it a second time itself.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_window


def _make_mock_window():
    mock_self = MagicMock()
    mock_self.chat_view.viewport.return_value = MagicMock(width=lambda: 1000, height=lambda: 800)
    mock_self.composer = None
    mock_self.composer_overlay_parent = None
    mock_self.notification_banner.isVisible.return_value = False
    mock_self.token_counter_widget.isVisible.return_value = False
    return mock_self


def test_chat_window_overlay_update_delegates_to_chat_view():
    mock_self = _make_mock_window()

    graphlink_window.ChatWindow._update_overlay_positions(mock_self)

    mock_self.chat_view._update_overlay_positions.assert_called_once()


def test_chat_window_overlay_update_delegates_to_the_coordinator():
    mock_self = _make_mock_window()

    graphlink_window.ChatWindow._update_overlay_positions(mock_self)

    mock_self.overlay_coordinator.reposition_all.assert_called_once()


def test_delegation_happens_regardless_of_search_overlay_visibility_in_chat_view():
    # The original bug specifically manifested when search_overlay's visibility
    # was toggled, so cover both states explicitly rather than only the default
    # (hidden) case - now exercised on ChatView's own mock, since that's where
    # search_overlay's visibility is actually read (via _search_overlay_host).
    for visible in (True, False):
        mock_self = _make_mock_window()
        mock_self.chat_view._search_overlay_host.isVisible.return_value = visible

        graphlink_window.ChatWindow._update_overlay_positions(mock_self)

        mock_self.chat_view._update_overlay_positions.assert_called_once()


def test_composer_is_centered_over_the_graph_viewport():
    mock_self = _make_mock_window()
    composer = MagicMock()
    composer.height.return_value = 96
    mock_self.composer = composer

    graphlink_window.ChatWindow._update_composer_overlay(mock_self)

    composer.setFixedWidth.assert_called_once_with(820)
    composer.setFixedHeight.assert_called_once_with(96)
    composer.move.assert_called_once_with(90, 686)
    composer.raise_.assert_called_once()


def test_composer_position_is_translated_into_window_overlay_coordinates():
    mock_self = _make_mock_window()
    overlay_parent = MagicMock()
    viewport = mock_self.chat_view.viewport.return_value
    viewport.mapTo.return_value = graphlink_window.QPoint(25, 40)
    mock_self.composer_overlay_parent = overlay_parent
    composer = MagicMock()
    composer.height.return_value = 96
    mock_self.composer = composer

    graphlink_window.ChatWindow._update_composer_overlay(mock_self)

    viewport.mapTo.assert_called_once_with(overlay_parent, graphlink_window.QPoint(0, 0))
    composer.move.assert_called_once_with(115, 726)


def test_visible_notification_is_raised_above_composer():
    mock_self = _make_mock_window()
    mock_self.notification_banner.isVisible.return_value = True
    composer = MagicMock()
    composer.height.return_value = 96
    mock_self.composer = composer

    graphlink_window.ChatWindow._update_composer_overlay(mock_self)

    mock_self.notification_banner.raise_.assert_called_once()
