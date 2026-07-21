"""Contract tests for the search-overlay island bridge (Phase 5 increment 1)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject

from graphlink_search_overlay_bridge import SearchOverlayBridge


class _FakeNode:
    def __init__(self, name):
        self.name = name
        self.selected = False

    def setSelected(self, value):
        self.selected = value


class _FakeScene:
    def __init__(self, matches_by_query=None):
        self._matches_by_query = matches_by_query or {}
        self.highlighted = None
        self.selection_cleared = False

    def find_items(self, text):
        return list(self._matches_by_query.get(text, []))

    def update_search_highlight(self, matches):
        self.highlighted = list(matches)

    def clearSelection(self):
        self.selection_cleared = True


class _FakeChatView:
    def __init__(self, scene):
        self._scene = scene
        self.centered_on = None

    def scene(self):
        return self._scene

    def centerOn(self, target):
        self.centered_on = target


class _FakeParent(QObject):
    def __init__(self):
        super().__init__()
        self.visible = True

    def setVisible(self, visible):
        self.visible = visible


def _states(bridge):
    payloads = []
    bridge.stateChanged.connect(lambda p: payloads.append(json.loads(p)))
    return payloads


def test_ready_publishes_no_matches_before_any_search():
    view = _FakeChatView(_FakeScene())
    bridge = SearchOverlayBridge(view)
    payloads = _states(bridge)

    bridge.ready()

    assert payloads[-1]["currentIndex"] == -1
    assert payloads[-1]["totalMatches"] == 0


def test_search_publishes_match_count_and_highlights_the_scene():
    a, b = _FakeNode("a"), _FakeNode("b")
    scene = _FakeScene({"hi": [a, b]})
    view = _FakeChatView(scene)
    bridge = SearchOverlayBridge(view)
    payloads = _states(bridge)

    bridge.search("hi")

    assert payloads[-1]["totalMatches"] == 2
    assert payloads[-1]["currentIndex"] == -1
    assert scene.highlighted == [a, b]


def test_search_with_empty_text_clears_matches():
    scene = _FakeScene({"hi": [_FakeNode("a")]})
    view = _FakeChatView(scene)
    bridge = SearchOverlayBridge(view)
    bridge.search("hi")

    bridge.search("")

    assert scene.highlighted == []


def test_next_cycles_forward_and_wraps():
    a, b, c = _FakeNode("a"), _FakeNode("b"), _FakeNode("c")
    scene = _FakeScene({"q": [a, b, c]})
    view = _FakeChatView(scene)
    bridge = SearchOverlayBridge(view)
    bridge.search("q")
    payloads = _states(bridge)

    bridge.next()
    assert payloads[-1]["currentIndex"] == 0
    assert view.centered_on is a

    bridge.next()
    assert payloads[-1]["currentIndex"] == 1

    bridge.next()
    assert payloads[-1]["currentIndex"] == 2

    bridge.next()  # wraps back to 0
    assert payloads[-1]["currentIndex"] == 0


def test_previous_cycles_backward_and_wraps():
    a, b = _FakeNode("a"), _FakeNode("b")
    scene = _FakeScene({"q": [a, b]})
    view = _FakeChatView(scene)
    bridge = SearchOverlayBridge(view)
    bridge.search("q")
    payloads = _states(bridge)

    # Matches the legacy formula exactly: (-1 - 1 + 2) % 2 == 0, not 1 - the
    # very first previous() from the initial "no current match" state lands
    # on index 0, same as the old ChatWindow._find_previous_match did.
    bridge.previous()
    assert payloads[-1]["currentIndex"] == 0
    assert view.centered_on is a

    bridge.previous()  # 0 -> wraps to the last index (1)
    assert payloads[-1]["currentIndex"] == 1
    assert view.centered_on is b


def test_next_and_previous_are_no_ops_with_zero_matches():
    view = _FakeChatView(_FakeScene())
    bridge = SearchOverlayBridge(view)
    payloads = _states(bridge)

    bridge.next()
    bridge.previous()

    assert payloads == []


def test_close_clears_highlight_hides_the_host_and_publishes_zero_matches():
    scene = _FakeScene({"q": [_FakeNode("a")]})
    view = _FakeChatView(scene)
    bridge = SearchOverlayBridge(view)
    bridge.search("q")
    parent = _FakeParent()
    bridge.setParent(parent)
    payloads = _states(bridge)

    bridge.close()

    assert scene.highlighted == []
    assert parent.visible is False
    assert payloads[-1]["totalMatches"] == 0
    assert payloads[-1]["currentIndex"] == -1


def test_close_is_a_no_op_without_a_parent():
    view = _FakeChatView(_FakeScene())
    bridge = SearchOverlayBridge(view)

    bridge.close()  # must not raise


def test_publish_is_a_no_op_after_dispose():
    view = _FakeChatView(_FakeScene())
    bridge = SearchOverlayBridge(view)
    payloads = _states(bridge)

    bridge.dispose()
    bridge.ready()

    assert payloads == []
    assert bridge.disposed is True
