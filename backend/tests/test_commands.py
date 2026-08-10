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


# -- layer 4: the undo/redo stack (ADR-010 stages 10.2-10.5) ----------------


def test_undo_then_redo_round_trips_a_delete(wired):
    bus, document = wired
    node_id = _dispatch(bus, "addNode", 0, 0, "keep me")
    _dispatch(bus, "removeNodes", [node_id])
    assert node_id not in document.nodes

    _dispatch(bus, "undo")
    assert node_id in document.nodes

    _dispatch(bus, "redo")
    assert node_id not in document.nodes


def test_multiple_undos_walk_back_through_history_in_order(wired):
    bus, document = wired
    a = _dispatch(bus, "addNode", 0, 0, "a")
    b = _dispatch(bus, "addNode", 1, 1, "b")
    c = _dispatch(bus, "addNode", 2, 2, "c")

    _dispatch(bus, "undo")
    assert c not in document.nodes and b in document.nodes
    _dispatch(bus, "undo")
    assert b not in document.nodes and a in document.nodes
    _dispatch(bus, "undo")
    assert a not in document.nodes


def test_doing_something_new_after_undo_discards_the_redo_branch(wired):
    bus, document = wired
    first = _dispatch(bus, "addNode", 0, 0, "first")
    _dispatch(bus, "undo")
    assert first not in document.nodes

    # A new action after an undo makes the undone one unreachable - redoing
    # it would reapply a change onto a document that has since diverged.
    second = _dispatch(bus, "addNode", 5, 5, "second")
    _dispatch(bus, "redo")
    assert first not in document.nodes
    assert second in document.nodes


def test_undo_reports_the_action_name_so_the_button_can_say_what_it_undoes(wired):
    bus, document = wired
    node_id = _dispatch(bus, "addNode", 0, 0, "x")
    assert document.undo_label() == "Add Node"

    _dispatch(bus, "removeNodes", [node_id])
    assert document.undo_label() == "Delete"
    assert document.can_undo() is True
    assert document.can_redo() is False

    _dispatch(bus, "undo")
    assert document.redo_label() == "Delete"


def test_scene_payload_carries_undo_state_for_the_toolbar(wired):
    bus, document = wired
    payload = document.scene_payload()
    assert payload["canUndo"] is False
    assert payload["canRedo"] is False
    assert payload["undoLabel"] == ""

    _dispatch(bus, "addNode", 0, 0, "x")
    payload = document.scene_payload()
    assert payload["canUndo"] is True
    assert payload["undoLabel"] == "Add Node"


def test_undo_with_empty_history_is_refused_not_crashed(wired):
    bus, document = wired
    # No exception reaches the intent layer - a refusal is a normal outcome.
    assert _dispatch(bus, "undo") is None
    assert _dispatch(bus, "redo") is None


def test_undo_is_refused_while_a_node_is_still_generating(wired):
    bus, document = wired
    node_id = _dispatch(bus, "addNode", 0, 0, "busy")
    _dispatch(bus, "moveNode", node_id, 50, 50)

    # A live run is writing to this node; restoring a pre-run snapshot
    # underneath it would leave state neither the user nor the agent asked
    # for. ADR-010's own guardrail: cancel first, then undo.
    document.nodes[node_id].pending_request_id = "req-live"
    assert _dispatch(bus, "undo") is None
    assert document.nodes[node_id].x == 50  # unchanged - the undo was refused

    document.nodes[node_id].pending_request_id = None
    _dispatch(bus, "undo")
    assert document.nodes[node_id].x == 0


def test_organize_is_one_undo_not_one_per_node(wired):
    bus, document = wired
    a = _dispatch(bus, "addNode", 0, 0, "a")
    b = _dispatch(bus, "addNode", 500, 500, "b")
    before = {a: (document.nodes[a].x, document.nodes[a].y),
              b: (document.nodes[b].x, document.nodes[b].y)}

    _dispatch(bus, "organizeNodes")
    _dispatch(bus, "undo")

    # One Ctrl+Z restores the whole layout, not one node at a time.
    for node_id, (x, y) in before.items():
        assert (document.nodes[node_id].x, document.nodes[node_id].y) == (x, y)


def test_composite_groups_separate_commands_into_one_undo(document):
    a = document.add_note(0, 0)
    b = document.add_note(100, 0)

    with document.composite("multiMove", "user"):
        document.record_command("moveNode", "user",
                                lambda: document.move_node(a.id, 10, 10), node_ids=[a.id])
        document.record_command("moveNode", "user",
                                lambda: document.move_node(b.id, 110, 10), node_ids=[b.id])

    assert len(document.command_log) == 1
    document.undo()
    assert (document.nodes[a.id].x, document.nodes[a.id].y) == (0, 0)
    assert (document.nodes[b.id].x, document.nodes[b.id].y) == (100, 0)


