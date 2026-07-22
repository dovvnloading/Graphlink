"""Canvas domain tests (Qt-removal plan R1): scene document invariants,
intent surface, grid payload compatibility with the generated validator's
shape, and snapshot publishing."""

import asyncio

import pytest

from backend.canvas import (
    DRAG_FACTOR_MAX,
    DRAG_FACTOR_MIN,
    SceneDocument,
    SceneError,
    register_canvas,
)
from backend.events import SessionBus
from backend.notifications import NotificationState


# -- document invariants ----------------------------------------------------


def test_add_move_and_remove_nodes():
    doc = SceneDocument()
    a = doc.add_node(10, 20, "A")
    assert doc.nodes[a.id].x == 10
    doc.move_node(a.id, -5.5, 7.25)
    assert (doc.nodes[a.id].x, doc.nodes[a.id].y) == (-5.5, 7.25)
    doc.remove_nodes([a.id])
    assert doc.nodes == {}


def test_move_unknown_node_raises_scene_error():
    with pytest.raises(SceneError):
        SceneDocument().move_node("nope", 0, 0)


def test_connect_validates_and_is_idempotent():
    doc = SceneDocument()
    a, b = doc.add_node(0, 0), doc.add_node(100, 0)
    edge = doc.connect(a.id, b.id)
    assert doc.connect(a.id, b.id).id == edge.id, "duplicate connect returns the same edge"
    with pytest.raises(SceneError):
        doc.connect(a.id, a.id)
    with pytest.raises(SceneError):
        doc.connect(a.id, "ghost")


def test_removing_a_node_removes_its_edges():
    doc = SceneDocument()
    a, b, c = doc.add_node(0, 0), doc.add_node(1, 1), doc.add_node(2, 2)
    doc.connect(a.id, b.id)
    keep = doc.connect(b.id, c.id)
    doc.remove_nodes([a.id])
    assert list(doc.edges) == [keep.id], "edges die with either endpoint"


# -- R3.1: chat nodes --------------------------------------------------------


def test_add_chat_node_creates_a_real_chat_kind_node():
    doc = SceneDocument()
    node = doc.add_chat_node(10, 20, "Hello there, this is a real message", True)
    assert node.kind == "chat"
    assert node.content == "Hello there, this is a real message"
    assert node.is_user is True
    assert node.is_collapsed is False
    assert node.title == "Hello there, this is a real message"[:60]


def test_add_chat_node_falls_back_to_role_title_for_empty_content():
    doc = SceneDocument()
    node = doc.add_chat_node(0, 0, "", False)
    assert node.title == "Assistant"


def test_add_chat_node_connects_to_a_real_parent():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "question", True)
    child = doc.add_chat_node(10, 10, "answer", False, parent_id=parent.id)
    assert any(e.source == parent.id and e.target == child.id for e in doc.edges.values())


def test_add_chat_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_chat_node(0, 0, "orphaned", True, parent_id="ghost")


def test_delete_chat_node_reparents_children_to_the_deleted_nodes_parent():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", True)
    b = doc.add_chat_node(1, 1, "b", False, parent_id=a.id)
    c = doc.add_chat_node(2, 2, "c", True, parent_id=b.id)

    doc.delete_chat_node(b.id)

    assert b.id not in doc.nodes
    assert any(e.source == a.id and e.target == c.id for e in doc.edges.values())
    assert not any(e.target == b.id or e.source == b.id for e in doc.edges.values())


def test_delete_chat_node_at_the_root_makes_children_new_roots():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    child = doc.add_chat_node(1, 1, "child", False, parent_id=root.id)

    doc.delete_chat_node(root.id)

    assert root.id not in doc.nodes
    assert not any(e.target == child.id for e in doc.edges.values()), "child has no parent edge left"


def test_delete_chat_node_unknown_raises():
    with pytest.raises(SceneError):
        SceneDocument().delete_chat_node("ghost")


def test_set_chat_collapsed():
    doc = SceneDocument()
    node = doc.add_chat_node(0, 0, "hi", True)
    doc.set_chat_collapsed(node.id, True)
    assert doc.nodes[node.id].is_collapsed is True
    with pytest.raises(SceneError):
        doc.set_chat_collapsed("ghost", True)


def test_scene_payload_includes_chat_fields_defaulted_for_placeholders():
    doc = SceneDocument()
    doc.add_node(0, 0, "plain")
    doc.add_chat_node(1, 1, "real content", True)
    rows = {n["title"]: n for n in doc.scene_payload()["nodes"]}
    assert rows["plain"]["kind"] == "placeholder"
    assert rows["plain"]["content"] == ""
    assert rows["plain"]["isUser"] is False
    assert rows["plain"]["isCollapsed"] is False
    chat_row = rows["real content"]
    assert chat_row["kind"] == "chat"
    assert chat_row["content"] == "real content"
    assert chat_row["isUser"] is True


