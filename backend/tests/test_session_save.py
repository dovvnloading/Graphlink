"""Backend session SAVE tests (Qt-removal plan R6.5).

Exercises backend/session_save.py's build_chat_data against a real
SceneDocument, built via the SAME public API a live session already uses
(add_chat_node/create_frame/connect/etc - no mocking). Where a shape
matters for legacy compatibility, the expected keys/values are the ones
confirmed directly against graphlink_session/serializers.py (see
session_save.py's own module docstring for the exact citations) - not
approximated. Several tests round-trip the output straight back through
backend/session_load.py (already independently tested in
test_session_load.py) as the strongest possible correctness signal: if a
payload this module produces can't be read back by the project's own
loader, nothing else about its shape matters.
"""

import backend.agents as agents_module  # noqa: F401 - see test_canvas.py's own import-order note
from backend.canvas import SceneDocument
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data, _camel_to_snake_deep


def _round_trip(doc: SceneDocument) -> SceneDocument:
    chat_data = build_chat_data(doc)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    doc2 = SceneDocument()
    restore_chat_into_document(doc2, {"data": chat_data}, notes_data, pins_data)
    return doc2


def _has_edge(document, source_id, target_id) -> bool:
    return any(e.source == source_id and e.target == target_id for e in document.edges.values())


# -- per-kind node serialization ------------------------------------------


def test_chat_node_serializes_is_user_and_content():
    doc = SceneDocument()
    doc.add_chat_node(0, 0, "hello", is_user=True)
    chat_data = build_chat_data(doc)
    payload = chat_data["nodes"][0]
    assert payload["node_type"] == "chat"
    assert payload["raw_content"] == "hello"
    assert payload["is_user"] is True


def test_code_node_gets_parent_content_node_index_not_parent_node_index():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    doc.add_code_node(10, 10, "x=1", "python", parent.id)
    chat_data = build_chat_data(doc)
    code_payload = next(n for n in chat_data["nodes"] if n["node_type"] == "code")
    assert code_payload["parent_content_node_index"] == 0
    assert "parent_node_index" not in code_payload


def test_conversation_node_gets_parent_node_index_not_parent_content_node_index():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    doc.add_conversation_node(10, 10, parent.id)
    chat_data = build_chat_data(doc)
    conv_payload = next(n for n in chat_data["nodes"] if n["node_type"] == "conversation")
    assert conv_payload["parent_node_index"] == 0
    assert "parent_content_node_index" not in conv_payload


def test_web_node_kind_translates_to_legacy_web_node_type():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    doc.add_web_research_node(10, 10, parent.id)
    chat_data = build_chat_data(doc)
    web_payload = next(n for n in chat_data["nodes"] if n["node_type"] == "web")
    assert web_payload is not None


def test_code_sandbox_sandbox_id_survives_a_real_save_load_round_trip():
    # ADR-005 stage 5.3 (review-fix): the whole point of
    # code_sandbox_sandbox_id - a scratch-dir key stable ACROSS a reload -
    # is worthless if save/load doesn't actually preserve it. Uses the real
    # round trip (not a hand-built payload dict) as the strongest possible
    # signal, matching this file's own stated testing philosophy.
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    node = doc.add_code_sandbox_node(10, 10, parent.id)
    minted_sandbox_id = node.state.code_sandbox_sandbox_id
    assert minted_sandbox_id, "a sandbox id must be minted at creation time"

    doc2 = _round_trip(doc)

    restored = next(n for n in doc2.nodes.values() if n.kind == "code_sandbox")
    assert restored.state.code_sandbox_sandbox_id == minted_sandbox_id


def test_two_code_sandbox_nodes_get_different_sandbox_ids():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    a = doc.add_code_sandbox_node(10, 10, parent.id)
    b = doc.add_code_sandbox_node(20, 20, parent.id)
    assert a.state.code_sandbox_sandbox_id != b.state.code_sandbox_sandbox_id


