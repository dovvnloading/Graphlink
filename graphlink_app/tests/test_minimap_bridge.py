"""Contract tests for the minimap island bridge (Phase 6 increment 5) -
absorbs MinimapWidget (native QPainter QWidget, deleted this increment).

Wraps a FAKE chat_view/scene exposing exactly the real surface
MinimapBridge needs (`scene().nodes`, a real `scene_changed` Signal,
`_on_minimap_node_selected`) - nothing about ChatScene/ChatView themselves
is under test here, only that this bridge forwards to them correctly and
debounces its own publish against a high-frequency signal.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, Signal

from graphlink_minimap_bridge import MinimapBridge, _preview_for


class _FakeNode:
    def __init__(self, text, is_user):
        self.text = text
        self.is_user = is_user


class _FakeScene(QObject):
    scene_changed = Signal()

    def __init__(self):
        super().__init__()
        self.nodes = []


class _FakeChatView:
    def __init__(self):
        self._scene = _FakeScene()
        self.selected = []

    def scene(self):
        return self._scene

    def _on_minimap_node_selected(self, node):
        self.selected.append(node)


def _snapshot(bridge: MinimapBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    import json

    return json.loads(states[-1])


class TestPreviewText:
    def test_short_text_is_used_verbatim(self):
        assert _preview_for(_FakeNode("Hello there", True)) == "Hello there"

    def test_only_the_first_line_is_used(self):
        assert _preview_for(_FakeNode("First line\nSecond line", True)) == "First line"

    def test_long_text_is_truncated_to_50_chars_with_an_ellipsis(self):
        text = "x" * 80
        preview = _preview_for(_FakeNode(text, True))

        assert len(preview) == 50
        assert preview.endswith("...")
        assert preview == "x" * 47 + "..."

    def test_empty_text_falls_back_to_a_placeholder(self):
        assert _preview_for(_FakeNode("", True)) == "[Attachment/Content Node]"

    def test_whitespace_only_text_falls_back_to_a_placeholder(self):
        assert _preview_for(_FakeNode("   \n  ", True)) == "[Attachment/Content Node]"


def test_ready_publishes_every_node_with_id_and_is_user_and_preview():
    chat_view = _FakeChatView()
    node_a = _FakeNode("Hello", True)
    node_b = _FakeNode("Hi back", False)
    chat_view.scene().nodes = [node_a, node_b]
    bridge = MinimapBridge(chat_view)

    payload = _snapshot(bridge)

    assert len(payload["nodes"]) == 2
    assert payload["nodes"][0] == {"id": str(id(node_a)), "isUser": True, "preview": "Hello"}
    assert payload["nodes"][1] == {"id": str(id(node_b)), "isUser": False, "preview": "Hi back"}


def test_empty_scene_publishes_an_empty_node_list():
    bridge = MinimapBridge(_FakeChatView())

    payload = _snapshot(bridge)

    assert payload["nodes"] == []


def test_select_node_resolves_the_id_back_to_the_real_node_and_dispatches():
    chat_view = _FakeChatView()
    node_a = _FakeNode("Hello", True)
    node_b = _FakeNode("Hi back", False)
    chat_view.scene().nodes = [node_a, node_b]
    bridge = MinimapBridge(chat_view)

    bridge.selectNode(str(id(node_b)))

    assert chat_view.selected == [node_b]


def test_select_node_with_an_unknown_id_does_nothing():
    chat_view = _FakeChatView()
    chat_view.scene().nodes = [_FakeNode("Hello", True)]
    bridge = MinimapBridge(chat_view)

    bridge.selectNode("not-a-real-id")

    assert chat_view.selected == []


class TestDebouncedPublish:
    def test_scene_changed_does_not_publish_immediately(self):
        chat_view = _FakeChatView()
        bridge = MinimapBridge(chat_view)
        states = []
        bridge.stateChanged.connect(states.append)

        chat_view.scene().scene_changed.emit()
        chat_view.scene().scene_changed.emit()
        chat_view.scene().scene_changed.emit()

        assert states == [], "scene_changed must not publish synchronously - it should debounce"

    def test_scene_changed_starts_the_debounce_timer(self):
        chat_view = _FakeChatView()
        bridge = MinimapBridge(chat_view)

        chat_view.scene().scene_changed.emit()

        assert bridge._debounce_timer.isActive() is True

    def test_the_debounce_timer_firing_triggers_exactly_one_publish(self):
        chat_view = _FakeChatView()
        bridge = MinimapBridge(chat_view)
        states = []
        bridge.stateChanged.connect(states.append)

        chat_view.scene().scene_changed.emit()
        chat_view.scene().scene_changed.emit()
        chat_view.scene().scene_changed.emit()
        bridge._debounce_timer.timeout.emit()

        assert len(states) == 1
