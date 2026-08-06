"""ADR-010 stage 10.1: the command layer (backend/domain/commands.py).

Two layers of test here, deliberately kept separate:

1. Command.apply()/invert() proven against SYNTHETIC before/after state -
   no SceneDocument involved, just the restore mechanism itself.
2. CommandOps.record_command() proven against a REAL SceneDocument for
   every hard case commands.py's own module doc names: id-stable delete
   recreation, remove_nodes' silent asset eviction, remove_nodes' cascade-
   delete of an emptied frame, connect()'s idempotence, and move_node's
   hidden frame group_manual_x/y side effect.
"""

import copy

import pytest

from backend.domain.commands import Command
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneEdge, SceneNode


# -- layer 1: Command.apply()/invert() against synthetic state --------------


def test_invert_restores_a_deleted_node_under_its_original_object():
    node = SceneNode(id="n1", x=1.0, y=2.0, title="t", kind="note")
    document = SceneDocument()
    command = Command(
        command_type="test",
        provenance="user",
        node_before={"n1": node},
        node_after={"n1": None},
    )
    command.invert(document)
    assert document.nodes["n1"] == node
    assert document.nodes["n1"] is not node  # deep copy, not the same object


def test_invert_removes_a_created_node():
    document = SceneDocument()
    document.nodes["n1"] = SceneNode(id="n1", x=0, y=0, title="t", kind="note")
    command = Command(
        command_type="test",
        provenance="user",
        node_before={"n1": None},
        node_after={"n1": document.nodes["n1"]},
    )
    command.invert(document)
    assert "n1" not in document.nodes


def test_apply_is_the_mirror_of_invert_for_redo():
    document = SceneDocument()
    before = SceneNode(id="n1", x=1.0, y=1.0, title="t", kind="note")
    after = SceneNode(id="n1", x=5.0, y=5.0, title="t", kind="note")
    command = Command(
        command_type="move", provenance="user",
        node_before={"n1": before}, node_after={"n1": after},
    )
    document.nodes["n1"] = copy.deepcopy(before)
    command.apply(document)
    assert document.nodes["n1"].x == 5.0
    command.invert(document)
    assert document.nodes["n1"].x == 1.0


def test_is_noop_true_for_an_empty_command():
    assert Command(command_type="test", provenance="user").is_noop


def test_is_noop_false_when_anything_changed():
    command = Command(
        command_type="test", provenance="user", node_after={"n1": None},
    )
    assert not command.is_noop


def test_invert_restores_evicted_asset_bytes():
    document = SceneDocument()
    command = Command(
        command_type="test",
        provenance="user",
        asset_before={"img1": (b"\x89PNG...", "image/png")},
        asset_after={"img1": None},
    )
    command.invert(document)
    assert document.image_assets["img1"] == (b"\x89PNG...", "image/png")


# -- layer 2: record_command() against a real SceneDocument -----------------


@pytest.fixture
def document():
    return SceneDocument()


def test_create_is_invertible_and_id_is_never_reused_by_a_later_create(document):
    _node, command = document.record_command(
        "createNote", "user", lambda: document.add_note(0, 0),
    )
    node_id = next(iter(command.node_after))
    assert node_id in document.nodes

    command.invert(document)
    assert node_id not in document.nodes

    # A later, unrelated create must not reuse the undone node's old id -
    # the counter never rewinds, so this also pins that record_command
    # itself does nothing to the counter.
    other = document.add_note(10, 10)
    assert other.id != node_id


def test_delete_restores_the_node_under_its_original_id_no_remap_needed(document):
    node = document.add_note(0, 0)
    original_id = node.id

    _result, command = document.record_command(
        "deleteNodes", "user", lambda: document.remove_nodes([original_id]),
        node_ids=[original_id],
    )
    assert original_id not in document.nodes

    command.invert(document)
    # Same id, no remapping - the whole point of snapshot/restore over
    # re-invoking add_note to recreate it.
    assert original_id in document.nodes
    assert document.nodes[original_id].id == original_id