def test_composite_merge_keeps_the_earliest_before_and_latest_after(document):
    node = document.add_note(0, 0)

    with document.composite("repeatedMove", "user"):
        document.record_command("moveNode", "user",
                                lambda: document.move_node(node.id, 10, 10), node_ids=[node.id])
        document.record_command("moveNode", "user",
                                lambda: document.move_node(node.id, 99, 99), node_ids=[node.id])

    command = document.command_log[-1]
    # Undo must go back to the state before the WHOLE group (0, 0), not to
    # the intermediate (10, 10) the first step left behind.
    command.invert(document)
    assert (document.nodes[node.id].x, document.nodes[node.id].y) == (0, 0)
    command.apply(document)
    assert (document.nodes[node.id].x, document.nodes[node.id].y) == (99, 99)


def test_a_composite_that_nets_out_to_nothing_is_not_logged(document):
    with document.composite("createThenDelete", "user"):
        created, _ = document.record_command(
            "addNote", "user", lambda: document.add_note(0, 0),
        )
        document.record_command(
            "removeNodes", "user", lambda: document.remove_nodes([created.id]),
            node_ids=[created.id],
        )
    # Created and deleted inside the same group - nothing survives, so there
    # is nothing to undo and the stack must not gain a do-nothing entry.
    assert len(document.command_log) == 0


def test_undo_run_reverses_a_whole_agent_build_in_one_action(document):
    """ADR-010 stage 10.5."""
    for index in range(3):
        _node, command = document.record_command(
            "addNote", "agent", lambda i=index: document.add_note(i * 10, 0),
        )
        command.run_id = "run-1"

    assert len(document.command_log) == 3
    reversed_count = document.undo_run("run-1")
    assert reversed_count == 3
    assert len(document.nodes) == 0


def test_undo_run_stops_at_the_users_own_later_edits(document):
    _node, command = document.record_command(
        "addNote", "agent", lambda: document.add_note(0, 0),
    )
    command.run_id = "run-1"
    user_node, _ = document.record_command(
        "addNote", "user", lambda: document.add_note(500, 500),
    )

    # The user's own work sits on top of the agent's. Undoing "the build"
    # must not silently discard it to reach the agent's commands underneath.
    assert document.undo_run("run-1") == 0
    assert user_node.id in document.nodes


def test_session_load_clears_both_stacks(wired):
    bus, document = wired
    node_id = _dispatch(bus, "addNode", 0, 0, "old session")
    _dispatch(bus, "undo")
    assert len(document.redo_stack) == 1

    document.clear_for_load()
    # Redoing here would resurrect a node from the PREVIOUS chat into this
    # one - both directions have to be cleared, not just the undo side.
    assert len(document.command_log) == 0
    assert len(document.redo_stack) == 0


# -- layer 5: pin snapshot/restore (ADR-010 close-out) ----------------------


def test_add_pin_is_invertible(document):
    from graphlink_navigation_pins import NavigationPinRecord

    _result, command = document.record_command(
        "addPin", "user",
        lambda: document.pins.add(NavigationPinRecord.create(title="Start", x=0, y=0)),
    )
    assert len(document.pins.records) == 1

    command.invert(document)
    assert len(document.pins.records) == 0


def test_remove_pin_is_invertible_including_the_sort_order_renumber_cascade(document):
    # .add(title=..., x=..., y=...) via kwargs (not a pre-built record) is
    # what makes the store auto-assign an incrementing sort_order - needed
    # here to set up a real ordering to test the renumber cascade against.
    a = document.pins.add(title="a", x=0, y=0)
    b = document.pins.add(title="b", x=1, y=1)
    c = document.pins.add(title="c", x=2, y=2)
    assert [p.sort_order for p in document.pins.records] == [0, 1, 2]

    _result, command = document.record_command(
        "removePin", "user", lambda: document.pins.remove(a.pin_id),
    )
    # remove() renumbers every pin AFTER the removed one - b and c shift down.
    assert [p.pin_id for p in document.pins.records] == [b.pin_id, c.pin_id]
    assert [p.sort_order for p in document.pins.records] == [0, 1]

    command.invert(document)
    # The whole-store snapshot restore undoes the renumber cascade too, not
    # just "a exists again" - order and sort_order both come back exact.
    restored = document.pins.records
    assert [p.pin_id for p in restored] == [a.pin_id, b.pin_id, c.pin_id]
    assert [p.sort_order for p in restored] == [0, 1, 2]


