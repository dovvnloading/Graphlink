"""Contract tests for the composer-context island bridge (Phase 5 increment
3) - absorbs ComposerContextPopup (native Qt.Tool popup, deleted this
increment).

Wraps a FAKE composer bridge exposing exactly removeContextItem() - the one
real method call this bridge forwards to; the context DICT itself is passed
directly into open(), mirroring ComposerBridge.reviewContext()'s own
already-tested construction of that dict (not re-verified here).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject

from graphlink_composer_context_bridge import (
    COMPOSER_CONTEXT_MAX_HEIGHT,
    COMPOSER_CONTEXT_MIN_HEIGHT,
    ComposerContextBridge,
)


class _FakeHost(QObject):
    def __init__(self):
        super().__init__()
        self.visible = True

    def setVisible(self, visible):
        self.visible = visible


class _FakeComposerBridge:
    def __init__(self):
        self.removed = []

    def removeContextItem(self, item_id):
        self.removed.append(item_id)


_CONTEXT = {
    "anchor": {"id": "node-1", "label": "Chart analysis", "type": "ChatNode"},
    "items": [
        {"id": "attachment-0", "name": "analysis.csv", "kind": "document", "tokenCount": 42},
    ],
    "totalTokens": 42,
    "reviewAvailable": True,
}


def _snapshot(bridge: ComposerContextBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


def test_open_republishes_the_given_context_verbatim():
    composer_bridge = _FakeComposerBridge()
    bridge = ComposerContextBridge(composer_bridge)

    bridge.open(_CONTEXT)
    payload = _snapshot(bridge)

    assert payload["anchor"] == {"id": "node-1", "label": "Chart analysis", "type": "ChatNode"}
    assert payload["items"] == [{"id": "attachment-0", "name": "analysis.csv", "kind": "document", "tokenCount": 42}]
    assert payload["totalTokens"] == 42


def test_open_with_no_anchor_publishes_null():
    composer_bridge = _FakeComposerBridge()
    bridge = ComposerContextBridge(composer_bridge)

    bridge.open({"anchor": None, "items": [], "totalTokens": 0})
    payload = _snapshot(bridge)

    assert payload["anchor"] is None
    assert payload["items"] == []


def test_open_with_a_non_dict_context_does_not_raise():
    bridge = ComposerContextBridge(_FakeComposerBridge())

    bridge.open(None)
    payload = _snapshot(bridge)

    assert payload["anchor"] is None
    assert payload["items"] == []
    assert payload["totalTokens"] == 0


def test_remove_context_item_forwards_to_the_real_composer_bridge():
    composer_bridge = _FakeComposerBridge()
    bridge = ComposerContextBridge(composer_bridge)
    bridge.open(_CONTEXT)

    bridge.removeContextItem("attachment-0")

    assert composer_bridge.removed == ["attachment-0"]


def test_remove_context_item_always_closes_even_with_a_blank_id():
    """Matches the legacy popup's own _remove_item(): unconditionally closes
    afterward, even for an empty id - removing any one row closes the whole
    review panel."""
    composer_bridge = _FakeComposerBridge()
    bridge = ComposerContextBridge(composer_bridge)
    host = _FakeHost()
    bridge.setParent(host)

    bridge.removeContextItem("   ")

    assert composer_bridge.removed == []
    assert host.visible is False


def test_remove_context_item_hides_the_host():
    composer_bridge = _FakeComposerBridge()
    bridge = ComposerContextBridge(composer_bridge)
    host = _FakeHost()
    bridge.setParent(host)

    bridge.removeContextItem("attachment-0")

    assert host.visible is False


def test_close_hides_the_host():
    bridge = ComposerContextBridge(_FakeComposerBridge())
    host = _FakeHost()
    bridge.setParent(host)

    bridge.close()

    assert host.visible is False


def test_close_is_a_no_op_without_a_parent():
    bridge = ComposerContextBridge(_FakeComposerBridge())

    bridge.close()  # must not raise


def test_resize_bounds_to_min_and_max_height():
    bridge = ComposerContextBridge(_FakeComposerBridge())
    heights = []
    bridge.heightRequested.connect(heights.append)

    bridge.resize(1)
    bridge.resize(9999)

    assert heights == [COMPOSER_CONTEXT_MIN_HEIGHT, COMPOSER_CONTEXT_MAX_HEIGHT]