def test_delete_restores_edges_touching_the_deleted_node(document):
    parent = document.add_note(0, 0)
    child = document.add_chat_node(50, 0, "hi", is_user=True, parent_id=parent.id)
    edge_id = next(iter(document.edges))

    _result, command = document.record_command(
        "deleteNodes", "user", lambda: document.remove_nodes([child.id]),
        node_ids=[child.id], edge_ids=[edge_id],
    )
    assert edge_id not in document.edges

    command.invert(document)
    assert edge_id in document.edges
    assert document.edges[edge_id].source == parent.id
    assert document.edges[edge_id].target == child.id


def test_delete_restores_asset_bytes_remove_nodes_would_otherwise_discard(document):
    node = document.add_image_node(0, 0, b"\x89PNG-fake", "a cat",
                                    parent_id=document.add_note(0, 0).id)
    asset_id = node.state.image_asset_id
    assert asset_id in document.image_assets

    _result, command = document.record_command(
        "deleteNodes", "user", lambda: document.remove_nodes([node.id]),
        node_ids=[node.id],
    )
    assert asset_id not in document.image_assets  # remove_nodes evicted it for real

    command.invert(document)
    assert asset_id in document.image_assets
    assert document.image_assets[asset_id] == (b"\x89PNG-fake", "image/png")


def test_delete_that_empties_a_frame_restores_the_cascade_deleted_frame_too(document):
    member = document.add_chat_node(0, 0, "hi", is_user=True)
    frame = document.create_frame([member.id])
    frame_id = frame.id
    assert frame_id in document.nodes

    _result, command = document.record_command(
        "deleteNodes", "user", lambda: document.remove_nodes([member.id]),
        node_ids=[member.id],
    )
    # The frame was the member's only member - remove_nodes' own
    # _detach_node_from_membership cascade-deletes it, unrequested.
    assert frame_id not in document.nodes

    command.invert(document)
    # Both the explicit target AND the cascade-deleted frame come back -
    # this is the case a naive "snapshot only the caller's explicit ids"
    # design would have silently lost.
    assert member.id in document.nodes
    assert frame_id in document.nodes
    assert document.nodes[frame_id].item_ids == [member.id]


def test_delete_that_shrinks_a_frame_without_emptying_it_restores_membership(document):
    a = document.add_chat_node(0, 0, "a", is_user=True)
    b = document.add_chat_node(100, 0, "b", is_user=True)
    frame = document.create_frame([a.id, b.id])
    frame_id = frame.id

    _result, command = document.record_command(
        "deleteNodes", "user", lambda: document.remove_nodes([a.id]),
        node_ids=[a.id],
    )
    assert document.nodes[frame_id].item_ids == [b.id]

    command.invert(document)
    assert a.id in document.nodes
    # Frame survived the forward op (still had b.id); its item_ids must be
    # restored to include a.id again, not left at the shrunk [b.id].
    assert set(document.nodes[frame_id].item_ids) == {a.id, b.id}


def test_move_restores_position_and_the_hidden_frame_pinning_side_effect(document):
    member = document.add_chat_node(0, 0, "hi", is_user=True)
    frame = document.create_frame([member.id])
    frame_id = frame.id
    was_auto_fit = document.nodes[frame_id].state.group_manual_x is None
    assert was_auto_fit  # precondition: frame starts in auto-fit mode
    # create_frame computes the frame's initial position from a padded bbox
    # of its members, not (0, 0) - capture the REAL starting position rather
    # than assume one.
    original_x, original_y = document.nodes[frame_id].x, document.nodes[frame_id].y

    _result, command = document.record_command(
        "moveNode", "user", lambda: document.move_node(frame_id, 500.0, 500.0),
        node_ids=[frame_id],
    )
    # move_node's own documented side effect: moving a frame pins it out of
    # auto-fit, regardless of whether it was auto-fit before.
    assert document.nodes[frame_id].state.group_manual_x is not None

    command.invert(document)
    # A naive invert that only restored x/y (not the whole node) would
    # leave group_manual_x/y set here, silently converting an auto-fit
    # frame into a manually-pinned one - this pins that snapshot/restore
    # of the WHOLE object avoids that.
    assert document.nodes[frame_id].state.group_manual_x is None
    assert document.nodes[frame_id].x == original_x
    assert document.nodes[frame_id].y == original_y


