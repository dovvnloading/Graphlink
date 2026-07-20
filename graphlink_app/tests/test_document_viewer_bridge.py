"""Contract tests for the document-viewer island bridge (Phase 4 increment 3)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject

from graphlink_document_viewer_bridge import DocumentViewerBridge


def _last_payload(bridge: DocumentViewerBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


def test_ready_publishes_empty_content_before_anything_is_shown():
    bridge = DocumentViewerBridge()

    payload = _last_payload(bridge)

    assert payload == {
        "schemaVersion": DocumentViewerBridge.SCHEMA_VERSION,
        "minCompatibleSchemaVersion": DocumentViewerBridge.MIN_COMPATIBLE_SCHEMA_VERSION,
        "revision": 1,
        "content": "",
    }


def test_set_content_publishes_the_given_markdown_text():
    bridge = DocumentViewerBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.set_content("## Code\n\n```python\nprint('hi')\n```")

    payload = json.loads(states[-1])
    assert payload["content"] == "## Code\n\n```python\nprint('hi')\n```"
    assert payload["revision"] == 1


def test_set_content_coerces_none_to_empty_string():
    bridge = DocumentViewerBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.set_content(None)

    assert json.loads(states[-1])["content"] == ""


def test_set_content_publishes_again_on_a_second_call():
    bridge = DocumentViewerBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.set_content("first node's content")
    bridge.set_content("second node's content")

    assert len(states) == 2
    assert json.loads(states[0])["content"] == "first node's content"
    assert json.loads(states[1])["content"] == "second node's content"
    assert json.loads(states[1])["revision"] == 2


class _FakeParent(QObject):
    """Real QObject stand-in for the host WebIslandHost's setParent(self)
    call - bridge.close() reads self.parent(), a Qt-level ownership
    relationship that only ever holds a real QObject."""

    def __init__(self):
        super().__init__()
        self.visible = True

    def setVisible(self, visible: bool):
        self.visible = visible


def test_close_calls_set_visible_false_on_the_parent_when_one_exists():
    bridge = DocumentViewerBridge()
    parent = _FakeParent()
    bridge.setParent(parent)

    bridge.close()

    assert parent.visible is False


def test_close_is_a_no_op_without_a_parent():
    bridge = DocumentViewerBridge()

    # Must not raise even though nothing is attached to close.
    bridge.close()


def test_publish_is_a_no_op_after_dispose():
    bridge = DocumentViewerBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.dispose()
    bridge.set_content("should never be sent")

    assert states == []
    assert bridge.disposed is True


def test_revision_increments_monotonically_across_calls():
    bridge = DocumentViewerBridge()
    revisions = []
    bridge.stateChanged.connect(lambda payload: revisions.append(json.loads(payload)["revision"]))

    bridge.ready()
    bridge.ready()

    assert revisions == [1, 2]
