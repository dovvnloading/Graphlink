"""Tests for stable node IDs and skip-safe index arithmetic in chat persistence.

Regression coverage for doc/ARCHITECTURE_REVIEW_FINDINGS.md #47: graph references were
serialized purely as list positions, and the load path re-derived several lookup maps
from *survivor* counts instead of original payload positions. When any node was skipped
during load (the documented behavior for node types whose plugins were removed - e.g. a
save containing an old "workflow" node), every later slot shifted and frames/containers
silently adopted the wrong members. Two-part fix, both covered here:

1. Skip-safe arithmetic (fixes EXISTING saves): the chart/note/frame slot offsets in
   restore_chat now come from the original payload counts (len(node_payloads) etc.),
   so a skipped node no longer shifts unrelated references. The decisive test below
   reproduces the old corruption exactly: a container referencing the first note would,
   with one plugin node skipped, silently resolve to the SECOND note.

2. Stable IDs (hardens FUTURE saves): every node payload now carries an `id`
   (uuid4, stored on the node, restored on load so it survives load/save cycles), and
   connections/children dual-write `*_id` fields alongside the legacy positional
   fields. The deserializer prefers IDs and falls back to positions for pre-ID saves.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_pycoder import PyCoderNode
from graphlink_scene import ChatScene
from graphlink_session.deserializers import SceneDeserializer
from graphlink_session.serializers import SceneSerializer


def _make_window_and_scene():
    window = MagicMock()
    scene = ChatScene(window=window)
    window.chat_view.scene.return_value = scene
    window.total_session_tokens = 0
    window.chat_view._zoom_factor = 1.0
    window.chat_view.horizontalScrollBar.return_value.value.return_value = 0
    window.chat_view.verticalScrollBar.return_value.value.return_value = 0
    return window, scene


def _build_source_scene():
    """chat_a -> chat_b (parent/child + connection), a PyCoder node under chat_a,
    and two notes. all_nodes order is chat nodes first, then pycoder (NODE_LIST_NAMES),
    so the save-side item space is: [chat_a=0, chat_b=1, pycoder=2, note0=3, note1=4]."""
    window, scene = _make_window_and_scene()
    chat_a = scene.add_chat_node("alpha", is_user=True)
    chat_b = scene.add_chat_node("bravo", is_user=False, parent_node=chat_a)
    pycoder = PyCoderNode(parent_node=chat_a)
    scene.addItem(pycoder)
    scene.pycoder_nodes.append(pycoder)
    note_x = scene.add_note(QPointF(10, 10))
    note_x.content = "note-x"
    note_y = scene.add_note(QPointF(20, 20))
    note_y.content = "note-y"
    return window, scene, chat_a, chat_b


def _serialize(window):
    return SceneSerializer(window).serialize_chat_data()


def _restore(chat_data):
    window, scene = _make_window_and_scene()
    deserializer = SceneDeserializer(window)
    ok = deserializer.restore_chat(
        {"title": "t", "data": chat_data},
        chat_data.get("notes_data", []),
        chat_data.get("pins_data", []),
    )
    assert ok, "restore_chat reported failure"
    return scene


def _strip_ids(value):
    """Simulate a pre-ID (legacy) payload by removing every identity field."""
    if isinstance(value, dict):
        return {
            key: _strip_ids(item)
            for key, item in value.items()
            if key not in ("id", "children_ids", "start_node_id", "end_node_id")
        }
    if isinstance(value, list):
        return [_strip_ids(item) for item in value]
    return value


class TestIdsAreWrittenAndStable:
    def test_every_node_payload_carries_an_id(self):
        window, *_ = _build_source_scene()
        chat_data = _serialize(window)
        assert all(payload.get("id") for payload in chat_data["nodes"])

    def test_connections_and_children_dual_write_ids_alongside_indices(self):
        window, *_ = _build_source_scene()
        chat_data = _serialize(window)

        connection = chat_data["connections"][0]
        assert {"start_node_index", "end_node_index", "start_node_id", "end_node_id"} <= set(connection)

        chat_a_payload = chat_data["nodes"][0]
        assert chat_a_payload["children_ids"]
        assert len(chat_a_payload["children_ids"]) == len(chat_a_payload["children_indices"])

    def test_ids_survive_a_load_save_cycle(self):
        window, *_ = _build_source_scene()
        first = _serialize(window)

        restored_scene = _restore(first)
        second_window = restored_scene.window
        second = _serialize(second_window)

        assert [p["id"] for p in first["nodes"]] == [p["id"] for p in second["nodes"]]


class TestRoundTripWithASkippedNode:
    """Simulates the documented real-world case: an old save contains a node type
    whose plugin has been removed (deserialize_node leaves it None and moves on)."""

    def _tampered_payload(self, strip_ids=False):
        window, scene, chat_a, chat_b = _build_source_scene()
        chat_data = _serialize(window)
        # The pycoder payload (save index 2) becomes a removed-plugin type.
        assert chat_data["nodes"][2]["node_type"] == "pycoder"
        chat_data["nodes"][2]["node_type"] = "workflow"
        if strip_ids:
            chat_data = _strip_ids(chat_data)
        return chat_data

    def test_parent_child_link_survives_the_skip(self):
        scene = _restore(self._tampered_payload())

        restored_by_text = {node.text: node for node in scene.nodes}
        assert restored_by_text["bravo"].parent_node is restored_by_text["alpha"]
        assert restored_by_text["alpha"].children == [restored_by_text["bravo"]]

    def test_chat_connection_attaches_to_the_right_nodes(self):
        scene = _restore(self._tampered_payload())

        assert len(scene.connections) == 1
        connection = scene.connections[0]
        assert connection.start_node.text == "alpha"
        assert connection.end_node.text == "bravo"

    def test_container_referencing_the_first_note_gets_the_first_note_not_the_second(self):
        # THE decisive corruption case. Save-side item space:
        #   [chat_a=0, chat_b=1, pycoder=2, note0=3, note1=4]
        # A container holding item 3 (the first note) used to resolve, after the
        # pycoder node was skipped, against a survivor-count-based map where key 3
        # pointed at the SECOND note - silent wrong-member adoption, no error.
        chat_data = self._tampered_payload()
        first_note_content = chat_data["notes_data"][0]["content"]
        second_note_content = chat_data["notes_data"][1]["content"]
        assert first_note_content != second_note_content  # sanity for the assertion below

        chat_data["containers"] = [{
            "items": [3],
            "position": {"x": 0, "y": 0},
            "title": "holds first note",
            "is_collapsed": False,
            "color": "#3a3a3a",
            "header_color": None,
            "expanded_rect": {"x": 0, "y": 0, "width": 200, "height": 200},
        }]

        scene = _restore(chat_data)

        assert len(scene.containers) == 1
        contained = scene.containers[0].contained_items
        assert len(contained) == 1
        assert contained[0].content == first_note_content

    def test_legacy_payload_without_ids_is_equally_skip_safe(self):
        # The arithmetic fix must protect EXISTING saves, which have no ID fields.
        chat_data = self._tampered_payload(strip_ids=True)
        first_note_content = chat_data["notes_data"][0]["content"]
        chat_data["containers"] = [{
            "items": [3],
            "position": {"x": 0, "y": 0},
            "title": "holds first note",
            "is_collapsed": False,
            "color": "#3a3a3a",
            "header_color": None,
            "expanded_rect": {"x": 0, "y": 0, "width": 200, "height": 200},
        }]

        scene = _restore(chat_data)

        restored_by_text = {node.text: node for node in scene.nodes}
        assert restored_by_text["bravo"].parent_node is restored_by_text["alpha"]
        assert len(scene.connections) == 1
        assert scene.containers[0].contained_items[0].content == first_note_content


class TestIdResolutionBeatsAWrongIndex:
    def test_connection_with_stale_index_but_valid_id_resolves_via_id(self):
        # If positions and IDs ever disagree (e.g. a payload edited or merged by an
        # external tool), the stable ID wins - that is the whole point of having it.
        window, scene, chat_a, chat_b = _build_source_scene()
        chat_data = _serialize(window)
        connection = chat_data["connections"][0]
        connection["start_node_index"] = 999  # garbage position
        connection["end_node_index"] = 999

        restored = _restore(chat_data)

        assert len(restored.connections) == 1
        assert restored.connections[0].start_node.text == "alpha"
        assert restored.connections[0].end_node.text == "bravo"
