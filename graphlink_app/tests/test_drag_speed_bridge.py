"""Contract tests for the drag-speed island bridge (Phase 6 increment 5) -
absorbs ChatView.control_widget (native QWidget, deleted this increment).

Wraps a FAKE chat_view exposing exactly the real surface DragSpeedBridge
needs (a plain `_drag_factor` attribute) - nothing about panning itself is
under test here, only that this bridge sets the same attribute
`ChatView._update_drag()` used to compute.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_drag_speed_bridge import (
    DRAG_SPEED_MAX_HEIGHT,
    DRAG_SPEED_MIN_HEIGHT,
    PERCENT_MAX,
    PERCENT_MIN,
    PERCENT_PRESETS,
    DragSpeedBridge,
)


class _FakeChatView:
    def __init__(self):
        self._drag_factor = 1.0


def _snapshot(bridge: DragSpeedBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    import json

    return json.loads(states[-1])


def test_ready_publishes_the_static_configuration():
    bridge = DragSpeedBridge(_FakeChatView())

    payload = _snapshot(bridge)

    assert payload["percentPresets"] == PERCENT_PRESETS == [25, 50, 75, 100]
    assert payload["percentMin"] == PERCENT_MIN == 10
    assert payload["percentMax"] == PERCENT_MAX == 100


def test_set_drag_factor_sets_the_real_chat_view_attribute_directly():
    chat_view = _FakeChatView()
    bridge = DragSpeedBridge(chat_view)

    bridge.setDragFactor(0.5)

    assert chat_view._drag_factor == 0.5


def test_set_drag_factor_does_not_clamp_matching_legacy_lack_of_validation():
    chat_view = _FakeChatView()
    bridge = DragSpeedBridge(chat_view)

    bridge.setDragFactor(3.5)

    assert chat_view._drag_factor == 3.5


def test_set_drag_factor_does_not_publish_no_server_state_to_sync():
    chat_view = _FakeChatView()
    bridge = DragSpeedBridge(chat_view)
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.setDragFactor(0.75)

    assert states == []


def test_resize_bounds_to_min_and_max_height():
    bridge = DragSpeedBridge(_FakeChatView())
    heights = []
    bridge.heightRequested.connect(heights.append)

    bridge.resize(1)
    bridge.resize(9999)

    assert heights == [DRAG_SPEED_MIN_HEIGHT, DRAG_SPEED_MAX_HEIGHT]
