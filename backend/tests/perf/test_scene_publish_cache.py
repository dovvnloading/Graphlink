"""ADR-019: warm 500-node publishes must not rehash identical histories."""

from __future__ import annotations

import json
from unittest.mock import patch

from backend.domain import staleness
from backend.domain.staleness import fingerprint_for_reply
from backend.tests.perf.graph_factory import LARGE


def test_500_node_publish_reuses_lineage_without_changing_wire_bytes():
    doc = LARGE.build()  # 500 nodes, 697 edges, ten charts, five images
    reply = next(
        node for node in doc.nodes.values()
        if node.kind == "chat" and not node.state.is_user and doc._branch_parent_edge(node.id)
    )
    parent_id = doc._branch_parent_edge(reply.id).source
    reply.state.provider = "test-provider"
    reply.state.model = "test-model"
    reply.state.reply_history_fingerprint = fingerprint_for_reply(
        doc, parent_id, "test-provider", "test-model", False,
    )
    doc.update_chat_node_content(parent_id, "an edited upstream message")
    expected = json.dumps(doc.scene_payload())
    assert any(row.get("staleness") == "out_of_date" for row in doc.scene_payload()["nodes"])

    with patch.object(staleness, "_message_input", wraps=staleness._message_input) as message_input:
        for _ in range(3):
            assert json.dumps(doc.scene_payload()) == expected
        doc.take_dirty_patch_ops()  # The patch path shares the same lineage cache.
        assert message_input.call_count == 0

    # Direct edits must invalidate the right node even without a dirty-id
    # notification. The last chat leaf has no descendants in LARGE's tree.
    leaf = next(node for node in reversed(list(doc.nodes.values())) if node.kind == "chat")
    leaf.content = "changed directly"
    with patch.object(staleness, "_message_input", wraps=staleness._message_input) as message_input:
        changed = json.dumps(doc.scene_payload())
        assert message_input.call_count == 1
    assert changed != expected
    assert "changed directly" in changed

    # Compare warm and forced-cold encodes, not just payload dict equality:
    # the optimization must preserve JSON order, spelling and whitespace.
    for node in doc.nodes.values():
        node.__dict__.pop("_staleness_digest_cache", None)
    assert json.dumps(doc.scene_payload()) == changed
