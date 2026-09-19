"""The sparse per-node wire row (backend/domain/node_wire.py).

A node sends its identity keys plus every field that differs from the
contract default, and the client restores the rest from the same defaults
(the generated hydrateSceneNodeRow). These tests pin the three things that
arrangement depends on: the backend's defaults ARE the contract's, every
contract field has a route onto the wire, and "differs from the default" is
decided strictly enough that restoring a row never changes a value.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import backend.domain.node_states as node_states
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneNode
from backend.domain.node_wire import IDENTITY_KEYS, WIRE_DEFAULTS, _NODE_FIELDS, _fields_for, wire_keys
from backend.tests.conftest import client_row
from graphlink_wire_schema import NO_DEFAULT, wire_default

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "contracts"))

from graphlink_scene_payload import SceneNodeRow  # noqa: E402

STATE_CLASSES = (
    node_states.ArtifactState,
    node_states.ChartState,
    node_states.ChatState,
    node_states.CodeReviewState,
    node_states.CodeSandboxState,
    node_states.CodeState,
    node_states.ContainerState,
    node_states.DocumentState,
    node_states.FrameState,
    node_states.GitlinkState,
    node_states.HarnessState,
    node_states.HtmlState,
    node_states.ImageState,
    node_states.NoteState,
    node_states.PlanState,
    node_states.WebResearchState,
)


def _node(state=None, **fields) -> tuple[SceneDocument, SceneNode]:
    doc = SceneDocument()
    node = SceneNode(id="n1", x=1.0, y=2.0, title="t", kind="k", state=state, **fields)
    doc.nodes[node.id] = node
    return doc, node


# -- the contract ------------------------------------------------------------


def test_the_backend_defaults_are_the_contract_defaults():
    # The client restores omitted fields from SceneNodeRow's declared
    # defaults, the backend omits by WIRE_DEFAULTS - they must be one table.
    contract = {
        field.name: default
        for field in dataclasses.fields(SceneNodeRow)
        if (default := wire_default(field)) is not NO_DEFAULT
    }
    backend = {key: value for key, value in WIRE_DEFAULTS.items() if key != "contentParts"}
    assert backend == contract
    # Equal is not enough: 0 == False and 480 == 480.0 in Python, and the
    # omission test is type-strict, so a default of the wrong type would
    # send values the client already has.
    assert {k: type(v) for k, v in backend.items()} == {k: type(v) for k, v in contract.items()}


def test_every_contract_field_has_a_route_onto_the_wire():
    contract_keys = {field.name for field in dataclasses.fields(SceneNodeRow)}
    assert wire_keys() == contract_keys | {"contentParts"}
    no_default = {f.name for f in dataclasses.fields(SceneNodeRow) if wire_default(f) is NO_DEFAULT}
    assert set(IDENTITY_KEYS) == no_default


def test_no_field_is_owned_twice():
    node_level = {entry[0] for entry in _NODE_FIELDS}
    for state_class in STATE_CLASSES:
        keys = [entry[0] for entry in _fields_for(state_class)]
        assert len(keys) == len(set(keys)), state_class.__name__
        assert not node_level & set(keys), state_class.__name__


# -- omission ------------------------------------------------------------------


@pytest.mark.parametrize("state_class", [None, *STATE_CLASSES], ids=lambda c: getattr(c, "__name__", "None"))
def test_a_node_at_its_defaults_sends_little_more_than_its_identity(state_class):
    doc, node = _node(state_class() if state_class else None)
    row = doc._node_wire(node)
    sent = set(row) - set(IDENTITY_KEYS)
    # Two state classes start away from the contract default on purpose: a
    # new Builder plan and a new harness node begin with real budgets and a
    # real status, so those fields are information, not padding.
    expected = {
        node_states.PlanState: {"builderMaxSteps", "builderMaxTokens", "builderMaxWallSeconds", "builderMode", "builderStatus"},
        node_states.HarnessState: {"harnessMaxContextTokens", "harnessMaxTurns", "harnessStatus"},
    }.get(state_class, set())
    assert sent == expected


def test_a_field_moved_off_its_default_is_sent_and_left_out_again_when_it_returns():
    doc, node = _node(node_states.ChatState())
    node.state.branch_status = "accepted"
    node.pending_request_id = "req-1"
    row = doc._node_wire(node)
    assert row["branchStatus"] == "accepted"
    assert row["pendingRequestId"] == "req-1"

    node.state.branch_status = "active"
    node.pending_request_id = None
    row = doc._node_wire(node)
    assert "branchStatus" not in row
    assert "pendingRequestId" not in row
    assert client_row(row)["branchStatus"] == "active"
    assert client_row(row)["pendingRequestId"] is None


def test_omission_is_type_strict():
    # Each of these equals its default under Python's ==, but not as JSON:
    # False is not 0, 1 is not true, 480 is not the float default. They
    # must be sent as-is, never folded into the default.
    doc, node = _node(node_states.WebResearchState())
    node.state.research_completed = False
    assert doc._node_wire(node)["researchCompleted"] is False

    doc, node = _node(node_states.ChatState())
    node.state.is_user = 0
    assert doc._node_wire(node)["isUser"] == 0

    doc, node = _node(node_states.FrameState())
    node.state.is_locked = 1
    assert doc._node_wire(node)["isLocked"] == 1


def test_content_parts_none_is_left_out_but_an_empty_list_is_sent():
    doc, node = _node(node_states.ChatState())
    assert "contentParts" not in doc._node_wire(node)
    node.state.content_parts = []
    assert doc._node_wire(node)["contentParts"] == []


def test_a_field_reset_to_its_default_still_produces_a_patch():
    # take_dirty_patch_ops diffs whole rows. A field returning to its default
    # REMOVES a key, and the upsert that carries the shorter row is what tells
    # the client (which replaces the row and restores the default) to drop
    # the old value - e.g. a node that finished running must stop looking busy.
    doc = SceneDocument()
    node = doc.add_node(0, 0, "n")
    assert doc.take_dirty_patch_ops() is None  # first publish: full snapshot
    node.pending_request_id = "req-1"
    ops = doc.take_dirty_patch_ops()
    assert ops == [{"op": "upsertNode", "node": doc._node_wire(node)}]
    assert ops[0]["node"]["pendingRequestId"] == "req-1"

    node.pending_request_id = None
    ops = doc.take_dirty_patch_ops()
    assert ops is not None and len(ops) == 1
    assert "pendingRequestId" not in ops[0]["node"]
    assert client_row(ops[0]["node"])["pendingRequestId"] is None


# -- restoring a row changes nothing -------------------------------------------


def _dense_row(doc: SceneDocument, node: SceneNode) -> dict:
    """The row with NOTHING omitted - what every node used to send."""
    row = {key: getattr(node, key) for key in IDENTITY_KEYS}
    for key, default in WIRE_DEFAULTS.items():
        row[key] = default
    for key, attr, transform, _ in _NODE_FIELDS:
        value = getattr(node, attr)
        row[key] = transform(value) if transform else value
    if node.state is not None:
        for key, attr, transform, _ in _fields_for(type(node.state)):
            value = getattr(node.state, attr)
            row[key] = transform(value) if transform else value
    row["isFinalDeliverable"] = node.id == doc.final_deliverable_node_id
    row["pluginState"] = doc._plugin_state_wire(node)
    return row


_SCALARS = st.one_of(
    st.none(), st.booleans(), st.integers(-3, 3), st.sampled_from([0.0, 480.0, 340.0, 0.5]),
    st.sampled_from(["", "active", "selected", "draft", "none", "x"]),
)


def _value_for(field: dataclasses.Field) -> st.SearchStrategy:
    default = field.default_factory() if field.default_factory is not dataclasses.MISSING else field.default
    if isinstance(default, list) or field.name == "content_parts":
        # Every list the builders read holds dicts they index by name; an
        # empty list is the case the omission logic has to get right.
        return st.just([])
    if isinstance(default, dict):
        return st.sampled_from([{}, {"k": "v"}])
    return _SCALARS


@st.composite
def _nodes(draw):
    state_class = draw(st.sampled_from([None, *STATE_CLASSES]))
    state = state_class() if state_class else None
    if state is not None:
        for field in dataclasses.fields(state):
            if draw(st.booleans()):
                setattr(state, field.name, draw(_value_for(field)))
    doc, node = _node(state)
    node.content = draw(st.sampled_from(["", "hello"]))
    node.is_collapsed = draw(st.sampled_from([False, True, 0, 1]))
    node.pending_request_id = draw(st.sampled_from([None, "", "req"]))
    node.color = draw(st.sampled_from([None, "", "#fff"]))
    node.item_ids = draw(st.sampled_from([[], ["a"]]))
    if draw(st.booleans()):
        doc.final_deliverable_node_id = node.id
    return doc, node


@given(_nodes())
def test_a_restored_row_is_exactly_the_row_that_used_to_be_sent(case):
    doc, node = case
    sparse = doc._node_wire(node)
    dense = _dense_row(doc, node)
    # Compared as JSON TEXT - what actually crosses the wire and what the
    # client holds after restoring. Comparing the dicts would be blind to
    # exactly the bug worth catching here: False == 0 in Python, but a row
    # restored as `false` where `0` was sent is a changed value.
    restored = client_row(json.loads(json.dumps(sparse)))
    expected = json.loads(json.dumps(dense))
    if dense["contentParts"] is None:
        del expected["contentParts"]
    assert json.dumps(restored, sort_keys=True) == json.dumps(expected, sort_keys=True)
