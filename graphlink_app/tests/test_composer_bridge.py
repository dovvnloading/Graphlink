"""Contract tests for the local React composer bridge."""

import json
from pathlib import Path

from graphlink_composer import ComposerController, ComposerRequestState
from graphlink_composer_bridge import ComposerBridge
from graphlink_composer_web import _inline_bundle


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
    bridge.updateDraft("Summarize it")
    bridge.send()

    assert window.send_calls == 1
    bridge.removeContextItem("attachment-0")
    assert window.pending_attachments == []


def test_bridge_cancel_prefers_window_request_callback():
    window = _Window()
    controller = ComposerController()
    bridge = ComposerBridge(window, controller)
    controller.begin_request(text="cancel me")

    bridge.cancel(controller.active_request_id)

    assert window.cancel_calls == 1
    assert controller.state is ComposerRequestState.PREPARING


def test_inline_bundle_is_self_contained_and_keeps_channel_local():
    root = Path(__file__).resolve().parents[2] / "assets" / "composer"
    document = _inline_bundle(root)

    assert "./assets/" not in document
    assert "qrc:///qtwebchannel/qwebchannel.js" in document
    assert "Content-Security-Policy" in document