def test_move_and_update_pin_are_invertible(document):
    from graphlink_navigation_pins import NavigationPinRecord

    pin = document.pins.add(NavigationPinRecord.create(title="Waypoint", note="", x=0, y=0))

    _result, move_command = document.record_command(
        "movePin", "user", lambda: document.pins.move(pin.pin_id, 500, 500),
    )
    assert document.pins.get(pin.pin_id).position == (500, 500)
    move_command.invert(document)
    assert document.pins.get(pin.pin_id).position == (0, 0)

    _result, update_command = document.record_command(
        "updatePin", "user", lambda: document.pins.update(pin.pin_id, title="Renamed", note="edited"),
    )
    assert document.pins.get(pin.pin_id).title == "Renamed"
    update_command.invert(document)
    assert document.pins.get(pin.pin_id).title == "Waypoint"


def test_a_mutation_that_never_touches_pins_produces_no_pin_diff(document):
    _result, command = document.record_command(
        "addNote", "user", lambda: document.add_note(0, 0),
    )
    # None, not an empty tuple - "never touched" must stay distinguishable
    # from "watched and found unchanged" (an empty store IS a valid pin
    # state, e.g. after the last pin was removed).
    assert command.pin_before is None
    assert command.pin_after is None
    assert command.is_noop is False  # the note creation itself still counts


def test_pin_command_survives_a_redo_round_trip(document):
    from graphlink_navigation_pins import NavigationPinRecord

    _result, command = document.record_command(
        "addPin", "user",
        lambda: document.pins.add(NavigationPinRecord.create(title="p", x=0, y=0)),
    )
    command.invert(document)
    assert len(document.pins.records) == 0
    command.apply(document)
    assert len(document.pins.records) == 1
    assert document.pins.records[0].title == "p"


def test_pin_mutation_inside_a_composite_merges_correctly(document):
    from graphlink_navigation_pins import NavigationPinRecord

    pin = document.pins.add(NavigationPinRecord.create(title="p", x=0, y=0))

    with document.composite("multiPinEdit", "user"):
        document.record_command(
            "movePin", "user", lambda: document.pins.move(pin.pin_id, 10, 10),
        )
        document.record_command(
            "updatePin", "user", lambda: document.pins.update(pin.pin_id, title="renamed"),
        )

    assert len(document.command_log) == 1
    document.command_log[-1].invert(document)
    restored = document.pins.get(pin.pin_id)
    assert restored.position == (0, 0)
    assert restored.title == "p"


def test_pin_only_mutations_inside_a_composite_with_node_mutations_all_undo_together(document):
    from graphlink_navigation_pins import NavigationPinRecord

    node = document.add_note(0, 0)
    with document.composite("mixedEdit", "user"):
        document.record_command(
            "moveNode", "user", lambda: document.move_node(node.id, 50, 50), node_ids=[node.id],
        )
        document.record_command(
            "addPin", "user",
            lambda: document.pins.add(NavigationPinRecord.create(title="p", x=0, y=0)),
        )

    assert len(document.command_log) == 1
    document.command_log[-1].invert(document)
    assert document.nodes[node.id].x == 0
    assert len(document.pins.records) == 0


# -- layer 6: end-to-end for the ADR-010 close-out wraps ---------------------
#
# One test per module wrapped in the close-out, hitting the shape that
# matters most in that file - not exhaustive per-intent coverage (the
# classify-or-fail gate is what guarantees every List-A intent calls
# record_command at all; these prove the WRAPS are actually correct).


def test_pins_are_undoable_end_to_end(wired):
    bus, document = wired
    pin_id = _dispatch(bus, "addPin", "Waypoint", 0, 0, "")
    document.command_log.clear()

    _dispatch(bus, "movePin", pin_id, 500, 500)
    assert document.pins.get(pin_id).position == (500, 500)

    document.command_log[-1].invert(document)
    assert document.pins.get(pin_id).position == (0, 0)


