"""Tests for the transport-agnostic IslandBridge base class.

graphlink_island_bridge.py has no Qt imports at all - deliberately, since it is
meant to be the shared publish/dispose/schemaVersion/revision contract behind
every future desktop<->web bridge, including ones that will eventually run over
a non-Qt transport. These tests exercise it with a bare-minimum fake transport
and no QApplication, no Qt event loop, and no other test in this file requiring
the shared conftest.py QApplication fixture - if a future edit accidentally
makes this module depend on Qt, these tests start needing that fixture and the
regression is visible immediately.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from graphlink_island_bridge import IslandBridge


class _RecordingBridge(IslandBridge):
    """Minimal concrete bridge: a plain list stands in for a real transport."""

    def __init__(self, state=None):
        super().__init__()
        self._state = dict(state or {"value": 0})
        self.sent = []
        self.after_publish_calls = []
        self.disposed_calls = 0

    def set_state(self, **kwargs):
        self._state.update(kwargs)

    def _build_state_payload(self):
        return dict(self._state)

    def _transport_send(self, payload_json):
        self.sent.append(payload_json)

    def _after_publish(self, payload, serialized):
        self.after_publish_calls.append((payload, serialized))

    def _on_dispose(self):
        self.disposed_calls += 1


def test_publish_sends_sorted_json_with_schema_version_and_revision():
    bridge = _RecordingBridge(state={"b": 2, "a": 1})

    bridge.publish()

    assert len(bridge.sent) == 1
    payload = json.loads(bridge.sent[0])
    assert payload["schemaVersion"] == 1
    assert payload["revision"] == 1
    assert payload["a"] == 1 and payload["b"] == 2
    # sort_keys=True: "a" before "b" before the injected keys alphabetically
    assert bridge.sent[0].index('"a"') < bridge.sent[0].index('"b"')


def test_revision_increments_monotonically_across_publishes():
    bridge = _RecordingBridge()

    bridge.publish()
    bridge.publish()
    bridge.publish()

    revisions = [json.loads(payload)["revision"] for payload in bridge.sent]
    assert revisions == [1, 2, 3]


def test_build_state_payload_need_not_include_schema_or_revision():
    # _build_state_payload() returns only domain state; publish() injects the
    # envelope. A subclass accidentally including its own schemaVersion/revision
    # keys gets overwritten, never doubled or left stale.
    bridge = _RecordingBridge(state={"schemaVersion": 999, "revision": 999})

    bridge.publish()

    payload = json.loads(bridge.sent[0])
    assert payload["schemaVersion"] == 1
    assert payload["revision"] == 1


def test_after_publish_hook_receives_the_full_envelope():
    bridge = _RecordingBridge(state={"value": 42})

    bridge.publish()

    assert len(bridge.after_publish_calls) == 1
    payload, serialized = bridge.after_publish_calls[0]
    assert payload["value"] == 42
    assert payload["revision"] == 1
    assert serialized == bridge.sent[0]


def test_dispose_is_idempotent_and_calls_the_hook_exactly_once():
    bridge = _RecordingBridge()

    assert bridge.disposed is False
    bridge.dispose()
    bridge.dispose()
    bridge.dispose()

    assert bridge.disposed is True
    assert bridge.disposed_calls == 1


def test_publish_is_a_no_op_after_dispose():
    bridge = _RecordingBridge()
    bridge.publish()
    sent_before = len(bridge.sent)

    bridge.dispose()
    bridge.publish()
    bridge.publish()

    assert len(bridge.sent) == sent_before


def test_unimplemented_hooks_raise_not_implemented_for_a_bare_subclass():
    class _Bare(IslandBridge):
        pass

    bridge = _Bare()
    try:
        bridge.publish()
    except NotImplementedError:
        pass
    else:
        raise AssertionError("expected NotImplementedError from an unimplemented bridge")
