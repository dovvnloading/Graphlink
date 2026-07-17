"""Contract tests for the local React composer bridge."""

import json
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSize

from graphlink_composer import ComposerController, ComposerRequestState
from graphlink_composer_bridge import (
    COMPOSER_MAX_HEIGHT,
    COMPOSER_MIN_HEIGHT,
    ComposerBridge,
)
from graphlink_composer_popups import (
    ComposerContextPopup,
    ComposerPickerPopup,
    composer_picker_list_height,
    composer_picker_position,
)
from graphlink_composer_web import _inline_bundle, _rounded_region


class _Settings:
    def get_current_mode(self):
        return "Ollama (Local)"

    def get_ollama_chat_model(self):
        return ""

    def get_ollama_scanned_models(self):
        return ["qwen2.5:7b"]


class _Window:
    def __init__(self):
        self.settings_manager = _Settings()
        self.current_node = type("Node", (), {"title": "Chart analysis"})()
        self.pending_attachments = [
            {
                "path": "C:/private/analysis.csv",
                "name": "analysis.csv",
                "kind": "document",
                "token_count": 42,
                "context_label": "CSV table",
            }
        ]
        self.send_calls = 0
        self.cancel_calls = 0

    def send_message(self):
        self.send_calls += 1

    def _main_request_cancel_callback(self):
        self.cancel_calls += 1

    def _handle_attachment_pill_removed(self, path):
        self.pending_attachments = [
            item for item in self.pending_attachments if item["path"] != path
        ]

    def _handle_large_paste_from_input(self, text):
        self.large_paste = text


def test_bridge_publishes_versioned_state_without_attachment_paths():
    window = _Window()
    controller = ComposerController()
    bridge = ComposerBridge(window, controller)
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.ready()
    controller.update_text("Explain the chart")
    state = json.loads(states[-1])

    assert state["schemaVersion"] == 1
    assert state["draft"]["text"] == "Explain the chart"
    assert state["context"]["anchor"]["label"] == "Chart analysis"
    assert state["context"]["items"][0]["name"] == "analysis.csv"
    assert state["context"]["totalTokens"] == 42
    assert "C:/private/analysis.csv" not in states[-1]
    assert state["route"]["modelId"] == "qwen2.5:7b"
    assert state["request"]["canSend"]


def test_bridge_routes_send_and_removes_context_by_opaque_id():
    window = _Window()
    controller = ComposerController()
    bridge = ComposerBridge(window, controller)
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.updateDraft("Summarize it")
    bridge.send()

    assert window.send_calls == 1
    bridge.removeContextItem("attachment-0")
    assert window.pending_attachments == []
    assert json.loads(states[-1])["context"]["items"] == []


def test_bridge_routes_large_paste_to_native_attachment_staging():
    window = _Window()
    bridge = ComposerBridge(window, ComposerController())

    bridge.stageTextAttachment("def explain_chart():\n    return 'context'")

    assert window.large_paste.startswith("def explain_chart")


def test_bridge_cancel_prefers_window_request_callback():
    window = _Window()
    controller = ComposerController()
    bridge = ComposerBridge(window, controller)
    controller.begin_request(text="cancel me")

    bridge.cancel(controller.active_request_id)

    assert window.cancel_calls == 1
    assert controller.state is ComposerRequestState.PREPARING


def test_selector_intents_are_delegated_to_the_desktop_window():
    window = _Window()
    calls = []
    window.open_composer_model_picker = calls.append
    bridge = ComposerBridge(window, ComposerController())

    bridge.openModelSelector()
    bridge.openReasoningSelector()

    assert calls == ["model", "reasoning"]


def test_context_review_is_delegated_to_a_native_desktop_popup():
    window = _Window()
    calls = []
    window.open_composer_context_popup = calls.append
    bridge = ComposerBridge(window, ComposerController())

    bridge.reviewContext()

    assert len(calls) == 1
    assert calls[0]["anchor"]["label"] == "Chart analysis"
    assert calls[0]["items"][0]["name"] == "analysis.csv"


def test_context_popup_is_a_native_window_surface_with_bounded_content():
    popup = ComposerContextPopup(
        {
            "anchor": {"type": "ChatNode", "label": "Chart analysis"},
            "items": [
                {"id": "attachment-0", "kind": "document", "name": "analysis.csv"},
            ],
            "totalTokens": 42,
        }
    )

    assert popup.isWindow()
    assert popup.list_widget.count() == 2
    assert "42" in popup.total_label.text()
    popup.close()
    popup.deleteLater()


def test_picker_position_prefers_space_above_and_clamps_horizontally():
    position = composer_picker_position(
        QRect(0, 0, 1000, 800),
        QRect(100, 700, 800, 72),
        QSize(360, 260),
    )

    assert position == QPoint(530, 432)


def test_picker_position_falls_back_to_viewport_margin_when_popup_is_tall():
    position = composer_picker_position(
        QRect(0, 0, 400, 300),
        QRect(40, 210, 320, 70),
        QSize(360, 280),
    )

    assert position == QPoint(8, 8)


def test_picker_list_height_is_content_sized_and_capped_for_catalogs():
    assert composer_picker_list_height([]) == 0
    assert composer_picker_list_height([50, 50]) == 120
    assert composer_picker_list_height([50] * 6) == 300


def test_bridge_clamps_compact_composer_height_requests():
    bridge = ComposerBridge(_Window(), ComposerController())
    heights = []
    bridge.heightRequested.connect(heights.append)

    bridge.resize(1)
    bridge.resize(10_000)

    assert heights == [COMPOSER_MIN_HEIGHT, COMPOSER_MAX_HEIGHT]


def test_composer_native_mask_has_rounded_corners():
    region = _rounded_region(QRect(0, 0, 100, 40), 12)

    assert region.contains(QPoint(50, 20))
    assert not region.contains(QPoint(0, 0))
    assert region.contains(QPoint(12, 0))


def test_picker_event_filter_is_safe_for_reasoning_popup_without_search_control():
    popup = ComposerPickerPopup("reasoning", {"reasoning": {"options": []}})

    assert popup.eventFilter(object(), QEvent(QEvent.Type.MouseMove)) is False

    popup.close()
    popup.deleteLater()


def test_inline_bundle_is_self_contained_and_keeps_channel_local():
    root = Path(__file__).resolve().parents[2] / "assets" / "composer"
    document = _inline_bundle(root)

    assert "./assets/" not in document
    assert "qrc:///qtwebchannel/qwebchannel.js" in document
    assert "Content-Security-Policy" in document
