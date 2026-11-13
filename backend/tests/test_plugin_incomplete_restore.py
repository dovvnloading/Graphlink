"""A missing plugin must not turn a recoverable saved node into permanent data loss.

Source graphs can outlive their plugin installation. Unknown node payloads
are currently skipped by the restorer; saving that incomplete live scene
must never overwrite the original database row.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.chat_library import (
    load_chat_row, make_load_chat, make_save_chat, register_chat_library, save_chat_atomically_row,
)
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import NodeKindSpec, PluginNodeSeed, PluginRegistry
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.canvas import SceneDocument


UNKNOWN_KIND = "absent_plugin.thing"


def _saved_graph():
    return {
        "title": "Do not overwrite original",
        "data": {
            "nodes": [
                {
                    "id": "old-plugin-id", "node_type": UNKNOWN_KIND,
                    "title": "Plugin-created node", "content": "important data",
                    "position": {"x": 40, "y": 50},
                },
                {
                    "id": "old-chat-id", "node_type": "chat",
                    "raw_content": "unrelated note", "position": {"x": 1, "y": 2},
                },
            ],
            "edges": [],
        },
    }


def _load_missing_plugin():
    document = SceneDocument()
    restore_chat_into_document(
        document, _saved_graph(), [], [],
        plugin_registry=PluginRegistry(),
    )
    assert len(document.nodes) == 1, "the missing node must be absent from live canvas"
    assert document.unrestored_node_count == 1
    return document


def test_incomplete_graph_refuses_save_before_serializing_anything():
    document = _load_missing_plugin()
    with pytest.raises(RuntimeError, match="Cannot overwrite this saved graph"):
        build_chat_data(document, plugin_registry=PluginRegistry())


def test_new_graph_resets_incomplete_restore_safety_latch():
    document = _load_missing_plugin()
    document.clear_for_load()
    assert document.unrestored_node_count == 0
    document.add_chat_node(0, 0, "a fresh graph", True)
    saved = build_chat_data(document, plugin_registry=PluginRegistry())
    assert len(saved["nodes"]) == 1


def test_existing_registered_plugin_node_remains_saveable():
    registry = PluginRegistry()
    registry.node_kinds[UNKNOWN_KIND] = NodeKindSpec(
        plugin_id="absent_plugin",
        kind=UNKNOWN_KIND,
        factory=lambda *_: PluginNodeSeed(title="unused", content="unused"),
        requires_parent=False,
    )
    document = SceneDocument()
    restore_chat_into_document(
        document, _saved_graph(), [], [], plugin_registry=registry,
    )
    assert document.unrestored_node_count == 0
    assert any(node.kind == UNKNOWN_KIND for node in document.nodes.values())
    saved = build_chat_data(document, plugin_registry=registry)
    assert any(
        node["node_type"] == UNKNOWN_KIND and node["content"] == "important data"
        for node in saved["nodes"]
    )


def test_manual_save_refuses_to_replace_original_row(tmp_path):
    document = _load_missing_plugin()
    document.current_chat_id = 42
    db_path = tmp_path / "original-chats.db"
    bus = SessionBus("incomplete-manual-save")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    save = make_save_chat(
        bus, db_path, document, notifications,
        record_saved=lambda *_args: None,
        last_saved={},
    )
    assert asyncio.run(save()) is False
    assert notifications.msg_type == "error"
    assert "Cannot overwrite this saved graph" in notifications.message
    assert not db_path.exists(), "save must not write a new or replacement DB row"


def test_autosave_skips_incomplete_graph_without_repeated_notifications(tmp_path):
    from backend.autosave import autosave_tick

    document = _load_missing_plugin()
    document.current_chat_id = 42
    path = tmp_path / "original-chats.db"
    bus = SessionBus("incomplete-autosave")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    asyncio.run(autosave_tick(bus, path, document, notifications, last_saved={}))
    assert not path.exists()
    assert notifications.visible is False


def test_load_warns_that_unrestored_nodes_make_save_unsafe(tmp_path, monkeypatch):
    import backend.chat_library as library

    row = {
        **_saved_graph(), "workspace_id": 1, "updated_at": "2026-11-13T01:00:00Z",
    }
    monkeypatch.setattr(library, "load_chat_snapshot", lambda *_args: (row, [], []))
    monkeypatch.setattr(library, "store_for", lambda *_args: None)
    # Avoid the shared installed-plugin registry when reading a synthetic
    # historical node kind. The root itself contains no absent_plugin.
    document = SceneDocument()
    bus = SessionBus("incomplete-load-warning")
    notifications = NotificationState()
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("notification", notifications.payload)
    saved_state = {}
    loader = make_load_chat(
        bus, tmp_path / "old.db", document, notifications,
        record_saved=lambda *_args: None, last_saved=saved_state,
    )
    asyncio.run(loader(42))
    assert document.unrestored_node_count == 1
    assert document.current_chat_id == 42
    assert notifications.msg_type == "warning"
    assert "Saving is disabled" in notifications.message
    assert saved_state.get("digest") is None


def _navigation_fixture(tmp_path):
    """Two *real* SQLite rows; the source contains one unrenderable node."""
    db_path = tmp_path / "navigation.db"
    original = _saved_graph()["data"]
    # Include a missing-plugin edge; its raw source row must also survive.
    original["edges"] = [{"source": "old-chat-id", "target": "old-plugin-id", "role": "reference"}]
    original_id, _ = save_chat_atomically_row(
        db_path, None, "Recoverable source", original, [], [],
    )
    other_id, _ = save_chat_atomically_row(
        db_path, None, "Other graph",
        {"nodes": [{"id": "other-chat", "node_type": "chat",
                    "raw_content": "other graph", "position": {"x": 0, "y": 0}}]},
        [], [],
    )
    bus = SessionBus("incomplete-recovery-navigation")
    document = SceneDocument()
    notifications = NotificationState()
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("notification", notifications.payload)
    register_chat_library(bus, db_path, document, notifications, autosave_interval_seconds=None)
    return db_path, original_id, other_id, original, bus, document, notifications


def test_incomplete_graph_switch_requires_opt_in_and_preserves_source_row(tmp_path):
    db, original_id, other_id, original, bus, document, _notice = _navigation_fixture(tmp_path)
    async def scenario():
        await bus.dispatch_intent("app-chat-library", "loadChat", [original_id])
        assert document.current_chat_id == original_id
        assert document.unrestored_node_count == 1
        document.add_chat_node(100, 100, "unsaved edit to partial copy", is_user=True)

        # An ordinary switch must not overwrite the original or discard edits.
        result = await bus.dispatch_intent("app-chat-library", "loadChat", [other_id])
        assert result == "discard-required"
        assert document.current_chat_id == original_id
        assert any(n.content == "unsaved edit to partial copy" for n in document.nodes.values())
        assert load_chat_row(db, original_id)["data"] == original

        # This second request is sent only after the UI's specific discard
        # confirmation. It abandons the *live* partial copy, not its DB row.
        result = await bus.dispatch_intent("app-chat-library", "loadChat", [other_id, True])
        assert result is None
        assert document.current_chat_id == other_id
        assert document.unrestored_node_count == 0
        assert load_chat_row(db, original_id)["data"] == original
        assert any(n.content == "other graph" for n in document.nodes.values())
    asyncio.run(scenario())


def test_incomplete_graph_new_chat_and_same_row_reopen_are_explicit(tmp_path):
    db, original_id, _, original, bus, document, _notice = _navigation_fixture(tmp_path)
    async def scenario():
        await bus.dispatch_intent("app-chat-library", "loadChat", [original_id])
        document.add_chat_node(0, 0, "not in original row", is_user=True)

        # Reopen the *same* graph after the missing plugin returns: the fast
        # path must not suppress recovery when the live scene is incomplete.
        result = await bus.dispatch_intent("app-chat-library", "loadChat", [original_id])
        assert result == "discard-required"
        await bus.dispatch_intent("app-chat-library", "loadChat", [original_id, True])
        assert not any(n.content == "not in original row" for n in document.nodes.values())
        assert document.unrestored_node_count == 1
        assert load_chat_row(db, original_id)["data"] == original

        # Normal New Chat remains blocked; an explicit opt-in clears only the
        # in-memory canvas and resets the safety latch.
        assert await bus.dispatch_intent("app-chat-library", "newChat", []) == "discard-required"
        assert document.current_chat_id == original_id
        await bus.dispatch_intent("app-chat-library", "newChat", [None, True])
        assert document.current_chat_id is None
        assert document.unrestored_node_count == 0
        assert not document.nodes
        assert load_chat_row(db, original_id)["data"] == original
    asyncio.run(scenario())
