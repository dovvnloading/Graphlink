"""System Prompt plugin: graph semantics, idempotence and undo regression tests.

Run through the real registered picker action (not a test-only replica) so
the built-in contract, scene publications and undo stack are exercised.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import PluginRunContext, discover_plugins
from backend.plugins import register_plugins
from graphlink_settings_store import SettingsManager


@pytest.fixture
def prompt_workspace(tmp_path):
    bus = SessionBus("system-prompt-refactor-test")
    document = SceneDocument()
    notifications = NotificationState()
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("notification", notifications.payload)
    register_plugins(
        bus, notifications, document, SettingsManager(tmp_path / "session.dat"),
    )
    return bus, document, notifications


def _pick(bus, parent=None, x=None, y=None):
    return asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["System Prompt", parent, x, y])
    )


def _links(document):
    return {(edge.source, edge.target) for edge in document.edges.values()}


def test_selecting_an_existing_pending_prompt_reuses_it_without_a_new_command(prompt_workspace):
    bus, document, _ = prompt_workspace
    note_id = _pick(bus, None, 24.0, -80.0)
    before = len(document.command_log)
    assert _pick(bus, note_id, 999.0, 999.0) == note_id
    assert len(document.command_log) == before
    assert len(document.nodes) == 1
    assert not document.edges


def test_selecting_an_attached_prompt_does_not_create_prompt_of_prompt(prompt_workspace):
    bus, document, _ = prompt_workspace
    root = document.add_chat_node(0.0, 0.0, "hello", is_user=True)
    prompt = _pick(bus, root.id)
    assert (prompt, root.id) in _links(document)

    previous_commands = len(document.command_log)
    assert _pick(bus, prompt) == prompt
    assert len(document.command_log) == previous_commands
    assert (prompt, root.id) in _links(document)
    assert sum(1 for node in document.nodes.values() if node.kind == "note") == 1


def test_pending_prompt_is_attached_to_existing_branch_preserving_content_and_undo(prompt_workspace):
    bus, document, _ = prompt_workspace
    # Start with an established branch, then author a prompt independently.
    # This is the explicit-attachment path (a new root would adopt it
    # automatically on creation instead).
    root = document.add_chat_node(100.0, 200.0, "Hi", is_user=True)
    pending_id = _pick(bus, None, 15.0, 25.0)
    document.set_note_content(pending_id, "Use terse responses.")
    assert not _links(document)
    before = len(document.command_log)

    assert _pick(bus, root.id) == pending_id
    assert len(document.command_log) == before + 1
    assert document.command_log[-1].command_type == "pluginSystemPrompt"
    assert (pending_id, root.id) in _links(document)
    assert document.nodes[pending_id].content == "Use terse responses."
    assert (document.nodes[pending_id].x, document.nodes[pending_id].y) == (15.0, 25.0)

    document.undo()
    assert pending_id in document.nodes
    assert document.nodes[pending_id].content == "Use terse responses."
    assert (pending_id, root.id) not in _links(document)

    document.redo()
    assert (pending_id, root.id) in _links(document)


def test_reuses_attached_prompt_even_if_another_pending_prompt_exists(prompt_workspace):
    bus, document, _ = prompt_workspace
    root = document.add_chat_node(0.0, 0.0, "first", is_user=True)
    attached_id = _pick(bus, root.id)
    pending_id = _pick(bus, None, 10.0, 20.0)
    before = len(document.command_log)

    assert pending_id != attached_id
    assert _pick(bus, root.id) == attached_id
    assert len(document.command_log) == before
    assert (attached_id, root.id) in _links(document)
    assert document.unattached_system_prompt_notes()[0].id == pending_id


def test_standalone_ordinary_note_cannot_be_the_target_of_an_ineffective_prompt(prompt_workspace):
    bus, document, _ = prompt_workspace
    ordinary = document.add_note(0.0, 0.0)
    prompt_id = _pick(bus, ordinary.id, 30.0, 40.0)

    assert prompt_id != ordinary.id
    assert prompt_id in document.nodes
    assert document.nodes[prompt_id].state.is_system_prompt
    assert (document.nodes[prompt_id].x, document.nodes[prompt_id].y) == (30.0, 40.0)
    assert (prompt_id, ordinary.id) not in _links(document)
    assert [p.id for p in document.unattached_system_prompt_notes()] == [prompt_id]


def test_prompt_with_incoming_edge_is_not_considered_pending_or_auto_adopted(prompt_workspace):
    bus, document, _ = prompt_workspace
    root = document.add_chat_node(0.0, 0.0, "root", is_user=True)
    linked_note = document.add_note(0.0, 0.0, is_system_prompt=True)
    document.connect(root.id, linked_note.id)

    assert document.unattached_system_prompt_notes() == []
    new_root = document.add_chat_node(100.0, 0.0, "other root", is_user=True)
    assert (linked_note.id, new_root.id) not in _links(document)

    # Explicitly asking for a prompt on root must not select the linked
    # note and attempt to create a cycle back to its existing parent.
    created = _pick(bus, root.id)
    assert created not in (None, linked_note.id)
    assert (created, root.id) in _links(document)


def test_ambiguous_pending_notes_fail_without_guessing_or_mutating(prompt_workspace):
    bus, document, notifications = prompt_workspace
    document.add_note(1, 1, is_system_prompt=True)
    document.add_note(2, 2, is_system_prompt=True)
    root = document.add_node(0, 0, "root")

    assert _pick(bus, root.id) is None
    assert notifications.visible
    assert notifications.msg_type == "warning"
    assert "Multiple unattached" in notifications.message
    assert len(document.nodes) == 3
    assert not document.edges
    assert not document.command_log


@pytest.mark.parametrize("x,y", [
    (float("nan"), float("inf")),
    (-float("inf"), float("nan")),
])
def test_nonfinite_spawn_coordinates_are_normalized_to_origin(x, y):
    registry = discover_plugins()
    action = registry.resolve_builtin_action("System Prompt")
    assert action is not None
    document = SceneDocument()
    context = PluginRunContext(
        plugin_id="system_prompt", notifications=NotificationState(),
        spawn_x=x, spawn_y=y,
    )
    note_id = action.handler(document, context, None)
    note = document.nodes[note_id]
    assert math.isfinite(note.x) and math.isfinite(note.y)
    assert (note.x, note.y) == (0.0, 0.0)