def test_reload_after_deleting_an_earlier_node_does_not_swap_code_sandbox_scratch_dirs():
    """The exact bug an adversarial review caught before this stage shipped:
    node.id is reassigned fresh, purely by array position, on every load
    (register_restored_node) - so deleting a node ahead of a code_sandbox
    node in save order used to shift every later node's id on the NEXT
    load, which (before code_sandbox_sandbox_id existed) silently pointed
    that code_sandbox node's on-disk venv at whatever directory the id it
    inherited happened to name - potentially another node's leftover
    files, or an empty one that orphaned its own.

    Reproduces the concrete scenario from the finding: parent(chat) -> B
    (code_sandbox) -> C (code_sandbox), delete parent's OTHER child
    positioned ahead of B in save order, then round-trip. B and C's ids
    shift, but their code_sandbox_sandbox_id (and therefore
    VirtualEnvSandbox.base_dir) must not."""
    from graphlink_plugins.code_sandbox.domain import VirtualEnvSandbox

    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    decoy = doc.add_chat_node(10, 10, "decoy", is_user=False)  # ahead of B in save order
    node_b = doc.add_code_sandbox_node(20, 20, parent.id)
    node_c = doc.add_code_sandbox_node(30, 30, parent.id)
    node_b.state.code_sandbox_code = "b's code"
    node_c.state.code_sandbox_code = "c's code"
    sandbox_id_b = node_b.state.code_sandbox_sandbox_id
    sandbox_id_c = node_c.state.code_sandbox_sandbox_id
    dir_b_before = VirtualEnvSandbox(sandbox_id_b).base_dir
    dir_c_before = VirtualEnvSandbox(sandbox_id_c).base_dir
    assert dir_b_before != dir_c_before

    doc.remove_nodes([decoy.id])  # shifts every later node's array position
    doc2 = _round_trip(doc)

    sandbox_nodes = [n for n in doc2.nodes.values() if n.kind == "code_sandbox"]
    restored_b = next(n for n in sandbox_nodes if n.state.code_sandbox_code == "b's code")
    restored_c = next(n for n in sandbox_nodes if n.state.code_sandbox_code == "c's code")
    # The whole point: ids ARE free to have shifted (that volatility is
    # what caused the bug) - what must NOT have shifted is which on-disk
    # directory each node's sandbox resolves to.
    assert restored_b.state.code_sandbox_sandbox_id == sandbox_id_b
    assert restored_c.state.code_sandbox_sandbox_id == sandbox_id_c
    assert VirtualEnvSandbox(restored_b.state.code_sandbox_sandbox_id).base_dir == dir_b_before
    assert VirtualEnvSandbox(restored_c.state.code_sandbox_sandbox_id).base_dir == dir_c_before


def test_gitlink_node_packs_repo_state_and_proposal_data():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    node = doc.add_gitlink_node(10, 10, parent.id)
    node.state.gitlink_repo = "org/repo"
    node.state.gitlink_branch = "main"
    node.state.gitlink_scope_mode = "all"
    node.state.gitlink_local_root = "/tmp/x"
    node.state.gitlink_pending_changes = [{"path": "a.py"}]
    chat_data = build_chat_data(doc)
    payload = next(n for n in chat_data["nodes"] if n["node_type"] == "gitlink")
    assert payload["repo_state"] == {
        "repo": "org/repo", "branch": "main", "scope_mode": "all",
        "local_root": "/tmp/x", "imported_root": "",
    }
    assert payload["proposal_data"] == {"files": [{"path": "a.py"}]}


def test_image_node_encodes_bytes_from_image_assets_store():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    doc.add_image_node(10, 10, b"fake-png-bytes", "a prompt", parent.id)
    chat_data = build_chat_data(doc)
    payload = next(n for n in chat_data["nodes"] if n["node_type"] == "image")
    import base64
    assert base64.b64decode(payload["image_bytes"]) == b"fake-png-bytes"
    assert payload["prompt"] == "a prompt"


def test_research_result_translates_camel_case_back_to_snake_case():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    node = doc.add_web_research_node(10, 10, parent.id)
    node.state.research_result = {
        "requestId": "r1", "originalQuery": "q", "effectiveQuery": "q2",
        "answerMarkdown": "answer", "sources": [{"sourceId": "s1", "canonicalUrl": "u"}],
        "citations": [{"sourceId": "s1", "claimContext": "c"}], "warnings": [], "providerSnapshot": {},
    }
    chat_data = build_chat_data(doc)
    payload = next(n for n in chat_data["nodes"] if n["node_type"] == "web")
    result = payload["research_result"]
    assert result["request_id"] == "r1"
    assert result["answer_markdown"] == "answer"
    assert result["sources"][0]["source_id"] == "s1"
    assert result["sources"][0]["canonical_url"] == "u"
    assert result["citations"][0]["claim_context"] == "c"