def test_send_message_starts_a_root_branch():
    doc = SceneDocument()
    node = doc.send_message("hello there")
    assert node.kind == "chat"
    assert node.is_user is True
    assert node.content == "hello there"
    assert doc.last_chat_node_id == node.id


def test_send_message_continues_the_active_branch():
    doc = SceneDocument()
    first = doc.send_message("first message")
    second = doc.send_message("second message")
    assert any(e.source == first.id and e.target == second.id for e in doc.edges.values())
    assert doc.last_chat_node_id == second.id


def test_send_message_after_deleting_the_active_node_continues_from_its_parent():
    doc = SceneDocument()
    first = doc.send_message("first")
    second = doc.send_message("second")
    doc.delete_chat_node(second.id)
    assert doc.last_chat_node_id == first.id
    third = doc.send_message("third")
    assert any(e.source == first.id and e.target == third.id for e in doc.edges.values())


def test_drag_factor_is_bounded():
    doc = SceneDocument()
    doc.set_drag_factor(99)
    assert doc.drag_factor == DRAG_FACTOR_MAX
    doc.set_drag_factor(0.0001)
    assert doc.drag_factor == DRAG_FACTOR_MIN


def test_grid_payload_matches_generated_validator_shape():
    payload = SceneDocument().grid_payload()
    # Field-for-field the GridControlStatePayload contract (minus the
    # envelope, which the bus stamps): the R2 island port depends on this.
    assert set(payload) == {
        "gridSize",
        "gridOpacityPercent",
        "gridStyle",
        "gridColor",
        "sizePresets",
        "stylePresets",
        "colorPresets",
    }
    assert isinstance(payload["gridOpacityPercent"], int)
    assert len(payload["colorPresets"]) == 5


# -- intent surface over the bus --------------------------------------------


class Recorder:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)

    def topics_seen(self):
        return [m["topic"] for m in self.messages if m["kind"] == "state"]


