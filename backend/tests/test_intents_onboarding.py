"""ADR-012 stage 12.6: the bundled sample workspace's one intent
(backend/api/intents_onboarding.py) - loadSampleWorkspace.

Mirrors test_intents_knowledge.py's own shape: a dedicated test file per
intents_*.py module, built on test_canvas.py's shared make_bus() (the
full register_canvas surface), rather than folding into test_canvas.py
directly."""

from __future__ import annotations

import asyncio

from backend.api.intents_onboarding import (
    SAMPLE_ASSISTANT_MESSAGE,
    SAMPLE_NOTE_CONTENT,
    SAMPLE_USER_MESSAGE,
)
from backend.tests.test_canvas import make_bus


def _run(coro):
    return asyncio.run(coro)


def test_load_sample_workspace_creates_the_expected_fixture():
    bus, document, recorder = make_bus()

    _run(bus.dispatch_intent("scene", "loadSampleWorkspace", []))

    assert len(document.nodes) == 3
    kinds = sorted(node.kind for node in document.nodes.values())
    assert kinds == ["chat", "chat", "note"]

    note = next(n for n in document.nodes.values() if n.kind == "note")
    assert note.content == SAMPLE_NOTE_CONTENT

    user_chat = next(n for n in document.nodes.values() if n.kind == "chat" and n.state.is_user)
    assistant_chat = next(n for n in document.nodes.values() if n.kind == "chat" and not n.state.is_user)
    assert user_chat.content == SAMPLE_USER_MESSAGE
    assert assistant_chat.content == SAMPLE_ASSISTANT_MESSAGE

    # The assistant reply is a real branch continuation of the user message,
    # not a free-floating node - one edge, connecting exactly those two.
    assert len(document.edges) == 1
    edge = next(iter(document.edges.values()))
    assert (edge.source, edge.target) == (user_chat.id, assistant_chat.id)


def test_load_sample_workspace_is_deterministic_and_repeatable():
    """No LLM call, nothing random - the exit criterion ("E2E uses the
    sample fixture") needs this to produce the SAME 3 nodes/kinds every
    time, not just once."""
    bus, document, recorder = make_bus()

    _run(bus.dispatch_intent("scene", "loadSampleWorkspace", []))
    first_kinds = sorted(node.kind for node in document.nodes.values())
    first_contents = sorted(node.content for node in document.nodes.values())

    bus2, document2, _recorder2 = make_bus()
    _run(bus2.dispatch_intent("scene", "loadSampleWorkspace", []))
    second_kinds = sorted(node.kind for node in document2.nodes.values())
    second_contents = sorted(node.content for node in document2.nodes.values())

    assert first_kinds == second_kinds
    assert first_contents == second_contents


def test_load_sample_workspace_publishes_the_scene_topic():
    bus, document, recorder = make_bus()
    recorder.messages.clear()

    _run(bus.dispatch_intent("scene", "loadSampleWorkspace", []))

    assert recorder.topics_seen().count("scene") == 1


def test_load_sample_workspace_is_undoable_as_a_single_composite():
    """ADR-010 stage 10.3: the 3-node create + the note's content-set are one
    composite - one Ctrl+Z removes the whole fixture, not one node at a
    time."""
    bus, document, recorder = make_bus()

    _run(bus.dispatch_intent("scene", "loadSampleWorkspace", []))
    assert len(document.nodes) == 3
    assert document.can_undo()

    document.undo()
    assert len(document.nodes) == 0


def test_load_sample_workspace_appends_to_an_already_populated_scene():
    """Not a clear-then-populate: calling it on a scene that already has
    content just adds the fixture's 3 nodes alongside whatever was there,
    matching every other addXNode intent's own behavior (no implicit
    scene-wide reset anywhere else in this app)."""
    bus, document, recorder = make_bus()
    _run(bus.dispatch_intent("scene", "addNode", [0, 0, "existing"]))
    assert len(document.nodes) == 1

    _run(bus.dispatch_intent("scene", "loadSampleWorkspace", []))

    assert len(document.nodes) == 4
