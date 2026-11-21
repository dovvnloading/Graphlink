"""Chat message alternatives: bounded history, selected-projection, and v1 migration."""

from __future__ import annotations

from datetime import datetime

import pytest

import backend.agents as agents_module  # noqa: F401 - backend.canvas import order
from backend.canvas import SceneDocument
from backend.domain.model import SceneError
from backend.domain.node_states import MAX_CHAT_VERSIONS
from backend.session_load import _restore_chat_payload, restore_chat_into_document
from backend.session_save import build_chat_data


@pytest.mark.parametrize("is_user", [True, False])
def test_new_chat_messages_start_with_one_timestamped_version(is_user):
    document = SceneDocument()
    node = document.add_chat_node(0, 0, "first", is_user)
    assert [version.content for version in node.state.versions] == ["first"]
    assert node.state.version_index == 0
    assert datetime.fromisoformat(node.state.versions[0].created_at).tzinfo is not None
    if is_user:
        assert node.state.versions[0].provider_name is None
        assert node.state.versions[0].model_name is None


def test_append_preserves_previous_reply_and_selected_provider_usage_and_tools():
    document = SceneDocument()
    node = document.add_chat_node(0, 0, "answer A", False)
    node.state.provider = "provider A"
    node.state.model = "model A"
    node.state.prompt_tokens = 14
    node.state.completion_tokens = 8
    node.state.estimated_cost_usd = 0.02
    node.state.tool_invocations = [{"name": "fetch", "result": "A"}]
    document.sync_chat_version(node.id)

    document.update_chat_node_content(node.id, "answer B")
    assert [v.content for v in node.state.versions] == ["answer A", "answer B"]
    assert node.state.version_index == 1
    assert node.state.prompt_tokens is None
    assert node.state.provider is None
    node.state.provider = "provider B"
    node.state.model = "model B"
    node.state.response_incomplete = True
    document.sync_chat_version(node.id)

    document.set_chat_version(node.id, 0)
    assert node.content == "answer A"
    assert node.state.provider == "provider A"
    assert node.state.model == "model A"
    assert (node.state.prompt_tokens, node.state.completion_tokens) == (14, 8)
    assert node.state.estimated_cost_usd == 0.02
    assert node.state.tool_invocations == [{"name": "fetch", "result": "A"}]
    assert node.state.response_incomplete is False
    assert document.chat_branch_history(node.id)[-1]["content"] == "answer A"

    document.set_chat_version(node.id, 1)
    assert node.content == "answer B"
    assert node.state.provider == "provider B"
    assert node.state.model == "model B"
    assert node.state.response_incomplete is True


def test_20_version_cap_drops_oldest_and_regeneration_after_switch_appends():
    document = SceneDocument()
    node = document.add_chat_node(0, 0, "original", True)
    for i in range(25):
        document.update_chat_node_content(node.id, f"edit {i}")
    assert len(node.state.versions) == MAX_CHAT_VERSIONS == 20
    assert node.state.version_index == 19
    assert node.state.versions[0].content == "edit 5"
    assert node.state.versions[-1].content == "edit 24"
    document.set_chat_version(node.id, 0)
    document.update_chat_node_content(node.id, "edit after switch")
    assert len(node.state.versions) == 20
    assert node.state.versions[0].content == "edit 6"
    assert node.state.versions[-1].content == "edit after switch"
    assert node.state.version_index == 19


def test_switch_is_a_single_undoable_command_with_redo():
    document = SceneDocument()
    node = document.add_chat_node(0, 0, "first", False)
    document.update_chat_node_content(node.id, "second")
    _, command = document.record_command(
        "setChatVersion", "user",
        lambda: document.set_chat_version(node.id, 0), node_ids=[node.id],
    )
    assert document.nodes[node.id].content == "first"
    command.invert(document)
    assert document.nodes[node.id].content == "second"
    assert document.nodes[node.id].state.version_index == 1
    command.apply(document)
    assert document.nodes[node.id].content == "first"
    assert document.nodes[node.id].state.version_index == 0


def test_switch_validates_indices_and_refuses_inflight_regeneration():
    document = SceneDocument()
    node = document.add_chat_node(0, 0, "answer", False)
    with pytest.raises(SceneError, match="index"):
        document.set_chat_version(node.id, True)
    with pytest.raises(SceneError, match="index"):
        document.set_chat_version(node.id, -1)
    with pytest.raises(SceneError, match="index"):
        document.set_chat_version(node.id, 1)
    node.pending_request_id = "generating"
    with pytest.raises(SceneError, match="generating"):
        document.set_chat_version(node.id, 0)


def test_v1_saved_graph_migrates_to_a_single_current_version():
    node = _restore_chat_payload({
        "node_type": "chat", "raw_content": "original answer", "is_user": False,
        "provider": "old provider", "model": "old model",
        "response_incomplete": True, "prompt_tokens": 4,
        "position": {"x": 0, "y": 0},
    })
    assert node.content == "original answer"
    assert node.state.version_index == 0
    assert len(node.state.versions) == 1
    version = node.state.versions[0]
    assert (version.content, version.provider_name, version.model_name) == (
        "original answer", "old provider", "old model",
    )
    assert version.created_at == "", "old saves did not record an original creation time"
    assert node.state.response_incomplete is True
    assert node.state.prompt_tokens == 4


def test_v2_save_and_restore_selected_version_and_provenance():
    document = SceneDocument()
    node = document.add_chat_node(0, 0, "first reply", False)
    node.state.provider, node.state.model = "Provider 1", "Model 1"
    document.sync_chat_version(node.id)
    document.update_chat_node_content(node.id, "second reply")
    node.state.provider, node.state.model = "Provider 2", "Model 2"
    document.sync_chat_version(node.id)
    document.set_chat_version(node.id, 0)

    saved = build_chat_data(document)
    assert saved["schema_version"] == 2
    serialized = next(row for row in saved["nodes"] if row["node_type"] == "chat")
    assert serialized["raw_content"] == "first reply"
    assert serialized["version_index"] == 0
    assert [(v["content"], v["provider"], v["model"]) for v in serialized["versions"]] == [
        ("first reply", "Provider 1", "Model 1"),
        ("second reply", "Provider 2", "Model 2"),
    ]
    restored = SceneDocument()
    notes, pins = saved.pop("notes_data"), saved.pop("pins_data")
    restore_chat_into_document(restored, {"data": saved}, notes, pins)
    message = next(node for node in restored.nodes.values() if node.kind == "chat")
    assert message.content == "first reply"
    assert message.state.version_index == 0
    assert len(message.state.versions) == 2
    restored.set_chat_version(message.id, 1)
    assert message.content == "second reply"
    assert (message.state.provider, message.state.model) == ("Provider 2", "Model 2")


def test_malformed_saved_versions_fall_back_to_existing_text():
    node = _restore_chat_payload({
        "node_type": "chat", "raw_content": "still here",
        "versions": [{"unexpected": "not a version"}],
        "position": {"x": 0, "y": 0},
    })
    assert node.content == "still here"
    assert len(node.state.versions) == 1