def test_camel_to_snake_deep_is_a_true_inverse_of_snake_to_camel():
    from backend.session_load import _snake_to_camel_deep
    original = {"request_id": "x", "nested_list": [{"source_id": "y", "canonical_url": "z"}]}
    camel = _snake_to_camel_deep(original)
    back = _camel_to_snake_deep(camel)
    assert back == original


# -- edge classification --------------------------------------------------


def test_child_node_kinds_do_not_get_a_generic_connections_entry():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    doc.add_code_node(10, 10, "x", "python", parent.id)
    chat_data = build_chat_data(doc)
    assert chat_data["connections"] == []


def test_two_incoming_edges_to_one_target_only_the_first_becomes_parent_the_rest_is_catchall():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p1", is_user=False)
    other = doc.add_chat_node(0, 200, "p2", is_user=False)
    code = doc.add_code_node(10, 10, "x", "python", parent.id)
    # A second, manually-drawn incoming edge into the same code node.
    doc.connect(other.id, code.id)
    chat_data = build_chat_data(doc)
    code_payload = next(n for n in chat_data["nodes"] if n["node_type"] == "code")
    assert code_payload["parent_content_node_index"] == chat_data["nodes"].index(
        next(n for n in chat_data["nodes"] if n["id"] == parent.id)
    )
    assert len(chat_data["connections"]) == 1
    other_index = chat_data["nodes"].index(next(n for n in chat_data["nodes"] if n["id"] == other.id))
    assert chat_data["connections"][0]["start_node_index"] == other_index


def test_chat_to_code_edge_is_not_double_counted_as_a_child_relationship():
    # A chat's own code-node child is fully captured via the code node's own
    # parent_content_node_index - it must NOT also appear in the chat's
    # children_indices (legacy never did this either - children is scoped
    # to the chat-family branch tree only).
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    doc.add_code_node(10, 10, "x", "python", parent.id)
    chat_data = build_chat_data(doc)
    parent_payload = next(n for n in chat_data["nodes"] if n["node_type"] == "chat")
    assert "children_indices" not in parent_payload


def test_manual_connection_between_two_chat_nodes_becomes_children_not_catchall():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", is_user=True)
    b = doc.add_chat_node(0, 100, "b", is_user=False)
    doc.connect(a.id, b.id)
    chat_data = build_chat_data(doc)
    a_payload = next(n for n in chat_data["nodes"] if n["id"] == a.id)
    assert a_payload["children_indices"] == [chat_data["nodes"].index(next(n for n in chat_data["nodes"] if n["id"] == b.id))]
    assert chat_data["connections"] == []


def test_note_to_chat_edge_becomes_system_prompt_connection():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "hi", is_user=True)
    note = doc.add_note(-100, 0, is_system_prompt=True)
    doc.connect(note.id, chat.id)
    chat_data = build_chat_data(doc)
    assert len(chat_data["system_prompt_connections"]) == 1
    assert chat_data["system_prompt_connections"][0]["end_node_id"] == chat.id
    assert chat_data["connections"] == []


def test_chat_to_note_edge_becomes_group_summary_connection():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "hi", is_user=True)
    note = doc.add_note(-100, 0, is_summary_note=True)
    doc.connect(chat.id, note.id)
    chat_data = build_chat_data(doc)
    assert len(chat_data["group_summary_connections"]) == 1
    assert chat_data["group_summary_connections"][0]["start_node_id"] == chat.id
    assert chat_data["connections"] == []


def test_chart_parent_edge_is_captured_on_the_charts_own_payload():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    doc.add_chart_node(10, 10, parent.id, "bar", {"type": "bar", "title": "t", "labels": ["a"], "values": [1.0]})
    chat_data = build_chat_data(doc)
    assert len(chat_data["charts"]) == 1
    assert chat_data["charts"][0]["parent_node_id"] == parent.id
    # The chart's parent edge must NOT also leak into the generic catch-all.
    assert chat_data["connections"] == []


