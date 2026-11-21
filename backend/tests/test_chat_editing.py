"""Sent user chat edits: saved alternatives, sibling branches and single-command undo."""

from __future__ import annotations

import asyncio

import pytest

import backend.agents as agents_module  # noqa: F401 - backend.canvas import order
from backend.api.intents_chat import register_chat_edit_intents
from backend.canvas import SceneDocument
from backend.domain.model import SceneError
from backend.domain.node_states import MAX_CHAT_VERSIONS


def _thread():
    document = SceneDocument()
    parent = document.add_chat_node(0, 0, "parent", False)
    original = document.add_chat_node(0, 250, "old user text", True, parent_id=parent.id)
    reply = document.add_chat_node(0, 500, "existing reply", False, parent_id=original.id)
    return document, parent, original, reply


def test_edit_in_place_creates_selected_version_and_one_reversible_command():
    document, parent, user, reply = _thread()
    original_edges = list(document.edges)
    changed, command = document.record_command(
        "editChatMessage", "user",
        lambda: document.edit_chat_message(user.id, "new user text", "old user text"),
        node_ids=[user.id],
    )
    assert changed.id == user.id
    assert changed.content == "new user text"
    assert [v.content for v in changed.state.versions] == ["old user text", "new user text"]
    assert changed.state.version_index == 1
    assert document.chat_branch_history(reply.id)[-2]["content"] == "new user text"
    assert document.nodes[reply.id].content == "existing reply"
    assert list(document.edges) == original_edges
    assert document._branch_parent_edge(user.id).source == parent.id

    command.invert(document)
    restored = document.nodes[user.id]
    assert restored.content == "old user text"
    assert [v.content for v in restored.state.versions] == ["old user text"]
    assert document.nodes[reply.id].content == "existing reply"
    command.apply(document)
    assert document.nodes[user.id].content == "new user text"
    assert document.nodes[user.id].state.version_index == 1
    document.set_chat_version(user.id, 0)
    assert document.nodes[user.id].content == "old user text"


def test_repeated_edits_keep_the_existing_twenty_version_cap():
    document, _, user, _ = _thread()
    expected = user.content
    for i in range(25):
        changed = document.edit_chat_message(user.id, f"edit {i}", expected)
        expected = changed.content
    assert len(changed.state.versions) == MAX_CHAT_VERSIONS == 20
    assert changed.state.versions[0].content == "edit 5"
    assert changed.state.versions[-1].content == "edit 24"
    assert changed.state.version_index == 19
    # No-op edits do not manufacture an extra version.
    assert document.edit_chat_message(user.id, expected, expected).state.versions == changed.state.versions


def test_edit_as_new_branch_preserves_original_subtree_and_one_undo_step():
    document, parent, user, reply = _thread()
    prior_edges = set(document.edges)
    last_tip = document.last_chat_node_id
    branch, command = document.record_command(
        "editChatMessageAsNewBranch", "user",
        lambda: document.branch_edited_chat_message(user.id, "alternate message", "old user text"),
        node_ids=[user.id],
    )
    assert branch.id != user.id
    assert branch.state.is_user is True
    assert branch.content == "alternate message"
    assert len(branch.state.versions) == 1
    assert document._branch_parent_edge(branch.id).source == parent.id
    assert document._branch_parent_edge(reply.id).source == user.id
    assert document.chat_branch_history(branch.id)[-1]["content"] == "alternate message"
    assert document.nodes[user.id].content == "old user text"
    assert [v.content for v in document.nodes[user.id].state.versions] == ["old user text"]
    assert document.nodes[reply.id].content == "existing reply"
    assert prior_edges < set(document.edges)
    assert document.last_chat_node_id == last_tip

    command.invert(document)
    assert branch.id not in document.nodes
    assert set(document.edges) == prior_edges
    assert user.id in document.nodes and reply.id in document.nodes
    command.apply(document)
    assert branch.id in document.nodes
    assert document._branch_parent_edge(branch.id).source == parent.id


def test_edit_as_new_branch_of_root_creates_independent_root():
    document = SceneDocument()
    root = document.add_chat_node(0, 0, "root user", True)
    sibling = document.branch_edited_chat_message(root.id, "new root", "root user")
    assert document._branch_parent_edge(sibling.id) is None
    assert document._branch_parent_edge(root.id) is None
    assert root.content == "root user"


@pytest.mark.parametrize("as_branch", [False, True])
def test_edits_reject_assistants_empty_drafts_and_stale_writes(as_branch):
    document, _, user, reply = _thread()
    write = document.branch_edited_chat_message if as_branch else document.edit_chat_message
    before_nodes = set(document.nodes)
    before_edges = set(document.edges)
    with pytest.raises(SceneError, match="Only user"):
        write(reply.id, "rewritten", "existing reply")
    with pytest.raises(SceneError, match="nonempty"):
        write(user.id, "   ", "old user text")
    with pytest.raises(SceneError, match="changed"):
        write(user.id, "rewrite", "stale base")
    user.pending_request_id = "request-running"
    with pytest.raises(SceneError, match="running"):
        write(user.id, "rewrite", "old user text")
    assert set(document.nodes) == before_nodes
    assert set(document.edges) == before_edges
    assert user.content == "old user text"


class _Bus:
    def __init__(self):
        self.intents = {}

    def register_intent(self, topic, name, handler):
        self.intents[(topic, name)] = handler


def test_registered_intents_publish_only_scene_and_each_record_one_command():
    document, parent, user, reply = _thread()
    bus = _Bus()
    publishes = []

    async def publish_scene():
        publishes.append(document.scene_payload())

    register_chat_edit_intents(bus, document, publish_scene)
    assert set(bus.intents) == {
        ("scene", "editChatMessage"),
        ("scene", "editChatMessageAsNewBranch"),
    }
    asyncio.run(bus.intents[("scene", "editChatMessage")](
        user.id, "revised", "old user text",
    ))
    assert document.nodes[user.id].state.version_index == 1
    new_id = asyncio.run(bus.intents[("scene", "editChatMessageAsNewBranch")](
        user.id, "forked", "revised",
    ))
    assert document._branch_parent_edge(new_id).source == parent.id
    assert document._branch_parent_edge(reply.id).source == user.id
    assert len(publishes) == 2
    # No agent dispatcher is supplied to either registration/intent.