def test_connect_is_invertible(document):
    a = document.add_note(0, 0)
    b = document.add_note(100, 0)

    _edge, command = document.record_command(
        "connectNodes", "user", lambda: document.connect(a.id, b.id),
        node_ids=[a.id, b.id],
    )
    edge_id = next(iter(command.edge_after))
    assert edge_id in document.edges

    command.invert(document)
    assert edge_id not in document.edges


def test_idempotent_connect_produces_a_noop_command(document):
    a = document.add_note(0, 0)
    b = document.add_note(100, 0)
    document.connect(a.id, b.id)
    existing_edge_id = next(iter(document.edges))

    _edge, command = document.record_command(
        "connectNodes", "user", lambda: document.connect(a.id, b.id),
        node_ids=[a.id, b.id],
    )
    # connect() returned the pre-existing edge - nothing was created, so
    # inverting must be a true no-op, not "delete the edge it returned."
    assert command.is_noop

    command.invert(document)
    assert existing_edge_id in document.edges


def test_remove_edges_is_invertible_under_the_original_edge_id(document):
    a = document.add_note(0, 0)
    b = document.add_note(100, 0)
    edge = document.connect(a.id, b.id)

    _result, command = document.record_command(
        "removeEdges", "user", lambda: document.remove_edges([edge.id]),
        edge_ids=[edge.id],
    )
    assert edge.id not in document.edges

    command.invert(document)
    assert edge.id in document.edges
    assert document.edges[edge.id] == edge


def test_unanticipated_deletion_raises_instead_of_silently_losing_it(document):
    node = document.add_note(0, 0)
    with pytest.raises(AssertionError, match="no defensive snapshot"):
        # node_ids deliberately omitted - record_command has no way to know
        # this node is about to be deleted, and must fail loud rather than
        # produce a Command whose invert() can't restore it.
        document.record_command(
            "deleteNodes", "user", lambda: document.remove_nodes([node.id]),
        )


# -- layer 3: end-to-end through the REAL wired intent surface --------------
#
# Layers 1-2 above prove the mechanism in isolation. These prove the actual
# migration landed: that dispatching a real WS intent through the real
# register_canvas wiring produces a logged, correctly-inverting command. A
# call site that was MISSED by the migration fails here and only here - the
# domain-level tests above would still pass, since they call record_command
# directly rather than going through the intent.


@pytest.fixture
def wired():
    """The real bus + document + intent registrations, same construction
    backend/app.py itself uses."""
    from backend.tests.test_canvas import make_bus_with_dispatcher

    bus, document, _recorder, _dispatcher = make_bus_with_dispatcher()
    return bus, document


def _dispatch(bus, intent, *args, topic="scene"):
    import asyncio

    return asyncio.run(bus.dispatch_intent(topic, intent, list(args)))


def test_addnode_intent_logs_an_invertible_command(wired):
    bus, document = wired
    node_id = _dispatch(bus, "addNode", 10, 20, "hello")

    assert len(document.command_log) == 1
    command = document.command_log[-1]
    assert command.command_type == "addNode"
    assert command.provenance == "user"

    command.invert(document)
    assert node_id not in document.nodes