def test_parentless_chart_serializes_null_parent_fields():
    doc = SceneDocument()
    doc.add_chart_node(10, 10, None, "bar", {"type": "bar", "title": "t", "labels": ["a"], "values": [1.0]})
    chat_data = build_chat_data(doc)
    chart_payload = chat_data["charts"][0]
    assert chart_payload["parent_node_id"] is None
    assert chart_payload["parent_node_index"] is None


# -- frames / containers / offset math -------------------------------------


def test_frame_item_indices_reference_the_regular_nodes_list_positions():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", is_user=True)
    b = doc.add_chat_node(0, 100, "b", is_user=False)
    doc.create_frame([a.id, b.id])
    chat_data = build_chat_data(doc)
    frame_payload = chat_data["frames"][0]
    a_index = chat_data["nodes"].index(next(n for n in chat_data["nodes"] if n["id"] == a.id))
    b_index = chat_data["nodes"].index(next(n for n in chat_data["nodes"] if n["id"] == b.id))
    assert frame_payload["items"] == [a_index, b_index]


def test_frame_can_reference_a_chart_member_at_the_node_slot_count_offset():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", is_user=True)
    chart = doc.add_chart_node(10, 10, a.id, "bar", {"type": "bar", "title": "t", "labels": ["x"], "values": [1.0]})
    doc.create_frame([a.id, chart.id])
    chat_data = build_chat_data(doc)
    node_slot_count = len(chat_data["nodes"])
    frame_payload = chat_data["frames"][0]
    assert frame_payload["items"] == [0, node_slot_count]


def test_frame_position_survives_a_save_load_round_trip():
    """A frame the user dragged (and resized) must reload where they left
    it. _serialize_frame has always written the full rect, but the loader
    only ever read width/height back - so create_frame's own bbox-of-
    members position (derived from GROUP_MEMBER_DEFAULT_WIDTH/HEIGHT
    ESTIMATES, which no real rendered node matches) silently won, and every
    reload teleported the frame. Members here are deliberately spaced far
    wider than those 220x120 estimates, which is exactly when the drift is
    visible rather than incidental."""
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "question", is_user=True)
    b = doc.add_chat_node(0, 600, "reply", is_user=False, parent_id=a.id)
    frame = doc.create_frame([a.id, b.id])
    doc.resize_frame(frame.id, 1072.0, 795.0)
    doc.move_node(frame.id, -1170.0, 420.0)
    expected = (frame.x, frame.y, frame.state.group_width, frame.state.group_height)

    reloaded = _round_trip(doc)

    restored = next(n for n in reloaded.nodes.values() if n.kind == "frame")
    assert (restored.x, restored.y, restored.state.group_width, restored.state.group_height) == expected


def test_reloaded_frame_keeps_its_position_when_a_member_later_moves():
    """The position must be restored as a real manual ANCHOR (the same
    thing move_node pins for a live drag), not just assigned to x/y: only
    the anchor survives _recompute_group_bounds, which every later member
    move triggers. Without it a reload looks correct until the user nudges
    any member, at which point the frame jumps."""
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "question", is_user=True)
    b = doc.add_chat_node(0, 600, "reply", is_user=False, parent_id=a.id)
    frame = doc.create_frame([a.id, b.id])
    doc.resize_frame(frame.id, 1072.0, 795.0)
    doc.move_node(frame.id, -1170.0, 420.0)

    reloaded = _round_trip(doc)
    restored = next(n for n in reloaded.nodes.values() if n.kind == "frame")
    position_after_load = (restored.x, restored.y)
    member_id = restored.item_ids[0]
    member = reloaded.nodes[member_id]
    reloaded.move_node(member_id, member.x + 5.0, member.y)

    assert (restored.x, restored.y) == position_after_load


