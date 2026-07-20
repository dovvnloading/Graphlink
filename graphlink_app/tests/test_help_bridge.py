"""Contract tests for the help-dialog island bridge (Phase 4 increment 2)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject

from graphlink_help_bridge import HelpBridge


def _last_payload(bridge: HelpBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


def test_ready_publishes_the_envelope_only():
    bridge = HelpBridge()

    payload = _last_payload(bridge)

    assert payload == {
        "schemaVersion": HelpBridge.SCHEMA_VERSION,
        "minCompatibleSchemaVersion": HelpBridge.MIN_COMPATIBLE_SCHEMA_VERSION,
        "revision": 1,
    }


class _FakeParent(QObject):
    """Real QObject stand-in for the host WebIslandHost's setParent(self)
    call - bridge.close() reads self.parent(), a Qt-level ownership
    relationship that only ever holds a real QObject."""

    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True


def test_close_calls_close_on_the_parent_when_one_exists():
    bridge = HelpBridge()
    parent = _FakeParent()
    bridge.setParent(parent)

    bridge.close()

    assert parent.closed is True


def test_close_is_a_no_op_without_a_parent():
    bridge = HelpBridge()

    # Must not raise even though nothing is attached to close.
    bridge.close()


def test_publish_is_a_no_op_after_dispose():
    bridge = HelpBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.dispose()
    bridge.ready()

    assert states == []
    assert bridge.disposed is True


def test_revision_increments_monotonically_across_calls():
    bridge = HelpBridge()
    revisions = []
    bridge.stateChanged.connect(lambda payload: revisions.append(json.loads(payload)["revision"]))

    bridge.ready()
    bridge.ready()

    assert revisions == [1, 2]