def test_removenodes_intent_logs_a_command_that_restores_the_node(wired):
    bus, document = wired
    node_id = _dispatch(bus, "addNode", 10, 20, "hello")
    document.command_log.clear()

    _dispatch(bus, "removeNodes", [node_id])
    assert node_id not in document.nodes

    assert len(document.command_log) == 1
    assert document.command_log[-1].command_type == "removeNodes"
    document.command_log[-1].invert(document)
    assert node_id in document.nodes
    assert document.nodes[node_id].title == "hello"


def test_movenode_intent_logs_a_command_that_restores_the_position(wired):
    bus, document = wired
    node_id = _dispatch(bus, "addNode", 10, 20, "hello")
    document.command_log.clear()

    _dispatch(bus, "moveNode", node_id, 500, 600)
    assert document.nodes[node_id].x == 500

    document.command_log[-1].invert(document)
    assert document.nodes[node_id].x == 10
    assert document.nodes[node_id].y == 20


def test_movenodes_group_drag_is_ONE_command_not_one_per_node(wired):
    bus, document = wired
    a = _dispatch(bus, "addNode", 0, 0, "a")
    b = _dispatch(bus, "addNode", 100, 0, "b")
    document.command_log.clear()

    _dispatch(bus, "moveNodes", [[a, 50, 50], [b, 150, 50]])
    # One Ctrl+Z must put the whole group drag back, so the intent must
    # produce exactly one command regardless of how many nodes moved.
    assert len(document.command_log) == 1

    document.command_log[-1].invert(document)
    assert (document.nodes[a].x, document.nodes[a].y) == (0, 0)
    assert (document.nodes[b].x, document.nodes[b].y) == (100, 0)


def test_connectnodes_intent_is_invertible_and_idempotent_repeat_logs_nothing(wired):
    bus, document = wired
    a = _dispatch(bus, "addNode", 0, 0, "a")
    b = _dispatch(bus, "addNode", 100, 0, "b")
    document.command_log.clear()

    edge_id = _dispatch(bus, "connectNodes", a, b)
    assert len(document.command_log) == 1

    # Re-connecting the same pair creates nothing (connect is idempotent),
    # so it must not add a command that would delete the existing edge.
    _dispatch(bus, "connectNodes", a, b)
    assert len(document.command_log) == 1

    document.command_log[-1].invert(document)
    assert edge_id not in document.edges


def test_removeedges_intent_is_invertible(wired):
    bus, document = wired
    a = _dispatch(bus, "addNode", 0, 0, "a")
    b = _dispatch(bus, "addNode", 100, 0, "b")
    edge_id = _dispatch(bus, "connectNodes", a, b)
    document.command_log.clear()

    _dispatch(bus, "removeEdges", [edge_id])
    assert edge_id not in document.edges

    document.command_log[-1].invert(document)
    assert edge_id in document.edges


def test_addnote_and_createframe_intents_are_invertible(wired):
    bus, document = wired
    member = _dispatch(bus, "addChatNode", 0, 0, "hi", True, None)
    document.command_log.clear()

    frame_id = _dispatch(bus, "createFrame", [member])
    assert frame_id in document.nodes

    document.command_log[-1].invert(document)
    assert frame_id not in document.nodes


def test_the_command_log_is_bounded_and_drops_oldest_not_newest(wired):
    bus, document = wired
    for i in range(120):
        _dispatch(bus, "addNode", i, i, f"n{i}")

    # maxlen=100 per the ADR's own "bounded, e.g. 100" durability boundary.
    assert len(document.command_log) == 100
    # The newest survive; the oldest are the ones dropped.
    assert document.command_log[-1].command_type == "addNode"


def test_loading_a_session_clears_undo_history(wired):
    bus, document = wired
    _dispatch(bus, "addNode", 0, 0, "from the old session")
    assert len(document.command_log) > 0

    document.clear_for_load()
    # Inverting a command from the PREVIOUS chat would resurrect one of its
    # nodes into this one - the history must not survive the boundary.
    assert len(document.command_log) == 0