def test_a_manually_resized_frames_size_survives_being_saved_while_collapsed():
    """Technical-debt audit finding: group_width/group_height is the
    frame's CURRENT effective size - temporarily overwritten with the
    fixed collapsed-pill size while is_collapsed - but group_manual_width/
    height (the real, stable source of truth for a manual resize) is
    NEVER touched by collapse. _serialize_frame used to read group_width/
    height unconditionally, so autosave running while a manually-resized
    frame happened to be collapsed wrote the tiny pill size into "rect",
    which session_load.py's _restore_frames then applied as the frame's
    new PERMANENT manual size - destroying the real size the moment the
    user next expanded it."""
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", is_user=True)
    doc.set_measured_node_sizes([(a.id, 400.0, 300.0)])
    frame = doc.create_frame([a.id])
    doc.resize_frame(frame.id, 900.0, 700.0)  # user manually resizes bigger than auto-fit
    doc.toggle_group_collapsed(frame.id)  # collapsed at save time - the failure trigger
    assert frame.state.group_width == 260.0, "fixture invalid: must actually be collapsed to the pill"

    reloaded = _round_trip(doc)

    restored = next(n for n in reloaded.nodes.values() if n.kind == "frame")
    assert restored.is_collapsed is True
    reloaded.toggle_group_collapsed(restored.id)  # expand it back - the real test
    assert (restored.state.group_width, restored.state.group_height) == (900.0, 700.0), (
        "the user's real manual size must survive a save/load round trip "
        "made while the frame was collapsed, not be replaced by the pill size"
    )


def test_container_item_indices_reference_the_full_combined_offset_space():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", is_user=True)
    b = doc.add_chat_node(0, 100, "b", is_user=False)
    frame = doc.create_frame([a.id, b.id])
    doc.create_container([frame.id])
    chat_data = build_chat_data(doc)
    node_slot_count = len(chat_data["nodes"])
    note_slot_count = len(chat_data["notes_data"])
    chart_slot_count = len(chat_data["charts"])
    container_payload = chat_data["containers"][0]
    assert container_payload["items"] == [node_slot_count + note_slot_count + chart_slot_count]


def test_container_default_title_round_trips_through_content_field():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", is_user=True)
    container = doc.create_container([a.id])
    doc.set_group_label(container.id, "My Container")
    chat_data = build_chat_data(doc)
    assert chat_data["containers"][0]["title"] == "My Container"


def test_nested_containers_preserve_both_groups_and_membership_on_round_trip():
    doc = SceneDocument()
    note = doc.add_note(10, 20)
    inner = doc.create_container([note.id])
    outer = doc.create_container([inner.id])
    doc.set_group_label(inner.id, "Inner")
    doc.set_group_label(outer.id, "Outer")

    restored = _round_trip(doc)

    restored_note = next(node for node in restored.nodes.values() if node.kind == "note")
    restored_inner = next(node for node in restored.nodes.values() if node.content == "Inner")
    restored_outer = next(node for node in restored.nodes.values() if node.content == "Outer")
    assert restored_inner.item_ids == [restored_note.id]
    assert restored_outer.item_ids == [restored_inner.id]


# -- notes / pins / view state / tokens ------------------------------------


def test_note_serializes_content_position_and_flags():
    doc = SceneDocument()
    note = doc.add_note(12.0, 34.0, is_system_prompt=True)
    doc.set_note_content(note.id, "sys prompt text")
    chat_data = build_chat_data(doc)
    note_payload = chat_data["notes_data"][0]
    assert note_payload["content"] == "sys prompt text"
    assert note_payload["position"] == {"x": 12.0, "y": 34.0}
    assert note_payload["is_system_prompt"] is True


def test_pins_serialize_via_navigation_pin_record():
    doc = SceneDocument()
    doc.pins.add(title="My Pin", note="n", x=1.0, y=2.0)
    chat_data = build_chat_data(doc)
    assert len(chat_data["pins_data"]) == 1
    assert chat_data["pins_data"][0]["title"] == "My Pin"


def test_view_state_and_total_session_tokens_serialize():
    doc = SceneDocument()
    doc.set_view_state(1.5, 10.0, 20.0)
    doc.total_session_tokens = 42
    chat_data = build_chat_data(doc)
    assert chat_data["view_state"] == {"zoom_factor": 1.5, "scroll_position": {"x": 10.0, "y": 20.0}}
    assert chat_data["total_session_tokens"] == 42


# -- round trip (the strongest possible signal) ----------------------------


