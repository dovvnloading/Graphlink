"""The optimized scene encoder must preserve every byte and avoid re-encoding 500 rows."""

from __future__ import annotations

import json
from unittest.mock import patch

from backend.domain.scene_json import SceneJSONCache
from backend.tests.perf.graph_factory import LARGE


def test_500_node_steady_publish_skips_full_json_encoding():
    doc = LARGE.build()
    first = doc.scene_payload()
    assert doc.encode_scene_payload(first) == json.dumps(first)

    # The actual 500-node fixture, including charts and images. Wall time
    # belongs to the nightly job, but a full JSON rescan on a steady publish
    # is a deterministic regression the per-PR suite can catch.
    cache = doc._scene_json
    with patch.object(cache, "_encode_full", wraps=cache._encode_full) as slow:
        second = doc.scene_payload()
        assert doc.encode_scene_payload(second) == json.dumps(second)
        assert slow.call_count == 0

    node = next(n for n in doc.nodes.values() if n.kind == "chat" and n.state.is_user)
    node.content = "edited without dirty bookkeeping"
    with patch.object(cache, "_encode_full", wraps=cache._encode_full) as slow:
        edited = doc.scene_payload()
        assert doc.encode_scene_payload(edited) == json.dumps(edited)
        assert slow.call_count == 1
        assert next(row for row in edited["nodes"] if row["id"] == node.id)["content"] == node.content


def test_compact_websocket_scene_is_byte_identical_to_send_json():
    doc = LARGE.build()
    for version in (1, 2):
        payload = doc.scene_payload()
        payload.update(schemaVersion=2, minCompatibleSchemaVersion=2, revision=version)
        message = {"kind": "state", "topic": "scene", "payload": payload}
        if version == 2:
            message["id"] = "resync-ä"
        assert doc.encode_scene_message(message) == json.dumps(
            message, ensure_ascii=False, separators=(",", ":"),
        )
        assert doc.encode_scene_payload(payload) == json.dumps(payload)


def test_compact_websocket_reuses_unchanged_arrays():
    doc = LARGE.build()
    payload = doc.scene_payload()
    message = {"kind": "state", "topic": "scene", "payload": payload}
    cache = doc._scene_json
    assert doc.encode_scene_message(message) == json.dumps(
        message, ensure_ascii=False, separators=(",", ":"),
    )
    with patch.object(cache, "_encode_array_value", wraps=cache._encode_array_value) as slow:
        message["payload"]["revision"] = 2
        assert doc.encode_scene_message(message) == json.dumps(
            message, ensure_ascii=False, separators=(",", ":"),
        )
        assert slow.call_count == 0


def test_cached_fragments_cannot_hide_float_sign_or_nested_mutation():
    cache = SceneJSONCache()
    payload = {
        "nodes": [{"id": "n", "x": 0.0, "y": -0.0, "title": "é", "kind": "chat"}],
        "edges": [{"id": "e", "source": "n", "target": "n", "role": "legacy"}],
        "pins": [],
        "zoomFactor": 1.0,
    }
    for _ in range(2):
        assert cache.encode_payload(payload) == json.dumps(payload)
    payload["nodes"][0]["y"] = 0.0  # Python equality says unchanged; JSON doesn't
    assert cache.encode_payload(payload) == json.dumps(payload)
    payload["nodes"][0]["contentParts"] = [{"type": "text", "text": "start"}]
    assert cache.encode_payload(payload) == json.dumps(payload)
    payload["nodes"][0]["contentParts"][0]["text"] = "changed without replacing the list"
    assert cache.encode_payload(payload) == json.dumps(payload)
    payload["nodes"][0]["isUser"] = 0  # False and 0 compare equal in Python.
    assert cache.encode_payload(payload) == json.dumps(payload)


def test_cached_node_wire_is_unaffected_by_in_place_edits_and_caller_mutation():
    doc = LARGE.build()
    original = doc.scene_payload()
    chat = next(node for node in doc.nodes.values() if node.kind == "chat")
    original_row = next(row for row in original["nodes"] if row["id"] == chat.id)
    original_row["content"] = "a caller's mutation must not poison the cache"
    assert next(row for row in doc.scene_payload()["nodes"] if row["id"] == chat.id)["content"] == chat.content

    chat.x = -0.0
    chat.title = "renamed"
    chat.state.branch_status = "accepted"
    fast = doc.scene_payload()
    doc._node_wire_cache.clear()
    uncached = doc.scene_payload()
    assert json.dumps(fast) == json.dumps(uncached)

    # Mutable nested payloads take the uncached path.
    chat.state.content_parts = [{"type": "text", "text": "one"}]
    doc.scene_payload()
    chat.state.content_parts[0]["text"] = "two"
    fast = doc.scene_payload()
    doc._node_wire_cache.clear()
    assert json.dumps(fast) == json.dumps(doc.scene_payload())

def test_500_node_wire_build_skips_unchanged_rows_without_dirty_bookkeeping():
    import backend.domain.graph as graph_module

    doc = LARGE.build()
    doc.scene_payload()  # populate the per-node cache
    with patch.object(graph_module, "node_wire", wraps=graph_module.node_wire) as build:
        warmed = doc.scene_payload()
        assert build.call_count <= LARGE.chart_count + 2, (
            "an unchanged 500-node scene should not re-project every chat row"
        )
    chat = next(node for node in doc.nodes.values() if node.kind == "chat")
    # Versions are mutable but only their length and the selected index
    # belong on the scene wire; an in-place append must be seen.
    from backend.domain.node_states import ChatVersion

    before = next(row for row in warmed["nodes"] if row["id"] == chat.id)
    chat.state.versions.append(ChatVersion(content="new alternative"))
    after = next(row for row in doc.scene_payload()["nodes"] if row["id"] == chat.id)
    assert after["versionCount"] == before["versionCount"] + 1