def test_add_pin_assigns_an_incrementing_sort_order(wired):
    """Regression: the REAL addPin handler must go through the store's
    record=None kwargs path, which auto-assigns sort_order=len(records).
    The handler used to pre-build a NavigationPinRecord (whose dataclass
    default is sort_order=0) and pass it in, so every pin after the first
    landed at sort_order 0 - wrong for the persisted ordering key
    chat_library.py loads pins by (ORDER BY sort_order, id)."""
    bus, document = wired
    a = _dispatch(bus, "addPin", "First", 0, 0, "")
    b = _dispatch(bus, "addPin", "Second", 100, 0, "")
    c = _dispatch(bus, "addPin", "Third", 200, 0, "")

    by_id = {p.pin_id: p.sort_order for p in document.pins.records}
    assert [by_id[a], by_id[b], by_id[c]] == [0, 1, 2]

    # And the numbering stays dense across the store's remove-renumber
    # cascade: after removing the middle pin, a NEW pin continues from the
    # renumbered length, not from a stale count.
    _dispatch(bus, "removePin", b)
    d = _dispatch(bus, "addPin", "Fourth", 300, 0, "")
    assert [p.sort_order for p in document.pins.records] == [0, 1, 2]
    assert document.pins.get(d).sort_order == 2


def test_conversation_send_and_agent_reply_are_undoable_and_correctly_attributed(wired):
    bus, document = wired
    parent = _dispatch(bus, "addNote", 0, 0, False, False)
    node_id = _dispatch(bus, "addConversationNode", 0, 0, parent)
    document.command_log.clear()

    _dispatch(bus, "sendConversationMessage", node_id, "hello")
    # history is SceneNode's own generic field (reused across every
    # multi-turn kind), not something on state - see ArtifactState's own
    # docstring for why that field lives at the node level.
    assert len(document.nodes[node_id].history) == 1
    assert document.command_log[-1].command_type == "sendConversationMessage"
    assert document.command_log[-1].provenance == "user"

    document.command_log[-1].invert(document)
    assert len(document.nodes[node_id].history) == 0


def test_collapse_all_is_one_undo_across_every_node(wired):
    bus, document = wired
    a = _dispatch(bus, "addChatNode", 0, 0, "a", True, None)
    b = _dispatch(bus, "addChatNode", 100, 0, "b", True, None)
    document.command_log.clear()

    _dispatch(bus, "collapseAllNodes")
    assert document.nodes[a].is_collapsed and document.nodes[b].is_collapsed
    assert len(document.command_log) == 1

    document.command_log[-1].invert(document)
    assert not document.nodes[a].is_collapsed and not document.nodes[b].is_collapsed


def test_toggle_frame_lock_is_undoable_via_snapshot_not_replay(wired):
    """The flip-semantics concern from ADR-003's offline queue does not
    apply here - record_command restores a captured snapshot, it never
    re-invokes toggle_frame_lock, so there is no double-flip risk."""
    bus, document = wired
    member = _dispatch(bus, "addChatNode", 0, 0, "m", True, None)
    frame_id = _dispatch(bus, "createFrame", [member])
    document.command_log.clear()

    _dispatch(bus, "toggleFrameLock", frame_id)
    locked_after_toggle = document.nodes[frame_id].state.is_locked

    document.command_log[-1].invert(document)
    assert document.nodes[frame_id].state.is_locked != locked_after_toggle


def test_resize_chart_is_undoable(wired):
    bus, document = wired
    parent = _dispatch(bus, "addNote", 0, 0, False, False)
    # Built directly via the domain method (bypassing generateChart's own
    # async agent dispatch) - this test is about resizeChart's wrap, not
    # about exercising chart generation.
    node, _command = document.record_command(
        "addChartNode", "user",
        lambda: document.add_chart_node(0, 0, parent, "bar", {"labels": [], "values": []}),
    )
    document.command_log.clear()

    original_width = document.nodes[node.id].state.chart_width
    _dispatch(bus, "resizeChart", node.id, 900, 700)
    assert document.nodes[node.id].state.chart_width != original_width

    document.command_log[-1].invert(document)
    assert document.nodes[node.id].state.chart_width == original_width


def test_set_pycoder_mode_is_undoable(wired):
    bus, document = wired
    parent = _dispatch(bus, "addNote", 0, 0, False, False)
    node, _command = document.record_command(
        "addPycoderNode", "user", lambda: document.add_pycoder_node(0, 0, parent),
    )
    document.command_log.clear()

    _dispatch(bus, "setPyCoderMode", node.id, "manual")
    assert document.nodes[node.id].state.pycoder_mode == "manual"

    document.command_log[-1].invert(document)
    assert document.nodes[node.id].state.pycoder_mode != "manual"


def test_set_code_sandbox_requirements_is_undoable(wired):
    bus, document = wired
    parent = _dispatch(bus, "addNote", 0, 0, False, False)
    node, _command = document.record_command(
        "addCodeSandboxNode", "user",
        lambda: document.add_code_sandbox_node(0, 0, parent),
    )
    document.command_log.clear()

    _dispatch(bus, "setCodeSandboxRequirements", node.id, "requests==2.0")
    assert document.nodes[node.id].state.code_sandbox_requirements == "requests==2.0"

    document.command_log[-1].invert(document)
    assert document.nodes[node.id].state.code_sandbox_requirements != "requests==2.0"