def test_round_trip_preserves_node_count_and_kinds_for_a_rich_scene():
    doc = SceneDocument()
    user = doc.add_chat_node(0, 0, "hello", is_user=True)
    ai = doc.add_chat_node(0, 100, "hi there", is_user=False, parent_id=user.id)
    doc.add_code_node(200, 100, "print(1)", "python", ai.id)
    doc.add_conversation_node(400, 100, ai.id)
    note = doc.add_note(-200, 0, is_system_prompt=True)
    doc.connect(note.id, user.id)
    doc.add_chart_node(300, 300, ai.id, "bar", {"type": "bar", "title": "T", "labels": ["a"], "values": [1.0]})
    frame = doc.create_frame([user.id, ai.id])
    doc.create_container([frame.id])
    doc.pins.add(title="P", note="", x=0.0, y=0.0)
    doc.set_view_state(2.0, 5.0, 6.0)
    doc.total_session_tokens = 77

    doc2 = _round_trip(doc)

    assert len(doc2.nodes) == len(doc.nodes)
    assert sorted(n.kind for n in doc2.nodes.values()) == sorted(n.kind for n in doc.nodes.values())
    assert doc2.zoom_factor == 2.0 and doc2.scroll_x == 5.0 and doc2.scroll_y == 6.0
    assert doc2.total_session_tokens == 77
    assert len(doc2.pins.records) == 1


def test_round_trip_preserves_every_edge_topologically():
    doc = SceneDocument()
    user = doc.add_chat_node(0, 0, "hello", is_user=True)
    ai = doc.add_chat_node(0, 100, "hi", is_user=False, parent_id=user.id)
    code = doc.add_code_node(200, 0, "x", "python", ai.id)
    doc.connect(user.id, code.id)  # extra manual connection

    doc2 = _round_trip(doc)
    user2 = next(n for n in doc2.nodes.values() if n.kind == "chat" and n.state.is_user)
    ai2 = next(n for n in doc2.nodes.values() if n.kind == "chat" and not n.state.is_user)
    code2 = next(n for n in doc2.nodes.values() if n.kind == "code")

    assert len(doc2.edges) == len(doc.edges) == 3
    assert _has_edge(doc2, user2.id, ai2.id)
    assert _has_edge(doc2, ai2.id, code2.id)
    assert _has_edge(doc2, user2.id, code2.id)


def test_round_trip_preserves_gitlink_field_values():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    node = doc.add_gitlink_node(10, 10, parent.id)
    node.state.gitlink_repo = "org/repo"
    node.state.gitlink_branch = "dev"
    node.state.gitlink_pending_changes = [{"path": "a.py"}, {"path": "b.py"}]

    doc2 = _round_trip(doc)
    node2 = next(n for n in doc2.nodes.values() if n.kind == "gitlink")
    assert node2.state.gitlink_repo == "org/repo"
    assert node2.state.gitlink_branch == "dev"
    assert node2.state.gitlink_pending_changes == [{"path": "a.py"}, {"path": "b.py"}]
    assert node2.state.gitlink_change_state == "previewed"


# -- ADR-002 Workstream 1: "Branch status and lifecycle" ---------------------
#
# Includes the confirmed, pre-existing gap fixed inline in this same pass -
# see backend/session_save.py's own comment on _serialize_chat_node/
# _serialize_note. These round-trip tests (build_chat_data ->
# restore_chat_into_document) are the strongest possible signal that fields
# already synced live to the frontend now ALSO survive a real Save then Load,
# which is exactly the gap that was silently failing before this fix.


def test_chat_node_serializes_synthesis_provenance_and_branch_status():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    first = doc.add_chat_node(0, 160, "first", False, parent_id=root.id)
    second = doc.add_chat_node(460, 160, "second", False, parent_id=root.id)
    result = doc.add_chat_node(0, 320, "Combined answer", False, parent_id=first.id)
    doc.mark_branch_synthesis(result.id, [first.id, second.id], "merge them", "Anthropic Claude", "claude-sonnet-5")
    doc.set_branch_status(result.id, "accepted")

    payload = next(p for p in build_chat_data(doc)["nodes"] if p.get("raw_content") == "Combined answer")
    assert payload["provider"] == "Anthropic Claude"
    assert payload["model"] == "claude-sonnet-5"
    assert payload["is_branch_synthesis"] is True
    assert payload["synthesis_instructions"] == "merge them"
    assert set(payload["item_ids"]) == {first.id, second.id}
    assert payload["branch_status"] == "accepted"


