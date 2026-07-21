"""Contract tests for the composer-picker island bridge (Phase 5 increment
3) - absorbs ComposerPickerPopup (native Qt.Tool popup, deleted this
increment).

Wraps a FAKE composer bridge exposing exactly the surface the real
ComposerBridge already has (route_snapshot()/selectModel()/
setReasoningLevel()/window.show_settings) - nothing about model/reasoning
selection itself is under test here, only that this bridge forwards to it
correctly and reformats route_snapshot()'s dict into option rows the same
way ComposerPickerPopup._refresh_options() used to.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject

from graphlink_composer_picker_bridge import (
    COMPOSER_PICKER_MAX_HEIGHT,
    COMPOSER_PICKER_MIN_HEIGHT,
    ComposerPickerBridge,
)


class _FakeHost(QObject):
    def __init__(self):
        super().__init__()
        self.visible = True

    def setVisible(self, visible):
        self.visible = visible


class _FakeWindow:
    def __init__(self):
        self.settings_opened = 0

    def show_settings(self):
        self.settings_opened += 1


class _FakeComposerBridge:
    def __init__(self, route=None):
        self.window = _FakeWindow()
        self._route = route or {}
        self.selected_models = []
        self.reasoning_levels = []

    def route_snapshot(self):
        return self._route

    def selectModel(self, model_id):
        self.selected_models.append(model_id)

    def setReasoningLevel(self, level):
        self.reasoning_levels.append(level)


_MODEL_ROUTE = {
    "provider": "Ollama",
    "modelId": "llama3",
    "modelOptions": [
        {"id": "llama3", "label": "Llama 3", "active": True, "ready": True, "available": True, "source": "installed"},
        {"id": "mistral", "label": "Mistral", "active": False, "ready": True, "available": True, "source": "installed"},
        {"id": "gone", "label": "Gone", "active": False, "ready": False, "available": False, "source": "configured"},
    ],
    "reasoning": {
        "level": "Thinking",
        "options": [
            {"id": "Quick", "label": "Quick", "description": "Direct responses with less deliberation."},
            {"id": "Thinking", "label": "Thinking", "description": "More deliberate reasoning for complex requests."},
        ],
    },
}


def _snapshot(bridge: ComposerPickerBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    import json

    return json.loads(states[-1])


def test_open_sets_kind_and_bumps_open_token():
    composer_bridge = _FakeComposerBridge(_MODEL_ROUTE)
    bridge = ComposerPickerBridge(composer_bridge)

    bridge.open("model")
    first = _snapshot(bridge)
    bridge.open("reasoning")
    second = _snapshot(bridge)

    assert first["kind"] == "model"
    assert second["kind"] == "reasoning"
    assert second["openToken"] > first["openToken"]


def test_open_with_an_unknown_kind_falls_back_to_model():
    composer_bridge = _FakeComposerBridge(_MODEL_ROUTE)
    bridge = ComposerPickerBridge(composer_bridge)

    bridge.open("something-else")

    assert bridge.kind == "model"


def test_model_options_carry_precomputed_meta_and_current_flag():
    composer_bridge = _FakeComposerBridge(_MODEL_ROUTE)
    bridge = ComposerPickerBridge(composer_bridge)
    bridge.open("model")

    payload = _snapshot(bridge)

    by_id = {option["id"]: option for option in payload["options"]}
    assert by_id["llama3"]["current"] is True
    assert by_id["llama3"]["meta"] == "Selected"
    assert by_id["mistral"]["current"] is False
    assert by_id["mistral"]["meta"] == "Installed"
    assert by_id["gone"]["unavailable"] is True
    assert "verify in Settings" in by_id["gone"]["meta"]
    assert payload["title"] == "Ollama"


def test_reasoning_options_are_never_unavailable_and_use_description_as_meta():
    composer_bridge = _FakeComposerBridge(_MODEL_ROUTE)
    bridge = ComposerPickerBridge(composer_bridge)
    bridge.open("reasoning")

    payload = _snapshot(bridge)

    assert payload["title"] == "Choose response depth"
    by_id = {option["id"]: option for option in payload["options"]}
    assert by_id["Thinking"]["current"] is True
    assert by_id["Thinking"]["meta"] == "More deliberate reasoning for complex requests."
    assert all(option["unavailable"] is False for option in payload["options"])


def test_select_option_dispatches_to_the_real_composer_bridge_by_kind():
    composer_bridge = _FakeComposerBridge(_MODEL_ROUTE)
    bridge = ComposerPickerBridge(composer_bridge)
    bridge.open("model")

    bridge.selectOption("mistral")

    assert composer_bridge.selected_models == ["mistral"]
    assert composer_bridge.reasoning_levels == []


def test_select_option_for_reasoning_kind_dispatches_to_set_reasoning_level():
    composer_bridge = _FakeComposerBridge(_MODEL_ROUTE)
    bridge = ComposerPickerBridge(composer_bridge)
    bridge.open("reasoning")

    bridge.selectOption("Quick")

    assert composer_bridge.reasoning_levels == ["Quick"]
    assert composer_bridge.selected_models == []


def test_select_option_with_a_blank_id_does_nothing():
    composer_bridge = _FakeComposerBridge(_MODEL_ROUTE)
    bridge = ComposerPickerBridge(composer_bridge)
    bridge.open("model")

    bridge.selectOption("   ")

    assert composer_bridge.selected_models == []


def test_select_option_hides_the_host_via_close():
    composer_bridge = _FakeComposerBridge(_MODEL_ROUTE)
    bridge = ComposerPickerBridge(composer_bridge)
    host = _FakeHost()
    bridge.setParent(host)

    bridge.selectOption("mistral")

    assert host.visible is False


def test_request_settings_calls_window_show_settings_and_closes():
    composer_bridge = _FakeComposerBridge(_MODEL_ROUTE)
    bridge = ComposerPickerBridge(composer_bridge)
    host = _FakeHost()
    bridge.setParent(host)

    bridge.requestSettings()

    assert composer_bridge.window.settings_opened == 1
    assert host.visible is False


def test_resize_bounds_to_min_and_max_height():
    bridge = ComposerPickerBridge(_FakeComposerBridge())
    heights = []
    bridge.heightRequested.connect(heights.append)

    bridge.resize(1)
    bridge.resize(9999)

    assert heights == [COMPOSER_PICKER_MIN_HEIGHT, COMPOSER_PICKER_MAX_HEIGHT]
