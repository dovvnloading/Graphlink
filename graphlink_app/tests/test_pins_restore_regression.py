"""Regression for the silent startup crash the first time a saved session
contained navigation pins.

The session deserializer's _load_pins (and its _handle_load_error recovery
path, plus new_chat and a connections-module remnant) still called the
LEGACY PinOverlay's clear_pins()/add_pin_button() - methods that do not
exist on PinOverlayHost (Phase 5's replacement, wired as window.pin_overlay).
The AttributeError aborted the ENTIRE chat restore at launch; the recovery
handler then crashed on the same missing method, and the app exited with no
window - presenting as "the app sits spinning and never opens." It was
data-dependent: nothing surfaced until a user actually created pins and
saved, which is why empty-session test drives never caught it.

The store-based design makes those imperative calls unnecessary: scene.clear()
empties scene.pin_store (the overlay's reactive source of truth) and
scene.add_navigation_pin() registers restored pins in it. These tests pin the
contract down with a pin_overlay object that has NO legacy methods at all -
any reintroduced legacy call fails loudly here.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_scene import ChatScene
from graphlink_session.deserializers import SceneDeserializer


def _make_window_and_scene():
    window = MagicMock()
    # The decisive detail: a pin_overlay with NO methods at all. The real
    # PinOverlayHost has no clear_pins/add_pin_button either - a MagicMock
    # would silently absorb the legacy calls and hide the regression.
    window.pin_overlay = types.SimpleNamespace()
    scene = ChatScene(window=window)
    window.chat_view.scene.return_value = scene
    return window, scene


_PIN_PAYLOAD = [
    {
        "id": "pin-1",
        "title": "First waypoint",
        "note": "remember this",
        "position": {"x": 120.0, "y": 340.0},
        "sort_order": 0,
    },
    {
        "id": "pin-2",
        "title": "Second waypoint",
        "note": "",
        "position": {"x": -40.0, "y": 12.5},
        "sort_order": 1,
    },
]


class TestLoadPinsAgainstTheRealHostSurface:
    def test_restoring_pins_does_not_touch_pin_overlay_and_lands_in_the_store(self):
        window, scene = _make_window_and_scene()
        deserializer = SceneDeserializer(window)

        deserializer._load_pins(scene, _PIN_PAYLOAD)  # must not raise

        assert len(scene.pin_store.records) == 2
        titles = sorted(record.title for record in scene.pin_store.records)
        assert titles == ["First waypoint", "Second waypoint"]

    def test_restoring_zero_pins_is_a_no_op(self):
        window, scene = _make_window_and_scene()
        deserializer = SceneDeserializer(window)

        deserializer._load_pins(scene, [])
        deserializer._load_pins(scene, None)

        assert len(scene.pin_store.records) == 0

    def test_load_error_recovery_does_not_touch_pin_overlay(self):
        # The recovery path itself crashed on the same missing legacy method
        # it was recovering from - the app then died with no window at all.
        window, scene = _make_window_and_scene()
        deserializer = SceneDeserializer(window)

        deserializer._handle_load_error(scene, RuntimeError("boom"))  # must not raise

        assert window.current_node is None