def make_bus():
    bus = SessionBus("canvas-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    document = register_canvas(bus, notifications)
    recorder = Recorder()
    bus.attach(recorder)
    return bus, document, recorder


def test_scene_intents_mutate_and_publish():
    async def run():
        bus, document, recorder = make_bus()
        node_id = await bus.dispatch_intent("scene", "addNode", [40, 60, "hello"])
        assert document.nodes[node_id].title == "hello"
        other = await bus.dispatch_intent("scene", "addNode", [0, 0])
        edge_id = await bus.dispatch_intent("scene", "connectNodes", [node_id, other])
        assert edge_id in document.edges
        await bus.dispatch_intent("scene", "moveNode", [node_id, 1, 2])
        assert (document.nodes[node_id].x, document.nodes[node_id].y) == (1, 2)
        assert recorder.topics_seen().count("scene") == 4, "every mutation publishes"

    asyncio.run(run())


def test_chat_node_intents_mutate_and_publish():
    async def run():
        bus, document, recorder = make_bus()
        parent_id = await bus.dispatch_intent("scene", "addChatNode", [0, 0, "hi", True])
        child_id = await bus.dispatch_intent(
            "scene", "addChatNode", [10, 10, "reply", False, parent_id]
        )
        assert document.nodes[child_id].kind == "chat"
        assert any(
            e.source == parent_id and e.target == child_id for e in document.edges.values()
        )

        await bus.dispatch_intent("scene", "setChatCollapsed", [child_id, True])
        assert document.nodes[child_id].is_collapsed is True

        await bus.dispatch_intent("scene", "deleteChatNode", [parent_id])
        assert parent_id not in document.nodes
        assert child_id in document.nodes, "deleting the parent must not cascade-delete the child"
        assert recorder.topics_seen().count("scene") == 4, "every mutation publishes"

    asyncio.run(run())


def test_send_message_intent_creates_a_real_node_and_an_honest_deferred_notification():
    async def run():
        bus, document, recorder = make_bus()
        node_id = await bus.dispatch_intent("scene", "sendMessage", ["what is this graph about?"])
        assert document.nodes[node_id].content == "what is this graph about?"
        assert document.nodes[node_id].is_user is True

        notice = await bus.publish("notification")
        assert notice["visible"] is True
        assert notice["message"] == "AI response generation lands in R4."
        assert recorder.topics_seen().count("scene") == 1

    asyncio.run(run())


def test_pin_intents_round_trip_through_the_store():
    async def run():
        bus, document, _ = make_bus()
        pin_id = await bus.dispatch_intent("scene", "addPin", ["Start here", 5, 9, "note!"])
        payload = document.scene_payload()
        assert payload["pins"] == [
            {"id": pin_id, "title": "Start here", "note": "note!", "x": 5.0, "y": 9.0}
        ]
        await bus.dispatch_intent("scene", "movePin", [pin_id, 50, 90])
        assert document.scene_payload()["pins"][0]["x"] == 50.0
        await bus.dispatch_intent("scene", "removePin", [pin_id])
        assert document.scene_payload()["pins"] == []

    asyncio.run(run())


def test_update_pin_intent_renames_and_validates():
    async def run():
        bus, document, _ = make_bus()
        pin_id = await bus.dispatch_intent("scene", "addPin", ["Original", 0, 0])
        await bus.dispatch_intent("scene", "updatePin", [pin_id, "Renamed", "a note"])
        pin = document.scene_payload()["pins"][0]
        assert pin["title"] == "Renamed"
        assert pin["note"] == "a note"
        with pytest.raises(Exception):
            await bus.dispatch_intent("scene", "updatePin", [pin_id, "   ", ""])

    asyncio.run(run())


def test_grid_intents_use_the_bridge_slot_names_and_publish_grid_topic():
    async def run():
        bus, document, recorder = make_bus()
        await bus.dispatch_intent("grid-control", "setGridSize", [50])
        await bus.dispatch_intent("grid-control", "setGridOpacityPercent", [140])
        await bus.dispatch_intent("grid-control", "setGridStyle", ["Lines"])
        await bus.dispatch_intent("grid-control", "setGridColor", ["#404040"])
        assert document.grid.grid_size == 50
        assert document.grid.grid_opacity == 1.0, "opacity clamps to 100%"
        assert document.grid.grid_style == "Lines"
        assert recorder.topics_seen().count("grid-control") == 4

    asyncio.run(run())


def test_unknown_grid_style_is_rejected():
    async def run():
        bus, _, _ = make_bus()
        with pytest.raises(SceneError):
            await bus.dispatch_intent("grid-control", "setGridStyle", ["Sparkles"])

    asyncio.run(run())


def test_font_intents_use_bridge_slot_names_and_bound_values():
    async def run():
        bus, document, _ = make_bus()
        await bus.dispatch_intent("scene", "setFontFamily", ["Consolas"])
        await bus.dispatch_intent("scene", "setFontSize", [99])
        await bus.dispatch_intent("scene", "setFontColor", ["#C7C7C7"])
        payload = document.scene_payload()
        assert payload["fontFamily"] == "Consolas"
        assert payload["fontSizePt"] == 16, "size clamps to FONT_SIZE_MAX"
        assert payload["fontColor"] == "#C7C7C7"
        with pytest.raises(SceneError):
            await bus.dispatch_intent("scene", "setFontFamily", ["Comic Sans MS"])

    asyncio.run(run())


def test_organize_arranges_nodes_in_a_stable_grid():
    async def run():
        bus, document, _ = make_bus()
        for i in range(5):
            await bus.dispatch_intent("scene", "addNode", [500 - i * 37, i * 91])
        await bus.dispatch_intent("scene", "organizeNodes", [])
        positions = {n.id: (n.x, n.y) for n in document.nodes.values()}
        # 5 nodes -> 3 columns; stable id order fills rows left-to-right.
        assert positions["n0"] == (0.0, 0.0)
        assert positions["n1"] == (260.0, 0.0)
        assert positions["n2"] == (520.0, 0.0)
        assert positions["n3"] == (0.0, 180.0)
        assert positions["n4"] == (260.0, 180.0)

    asyncio.run(run())


def test_preset_topics_match_generated_validator_shapes():
    async def run():
        bus, _, recorder = make_bus()
        drag = await bus.publish("drag-speed")
        font = await bus.publish("font-control")
        assert set(drag) >= {"percentPresets", "percentMin", "percentMax"}
        assert set(font) >= {"fontFamilies", "colorPresets", "sizeMin", "sizeMax"}
        assert len(font["fontFamilies"]) == 16

    asyncio.run(run())


def test_snap_and_drag_factor_intents_publish_scene():
    async def run():
        bus, document, _ = make_bus()
        await bus.dispatch_intent("scene", "setSnapToGrid", [True])
        await bus.dispatch_intent("scene", "setDragFactor", [0.5])
        payload = document.scene_payload()
        assert payload["snapToGrid"] is True
        assert payload["dragFactor"] == 0.5

    asyncio.run(run())