def test_chat_node_serializes_a_model_override_pin():
    # ADR-018 stage 18.3 - the input-routing opposite of provider/model
    # above (an explicit pin, not a completed reply's provenance).
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    doc.set_model_override(root.id, "Anthropic Claude", "claude-opus-5")

    payload = next(p for p in build_chat_data(doc)["nodes"] if p.get("raw_content") == "root")
    assert payload["override_provider"] == "Anthropic Claude"
    assert payload["override_model_id"] == "claude-opus-5"


def test_chat_node_with_no_model_override_serializes_empty_strings():
    doc = SceneDocument()
    doc.add_chat_node(0, 0, "root", True)

    payload = next(p for p in build_chat_data(doc)["nodes"] if p.get("raw_content") == "root")
    assert payload["override_provider"] == ""
    assert payload["override_model_id"] == ""


def test_note_serializes_branch_comparison_provenance():
    doc = SceneDocument()
    first = doc.add_chat_node(0, 0, "first", True)
    second = doc.add_chat_node(0, 160, "second", True)
    note = doc.add_note(0, 0)
    doc.mark_branch_comparison_note(note.id, [first.id, second.id])

    note_payload = build_chat_data(doc)["notes_data"][0]
    assert note_payload["is_branch_comparison"] is True
    assert set(note_payload["item_ids"]) == {first.id, second.id}


def test_final_deliverable_node_id_serializes_at_the_top_level():
    doc = SceneDocument()
    node = doc.add_chat_node(0, 0, "hi", True)
    doc.set_final_deliverable(node.id, True)
    chat_data = build_chat_data(doc)
    assert chat_data["final_deliverable_node_id"] == node.id


def test_final_deliverable_node_id_serializes_as_none_when_unmarked():
    doc = SceneDocument()
    doc.add_chat_node(0, 0, "hi", True)
    chat_data = build_chat_data(doc)
    assert chat_data["final_deliverable_node_id"] is None


def test_round_trip_preserves_synthesize_branches_full_shape():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root question", True)
    first = doc.add_chat_node(0, 160, "first branch reply", False, parent_id=root.id)
    second = doc.add_chat_node(460, 160, "second branch reply", False, parent_id=root.id)
    result = doc.add_chat_node(0, 320, "Combined answer", False, parent_id=first.id)
    doc.mark_branch_synthesis(result.id, [first.id, second.id], "merge them", "Anthropic Claude", "claude-sonnet-5")
    doc.set_branch_status(result.id, "accepted")
    doc.set_final_deliverable(result.id, True)

    doc2 = _round_trip(doc)
    first2 = next(n for n in doc2.nodes.values() if n.content == "first branch reply")
    second2 = next(n for n in doc2.nodes.values() if n.content == "second branch reply")
    result2 = next(n for n in doc2.nodes.values() if n.content == "Combined answer")

    assert result2.state.provider == "Anthropic Claude"
    assert result2.state.model == "claude-sonnet-5"
    assert result2.state.is_branch_synthesis is True
    assert result2.state.synthesis_instructions == "merge them"
    assert set(result2.item_ids) == {first2.id, second2.id}
    assert result2.state.branch_status == "accepted"
    assert doc2.final_deliverable_node_id == result2.id


def test_round_trip_preserves_compare_branches_full_shape():
    doc = SceneDocument()
    first = doc.add_chat_node(0, 0, "first branch reply", True)
    second = doc.add_chat_node(0, 160, "second branch reply", True)
    note = doc.add_note(0, 0)
    doc.set_note_content(note.id, "Branch Comparison\n\nAgreements:\n• both agree")
    doc.mark_branch_comparison_note(note.id, [first.id, second.id])

    doc2 = _round_trip(doc)
    first2 = next(n for n in doc2.nodes.values() if n.content == "first branch reply")
    second2 = next(n for n in doc2.nodes.values() if n.content == "second branch reply")
    note2 = next(n for n in doc2.nodes.values() if n.kind == "note")

    assert note2.state.is_branch_comparison is True
    assert set(note2.item_ids) == {first2.id, second2.id}
    assert note2.content == "Branch Comparison\n\nAgreements:\n• both agree"