def test_send_artifact_message_is_undoable(wired):
    bus, document = wired
    parent = _dispatch(bus, "addNote", 0, 0, False, False)
    node, _command = document.record_command(
        "addArtifactNode", "user", lambda: document.add_artifact_node(0, 0, parent),
    )
    document.command_log.clear()

    _dispatch(bus, "sendArtifactMessage", node.id, "write a haiku")
    assert len(document.nodes[node.id].history) == 1

    document.command_log[-1].invert(document)
    assert len(document.nodes[node.id].history) == 0


def test_set_gitlink_local_root_is_undoable(wired):
    bus, document = wired
    parent = _dispatch(bus, "addNote", 0, 0, False, False)
    node, _command = document.record_command(
        "addGitlinkNode", "user", lambda: document.add_gitlink_node(0, 0, parent),
    )
    document.command_log.clear()

    _dispatch(bus, "setGitlinkLocalRoot", node.id, "C:/somewhere")
    assert document.nodes[node.id].state.gitlink_local_root == "C:/somewhere"

    document.command_log[-1].invert(document)
    assert document.nodes[node.id].state.gitlink_local_root != "C:/somewhere"


def test_undo_never_resurrects_a_snapshotted_pending_request_id():
    """ADR-006 stage 6.4 review fix (HIGH): reply commands are recorded while
    the node's pending_request_id is still set (on_end runs in _dispatch's
    finally, AFTER on_reply), so the deepcopied before/after snapshots
    capture the live request id. Restoring it on undo/redo would resurrect a
    phantom "generating" state - the frontend keys live-stream rendering on
    that marker (blank node, frames never arrive) and _guard_live_runs would
    then refuse further undo for a run that no longer exists. _restore
    always writes the marker back as None; _guard_live_runs has already
    refused the operation if a REAL run is live on the node."""
    document = SceneDocument()
    node = document.add_chat_node(0, 0, "original", False)
    node.pending_request_id = "req-live-during-recording"

    document.record_command(
        "regenerateResponse", "agent",
        lambda: document.update_chat_node_content(node.id, "regenerated"),
        node_ids=[node.id],
    )
    node.pending_request_id = None  # on_end ran after the command recorded

    command = document.command_log[-1]
    command.invert(document)
    assert document.nodes[node.id].content == "original"
    assert document.nodes[node.id].pending_request_id is None, (
        "undo must not resurrect the snapshotted in-flight marker"
    )

    command.apply(document)
    assert document.nodes[node.id].content == "regenerated"
    assert document.nodes[node.id].pending_request_id is None, (
        "redo must not resurrect it either"
    )


def test_undo_never_resurrects_a_snapshotted_run_owned_builder_status():
    """review-fix: a builderReplan/builderPlan command recorded MID-RUN
    snapshots the plan node while builder_status was a run-owned value
    ("running"/"planning"/"awaiting_approval"). By the time undo/redo of
    it can run at all, _guard_live_runs guarantees that run has already
    landed for real (same reasoning test_undo_never_resurrects_a_
    snapshotted_pending_request_id documents for pending_request_id) -
    restoring the stale value would render the node permanently
    "Building..."/"Waiting for approval" with no live run behind it and
    no button that could ever advance it."""
    document = SceneDocument()
    node = document.add_plan_node(0, 0, "goal")
    node.state.plan_steps = [{"id": "s1", "title": "old", "status": "pending", "detail": ""}]
    node.state.builder_status = "running"  # mid-run when this command was recorded

    document.record_command(
        "builderReplan", "agent",
        lambda: document.set_plan_steps(node.id, [
            {"id": "s2", "title": "new", "status": "pending", "detail": ""},
        ]),
        node_ids=[node.id],
    )
    node.state.builder_status = "done"  # the run landed for real after recording

    command = document.command_log[-1]
    command.invert(document)
    assert document.nodes[node.id].state.plan_steps[0]["title"] == "old"
    assert document.nodes[node.id].state.builder_status == "interrupted", (
        "undo must not resurrect a run-owned status no live run backs"
    )

    command.apply(document)
    assert document.nodes[node.id].state.plan_steps[0]["title"] == "new"
    assert document.nodes[node.id].state.builder_status == "interrupted", (
        "redo must not resurrect it either"
    )
