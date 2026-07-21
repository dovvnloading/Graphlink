"""Contract tests for the grid-control island bridge (Phase 6 increment 4)
- absorbs GridControl (native QWidget, deleted this increment).

Wraps a FAKE chat_view exposing exactly the real surface
GridControlBridge needs (`grid_settings`, `update()`,
`_on_snap_toggled`/`_on_ortho_toggled`/`_on_guides_toggled`/
`_on_fade_connections_toggled`) - nothing about ChatScene/ChatView
themselves is under test here, only that this bridge forwards to them
correctly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_grid_control_bridge import (
    GRID_CONTROL_MAX_HEIGHT,
    GRID_CONTROL_MIN_HEIGHT,
    GridControlBridge,
)
from graphlink_grid_view_settings import GridViewSettings


class _FakeChatView:
    def __init__(self):
        self.grid_settings = GridViewSettings()
        self.update_calls = 0
        self.snap_calls = []
        self.ortho_calls = []
        self.guides_calls = []
        self.fade_calls = []

    def update(self):
        self.update_calls += 1

    def _on_snap_toggled(self, enabled):
        self.snap_calls.append(enabled)

    def _on_ortho_toggled(self, enabled):
        self.ortho_calls.append(enabled)

    def _on_guides_toggled(self, enabled):
        self.guides_calls.append(enabled)

    def _on_fade_connections_toggled(self, enabled):
        self.fade_calls.append(enabled)


def _snapshot(bridge: GridControlBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    import json

    return json.loads(states[-1])


def test_ready_publishes_the_current_grid_settings():
    chat_view = _FakeChatView()
    bridge = GridControlBridge(chat_view)

    payload = _snapshot(bridge)

    assert payload["gridSize"] == 10
    assert payload["gridOpacityPercent"] == 30
    assert payload["gridStyle"] == "Dots"
    assert payload["gridColor"] == "#555555"


def test_payload_carries_the_static_size_and_style_presets():
    bridge = GridControlBridge(_FakeChatView())

    payload = _snapshot(bridge)

    assert payload["sizePresets"] == [10, 20, 50, 100]
    assert payload["stylePresets"] == ["Dots", "Lines", "Cross"]


def test_payload_carries_5_color_presets_including_theme_derived_ones():
    bridge = GridControlBridge(_FakeChatView())

    payload = _snapshot(bridge)

    assert len(payload["colorPresets"]) == 5
    assert payload["colorPresets"][0] == "#404040"
    assert payload["colorPresets"][1] == "#555555"


def test_set_grid_size_mutates_the_real_model_and_triggers_repaint():
    chat_view = _FakeChatView()
    bridge = GridControlBridge(chat_view)

    bridge.setGridSize(50)

    assert chat_view.grid_settings.grid_size == 50
    assert chat_view.update_calls == 1


def test_set_grid_size_republishes():
    chat_view = _FakeChatView()
    bridge = GridControlBridge(chat_view)
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.setGridSize(20)

    import json

    assert json.loads(states[-1])["gridSize"] == 20


def test_set_grid_opacity_percent_converts_to_a_0_to_1_float_on_the_model():
    chat_view = _FakeChatView()
    bridge = GridControlBridge(chat_view)

    bridge.setGridOpacityPercent(75)

    assert chat_view.grid_settings.grid_opacity == 0.75


def test_set_grid_opacity_percent_clamps_out_of_range_values():
    chat_view = _FakeChatView()
    bridge = GridControlBridge(chat_view)

    bridge.setGridOpacityPercent(-10)
    assert chat_view.grid_settings.grid_opacity == 0.0

    bridge.setGridOpacityPercent(9999)
    assert chat_view.grid_settings.grid_opacity == 1.0


def test_set_grid_style_mutates_the_real_model():
    chat_view = _FakeChatView()
    bridge = GridControlBridge(chat_view)

    bridge.setGridStyle("Cross")

    assert chat_view.grid_settings.grid_style == "Cross"
    assert chat_view.update_calls == 1


def test_set_grid_color_mutates_the_real_model():
    chat_view = _FakeChatView()
    bridge = GridControlBridge(chat_view)

    bridge.setGridColor("#ABCDEF")

    assert chat_view.grid_settings.grid_color == "#ABCDEF"
    assert chat_view.update_calls == 1


def test_the_4_toggle_intents_forward_to_chat_views_own_existing_methods():
    chat_view = _FakeChatView()
    bridge = GridControlBridge(chat_view)

    bridge.setSnapToGrid(True)
    bridge.setOrthogonalConnections(True)
    bridge.setSmartGuides(True)
    bridge.setFadeConnections(True)

    assert chat_view.snap_calls == [True]
    assert chat_view.ortho_calls == [True]
    assert chat_view.guides_calls == [True]
    assert chat_view.fade_calls == [True]


def test_the_4_toggle_intents_do_not_publish_no_server_state_to_sync():
    chat_view = _FakeChatView()
    bridge = GridControlBridge(chat_view)
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.setSnapToGrid(True)

    assert states == []


def test_resize_bounds_to_min_and_max_height():
    bridge = GridControlBridge(_FakeChatView())
    heights = []
    bridge.heightRequested.connect(heights.append)

    bridge.resize(1)
    bridge.resize(9999)

    assert heights == [GRID_CONTROL_MIN_HEIGHT, GRID_CONTROL_MAX_HEIGHT]