def test_round_trip_preserves_chat_response_incomplete_marker():
    # ADR-006 stage 6.4 (H5): the interrupted-reply marker must survive
    # save/load - the whole point of preserving the partial is offering the
    # retry affordance again after a session reload, which is worthless if
    # the flag silently resets to False on the way back in. The untouched
    # sibling node pins the load path's absent-key -> False default (every
    # pre-6.4 save lacks the key) in the same round trip.
    doc = SceneDocument()
    doc.add_chat_node(0, 0, "a complete reply", is_user=False)
    partial = doc.add_chat_node(0, 160, "half a reply", is_user=False)
    partial.state.response_incomplete = True

    doc2 = _round_trip(doc)
    complete2 = next(n for n in doc2.nodes.values() if n.content == "a complete reply")
    partial2 = next(n for n in doc2.nodes.values() if n.content == "half a reply")

    assert partial2.state.response_incomplete is True
    assert complete2.state.response_incomplete is False


def test_round_trip_preserves_chat_tool_invocations():
    # ADR-007 stage 7.4: a turn's tool calls + results survive save/load -
    # `arguments` stays a real dict domain-side (only JSON-encoded at the
    # wire boundary in graph.py's scene_payload()), so the round trip must
    # preserve it as a dict, not a string. The untouched sibling node pins
    # the load path's absent-key -> [] default (every pre-7.4 save lacks
    # the key), same posture as the response_incomplete test above.
    doc = SceneDocument()
    doc.add_chat_node(0, 0, "no tools here", is_user=False)
    with_tools = doc.add_chat_node(0, 160, "used a tool", is_user=False)
    with_tools.state.tool_invocations = [
        {"id": "call_1", "name": "echo", "arguments": {"message": "hi"}, "result": "hi", "is_error": False},
    ]

    doc2 = _round_trip(doc)
    plain2 = next(n for n in doc2.nodes.values() if n.content == "no tools here")
    tools2 = next(n for n in doc2.nodes.values() if n.content == "used a tool")

    assert plain2.state.tool_invocations == []
    assert tools2.state.tool_invocations == [
        {"id": "call_1", "name": "echo", "arguments": {"message": "hi"}, "result": "hi", "is_error": False},
    ]


def test_round_trip_preserves_chat_estimated_cost_usd():
    # ADR-016 stage 16.2: the cost snapshot survives save/load, same
    # posture as prompt_tokens/completion_tokens. The untouched sibling
    # node pins the load path's absent-key -> None default (every pre-16.2
    # save lacks the key).
    doc = SceneDocument()
    doc.add_chat_node(0, 0, "no cost recorded", is_user=False)
    priced = doc.add_chat_node(0, 160, "a priced reply", is_user=False)
    priced.state.estimated_cost_usd = 0.0042

    doc2 = _round_trip(doc)
    plain2 = next(n for n in doc2.nodes.values() if n.content == "no cost recorded")
    priced2 = next(n for n in doc2.nodes.values() if n.content == "a priced reply")

    assert plain2.state.estimated_cost_usd is None
    assert priced2.state.estimated_cost_usd == 0.0042


def test_round_trip_preserves_conversation_history_incomplete_marker():
    # ADR-006 stage 6.4 (H5): a conversation node's partial assistant
    # message carries its "incomplete" key through save/load untouched -
    # both _serialize_history and _restore_history copy the whole message
    # dict, so the marker rides along; the normal messages must round-trip
    # in their exact two-key {role, content} shape (the key is only ever
    # WRITTEN when True - see append_conversation_assistant_message).
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    conv = doc.add_conversation_node(10, 10, parent.id)
    doc.append_conversation_user_message(conv.id, "hello")
    doc.append_conversation_assistant_message(conv.id, "half a reply", incomplete=True)

    doc2 = _round_trip(doc)
    conv2 = next(n for n in doc2.nodes.values() if n.kind == "conversation")

    assert conv2.history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "half a reply", "incomplete": True},
    ]
