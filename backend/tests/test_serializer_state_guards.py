"""A save never writes a node whose state is not the one its kind promises.

backend/session_save.py dispatches on `node.kind` and hands the node to a
serializer that reads that kind's state fields directly. Node states are plain,
non-slotted dataclasses, so a node carrying the wrong state - or none at all,
which is what a row written before its kind's state class existed looks like -
does not fail cleanly on its own. It fails partway through building the
payload, with whatever `getattr` happens to hit first.

That matters more here than anywhere else in the codebase. This is the write
side of persistence: a payload half-built from the wrong fields is a saved
session that cannot be loaded back.

`node_access.with_state` turns that into one explicit SceneError at the top of
each serializer, before any field is read. These tests pin that for every
serializer that has state to narrow - which is all of them except the thinking
and conversation kinds, whose nodes carry no state at all and whose serializers
touch only SceneNode's own fields.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.canvas import SceneNode
from backend.domain.model import SceneError
from backend.domain.node_states import ChatState
from backend import session_save


# (serializer, kind it is registered for, extra positional args after the node).
# The extras are heterogeneous and mostly empty, so the element type has to be
# spelled out rather than inferred from the rows.
SERIALIZERS: list[tuple[Any, str, tuple[Any, ...]]] = [
    (session_save._serialize_chat_node, "chat", ()),
    (session_save._serialize_code_node, "code", ()),
    (session_save._serialize_document_node, "document", ()),
    (session_save._serialize_image_node, "image", (None, None)),
    (session_save._serialize_html_node, "html", ()),
    (session_save._serialize_web_node, "web_research", ()),
    (session_save._serialize_artifact_node, "artifact", ()),
    (session_save._serialize_gitlink_node, "gitlink", ()),
    (session_save._serialize_code_review_node, "code_review", ()),
    (session_save._serialize_code_sandbox_node, "code_sandbox", ()),
    (session_save._serialize_plan_node, "plan", ()),
    (session_save._serialize_harness_node, "harness", ()),
    (session_save._serialize_note, "note", ()),
    (session_save._serialize_frame, "frame", ({},)),
    (session_save._serialize_container, "container", ({},)),
    (session_save._serialize_chart, "chart", ({}, None)),
]

IDS = [fn.__name__ for fn, _, _ in SERIALIZERS]


def _node(kind: str, state) -> SceneNode:
    return SceneNode(id="n1", x=0.0, y=0.0, title="t", kind=kind, state=state)


@pytest.mark.parametrize("serializer, kind, extra", SERIALIZERS, ids=IDS)
def test_a_node_with_no_state_is_refused_not_half_serialized(serializer, kind, extra):
    """The shape a pre-migration row takes: the right kind, no state object."""
    with pytest.raises(SceneError):
        serializer(_node(kind, None), *extra)


@pytest.mark.parametrize("serializer, kind, extra", SERIALIZERS, ids=IDS)
def test_a_node_carrying_another_kinds_state_is_refused(serializer, kind, extra):
    """ChatState stands in for "some other kind's state". It is a real state
    class with real fields, so a serializer that read from it blindly would
    get partway through rather than failing on the first access - which is
    exactly the outcome being ruled out."""
    if kind == "chat":
        pytest.skip("ChatState is this serializer's own state")
    with pytest.raises(SceneError):
        serializer(_node(kind, ChatState()), *extra)


def test_the_error_names_the_node_and_the_state_it_wanted():
    """A save that fails should say which node and what was missing - the
    thing an AttributeError on `.state.gitlink_repo` did not say."""
    with pytest.raises(SceneError) as caught:
        session_save._serialize_gitlink_node(_node("gitlink", None))
    message = str(caught.value)
    assert "n1" in message
    assert "GitlinkState" in message


def test_a_correct_node_is_returned_unchanged_and_serializes():
    """The guard is a check, not a transformation: with_state hands back the
    same object, so nothing about a valid save changes."""
    node = _node("chat", ChatState(is_user=True))
    node.content = "hello"
    payload = session_save._serialize_chat_node(node)
    assert payload["node_type"] == "chat"
    assert payload["raw_content"] == "hello"
    assert payload["is_user"] is True
