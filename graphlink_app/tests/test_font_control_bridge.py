"""Contract tests for the font-control island bridge (Phase 6 increment 4)
- absorbs FontControl (native QWidget, deleted this increment).

Wraps a FAKE chat_view exposing exactly the real surface
FontControlBridge needs (`scene()` returning an object with
`setFontFamily`/`setFontSize`/`setFontColor`) - nothing about ChatScene
itself is under test here, only that this bridge forwards to it correctly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QColor

from graphlink_font_control_bridge import (
    FONT_COLOR_PRESETS,
    FONT_CONTROL_MAX_HEIGHT,
    FONT_CONTROL_MIN_HEIGHT,
    FONT_FAMILIES,
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    FontControlBridge,
)


class _FakeScene:
    def __init__(self):
        self.families = []
        self.sizes = []
        self.colors = []

    def setFontFamily(self, family):
        self.families.append(family)

    def setFontSize(self, size):
        self.sizes.append(size)

    def setFontColor(self, color):
        self.colors.append(color)


class _FakeChatView:
    def __init__(self):
        self._scene = _FakeScene()

    def scene(self):
        return self._scene


def _snapshot(bridge: FontControlBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    import json

    return json.loads(states[-1])


def test_ready_publishes_the_static_font_configuration():
    bridge = FontControlBridge(_FakeChatView())

    payload = _snapshot(bridge)

    assert payload["fontFamilies"] == FONT_FAMILIES
    assert payload["colorPresets"] == FONT_COLOR_PRESETS
    assert payload["sizeMin"] == FONT_SIZE_MIN == 8
    assert payload["sizeMax"] == FONT_SIZE_MAX == 16


def test_set_font_family_dispatches_to_the_real_scene():
    chat_view = _FakeChatView()
    bridge = FontControlBridge(chat_view)

    bridge.setFontFamily("Consolas")

    assert chat_view._scene.families == ["Consolas"]


def test_set_font_size_dispatches_to_the_real_scene():
    chat_view = _FakeChatView()
    bridge = FontControlBridge(chat_view)

    bridge.setFontSize(14)

    assert chat_view._scene.sizes == [14]


def test_set_font_color_converts_hex_to_a_real_qcolor_before_dispatching():
    chat_view = _FakeChatView()
    bridge = FontControlBridge(chat_view)

    bridge.setFontColor("#ABCDEF")

    assert len(chat_view._scene.colors) == 1
    assert isinstance(chat_view._scene.colors[0], QColor)
    assert chat_view._scene.colors[0].name().lower() == "#abcdef"


def test_intents_do_not_publish_no_server_state_to_sync():
    chat_view = _FakeChatView()
    bridge = FontControlBridge(chat_view)
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.setFontFamily("Arial")
    bridge.setFontSize(12)
    bridge.setFontColor("#FFFFFF")

    assert states == []


def test_resize_bounds_to_min_and_max_height():
    bridge = FontControlBridge(_FakeChatView())
    heights = []
    bridge.heightRequested.connect(heights.append)

    bridge.resize(1)
    bridge.resize(9999)

    assert heights == [FONT_CONTROL_MIN_HEIGHT, FONT_CONTROL_MAX_HEIGHT]
