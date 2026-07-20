"""Contract tests for the token-counter island bridge."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_token_counter_bridge import TokenCounterBridge


def _last_payload(bridge: TokenCounterBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


def test_ready_publishes_the_initial_all_zero_state():
    bridge = TokenCounterBridge()

    payload = _last_payload(bridge)

    assert payload["inputTokens"] == 0
    assert payload["outputTokens"] == 0
    assert payload["contextTokens"] == 0
    assert payload["totalTokens"] == 0
    assert payload["schemaVersion"] == TokenCounterBridge.SCHEMA_VERSION
    assert payload["revision"] == 1


def test_update_counts_is_a_partial_update_like_the_widget_it_replaces():
    bridge = TokenCounterBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.update_counts(input_tokens=10, context_tokens=5)
    first = json.loads(states[-1])
    assert first["inputTokens"] == 10
    assert first["contextTokens"] == 5
    assert first["outputTokens"] == 0
    assert first["totalTokens"] == 0

    # A later call that only touches output/total must not clobber the
    # input/context values set above - matches TokenCounterWidget.update_counts'
    # exact None-means-unchanged semantics.
    bridge.update_counts(output_tokens=7, total_tokens=22)
    second = json.loads(states[-1])
    assert second["inputTokens"] == 10
    assert second["contextTokens"] == 5
    assert second["outputTokens"] == 7
    assert second["totalTokens"] == 22


def test_reset_zeroes_every_field_and_publishes():
    bridge = TokenCounterBridge()
    bridge.update_counts(input_tokens=10, output_tokens=20, context_tokens=30, total_tokens=40)
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.reset()

    payload = json.loads(states[-1])
    assert payload == {
        "inputTokens": 0,
        "outputTokens": 0,
        "contextTokens": 0,
        "totalTokens": 0,
        "schemaVersion": TokenCounterBridge.SCHEMA_VERSION,
        "minCompatibleSchemaVersion": TokenCounterBridge.MIN_COMPATIBLE_SCHEMA_VERSION,
        "revision": 2,
    }


def test_negative_counts_are_clamped_to_zero():
    bridge = TokenCounterBridge()

    bridge.update_counts(input_tokens=-5, output_tokens=-1)

    payload = _last_payload_without_republishing(bridge)
    assert payload["inputTokens"] == 0
    assert payload["outputTokens"] == 0


def _last_payload_without_republishing(bridge: TokenCounterBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.update_counts()  # no-op field-wise, but publishes the current snapshot
    return json.loads(states[-1])


def test_publish_is_a_no_op_after_dispose():
    bridge = TokenCounterBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.dispose()
    bridge.update_counts(input_tokens=99)

    assert states == []
    assert bridge.disposed is True


def test_revision_increments_monotonically_across_calls():
    bridge = TokenCounterBridge()
    revisions = []
    bridge.stateChanged.connect(lambda payload: revisions.append(json.loads(payload)["revision"]))

    bridge.ready()
    bridge.update_counts(input_tokens=1)
    bridge.update_counts(output_tokens=2)
    bridge.reset()

    assert revisions == [1, 2, 3, 4]
