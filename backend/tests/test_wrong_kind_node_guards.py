"""Every per-kind run method rejects a node of the wrong kind.

Node states are plain, non-slotted dataclasses. Writing
`node.state.research_stage = "completed"` onto a chat node therefore does
not fail - it grafts a phantom attribute onto ChatState and returns
happily, and the caller has no way to tell. That is the failure mode these
guards exist to stop, and it is invisible unless something asserts on it,
because no live call site passes the wrong kind today.

Two groups are covered here:

  * the eight methods that gained a kind check when backend/domain/'s
    per-kind mixins were extracted and narrowed. Each had one already for
    its `add_*`/`start_*`/`set_*` siblings; the completion and failure
    paths did not, on the reasoning (written into two of the docstrings)
    that the id had been validated earlier in the same request. True, and
    still true - the guard is redundant on every live path. It is here for
    the path nobody has written yet.

  * complete_gitlink_run and complete_gitlink_apply, which gained the same
    check in PR #409 without anything pinning it.

`fail_*` methods return None for a wrong-kind node rather than raising:
they are documented as silent when their node has gone, and a node of
another kind is the same situation from a finished run's point of view.
Everything else raises SceneError.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.domain.model import SceneError
from backend.canvas import SceneDocument


def _chat_id(doc: SceneDocument) -> str:
    """A node id that is definitely not any of the kinds under test."""
    return doc.add_chat_node(0.0, 0.0, "hello", True).id


# (method name, positional args after node_id, keyword args). Spelled out
# rather than inferred because the two tables are concatenated below, and
# per-table inference gives them element types too narrow to add together.
_Call = tuple[str, tuple[Any, ...], dict[str, Any]]

RAISES: list[_Call] = [
    ("complete_web_research_run", ({"summary": "s"},), {}),
    ("fail_web_research_run", (), {"cancelled": False, "message": "boom"}),
    ("append_artifact_user_message", ("write it again",), {}),
    ("complete_artifact_generation", ("# doc", "done"), {}),
    ("complete_gitlink_run", ("## proposal", [], "", None, ""), {}),
    ("complete_gitlink_apply", (2,), {}),
]

RETURNS_NONE: list[_Call] = [
    ("apply_web_research_progress", (object(),), {}),
    ("fail_artifact_generation", ("boom",), {}),
    ("complete_code_sandbox_run", ("code", "out", "analysis"), {}),
    ("fail_code_sandbox_run", ("boom",), {}),
]


@pytest.mark.parametrize(
    "method, args, kwargs", RAISES, ids=[m for m, _, _ in RAISES],
)
def test_a_wrong_kind_node_is_rejected(method, args, kwargs):
    doc = SceneDocument()
    node_id = _chat_id(doc)
    with pytest.raises(SceneError):
        getattr(doc, method)(node_id, *args, **kwargs)


@pytest.mark.parametrize(
    "method, args, kwargs", RETURNS_NONE, ids=[m for m, _, _ in RETURNS_NONE],
)
def test_a_wrong_kind_node_is_a_quiet_no_op(method, args, kwargs):
    doc = SceneDocument()
    node_id = _chat_id(doc)
    assert getattr(doc, method)(node_id, *args, **kwargs) is None


@pytest.mark.parametrize(
    "method, args, kwargs",
    RAISES + RETURNS_NONE,
    ids=[m for m, _, _ in RAISES + RETURNS_NONE],
)
def test_the_wrong_kind_node_is_left_untouched(method, args, kwargs):
    """The point of the guard: no phantom per-kind field is grafted onto a
    state class that never declared one."""
    doc = SceneDocument()
    node_id = _chat_id(doc)
    before = dict(vars(doc.nodes[node_id].state))
    try:
        getattr(doc, method)(node_id, *args, **kwargs)
    except SceneError:
        pass
    assert vars(doc.nodes[node_id].state) == before
