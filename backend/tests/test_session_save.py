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


def test_pycoder_mode_translates_back_to_uppercase_enum_member_name():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    node = doc.add_pycoder_node(10, 10, parent.id)
    node.pycoder_mode = "manual"
    chat_data = build_chat_data(doc)
    payload = next(n for n in chat_data["nodes"] if n["node_type"] == "pycoder")
    assert payload["mode"] == "MANUAL"


def test_gitlink_node_packs_repo_state_and_proposal_data():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", is_user=False)
    node = doc.add_gitlink_node(10, 10, parent.id)
    node.gitlink_repo = "org/repo"
    node.gitlink_branch = "main"
    node.gitlink_scope_mode = "all"
    node.gitlink_local_root = "/tmp/x"
    node.gitlink_pending_changes = [{"path": "a.py"}]
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
    node = doc.add_image_node(10, 10, b"fake-png-bytes", "a prompt", parent.id)
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
    chart = doc.add_chart_node(10, 10, parent.id, "bar", {"type": "bar", "title": "t", "labels": ["a"], "values": [1.0]})
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
    node.gitlink_repo = "org/repo"
    node.gitlink_branch = "dev"
    node.gitlink_pending_changes = [{"path": "a.py"}, {"path": "b.py"}]

    doc2 = _round_trip(doc)
    node2 = next(n for n in doc2.nodes.values() if n.kind == "gitlink")
    assert node2.gitlink_repo == "org/repo"
    assert node2.gitlink_branch == "dev"
    assert node2.gitlink_pending_changes == [{"path": "a.py"}, {"path": "b.py"}]
    assert node2.gitlink_change_state == "previewed"


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
