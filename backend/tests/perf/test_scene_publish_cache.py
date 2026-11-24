"""ADR-019: warm 500-node publishes must not rehash identical histories."""

from __future__ import annotations

import json
from unittest.mock import patch

from backend.domain import staleness
from backend.domain.staleness import fingerprint_for_reply
from backend.tests.perf.graph_factory import LARGE, SMALL


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

    # An untracked leaf is still projected onto the wire, but it cannot
    # affect a badge and must not be rehashed. Direct edits need no dirty-id
    # notification for either wire rows or tracked ancestry.
    leaf = next(node for node in reversed(list(doc.nodes.values())) if node.kind == "chat")
    leaf.content = "changed directly"
    with patch.object(staleness, "_message_input", wraps=staleness._message_input) as message_input:
        changed = json.dumps(doc.scene_payload())
        assert message_input.call_count == 0
    assert changed != expected
    assert "changed directly" in changed

    # Compare warm and forced-cold encodes, not just payload dict equality:
    # the optimization must preserve JSON order, spelling and whitespace.
    for node in doc.nodes.values():
        node.__dict__.pop("_staleness_digest_cache", None)
    assert json.dumps(doc.scene_payload()) == changed


def test_large_unstamped_scene_visits_only_badge_ancestry():
    doc = LARGE.build()
    assert doc.nonchat_upstream_fingerprints
    parents = staleness._parents(doc)
    required = set()
    for node in doc.nodes.values():
        if node.kind in ("chat", "thinking"):
            continue
        cursor = node.id
        while cursor is not None and cursor not in required:
            required.add(cursor)
            cursor = parents.get(cursor)
    # The benchmark creates only 15 derived nodes in a 500-node graph.
    # A warm publish must not walk the other, untracked chat branches.
    assert len(required) < len(doc.nodes) // 2
    expected = json.dumps(doc.scene_payload())
    with patch.object(
        staleness, "_immutable_message_signature", wraps=staleness._immutable_message_signature,
    ) as signature:
        assert json.dumps(doc.scene_payload()) == expected
        assert signature.call_count == len(required)
        assert {call.args[0].id for call in signature.call_args_list} == required

    # Changing a tracked ancestor still affects every derived descendant,
    # even with a direct mutation and no dirty notification.
    root = next(node for node in doc.nodes.values() if node.id not in parents)
    root.content = "edited directly"
    expected_statuses = staleness._lineage(doc)[1]
    assert expected_statuses
    assert staleness.scene_staleness(doc) == expected_statuses
    rows = doc.scene_payload()["nodes"]
    assert {row["id"]: row["staleness"] for row in rows if "staleness" in row} == expected_statuses


def test_chat_only_publish_skips_unowned_metadata_projections():
    doc = SMALL.build()
    assert not doc.nonchat_upstream_fingerprints
    assert not doc.plugin_node_serializers
    expected = json.dumps(doc.scene_payload())
    with (
        patch.object(doc, "_text_input_status", wraps=doc._text_input_status) as text_status,
        patch.object(doc, "_table_output_wire", wraps=doc._table_output_wire) as table_wire,
        patch.object(doc, "_plugin_state_wire", wraps=doc._plugin_state_wire) as plugin_wire,
        patch.object(staleness, "_lineage", wraps=staleness._lineage) as lineage,
    ):
        assert json.dumps(doc.scene_payload()) == expected
        doc.take_dirty_patch_ops()
        assert not doc.take_dirty_patch_ops()
        assert text_status.call_count == table_wire.call_count == plugin_wire.call_count == lineage.call_count == 0
