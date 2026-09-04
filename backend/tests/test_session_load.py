"""Backend session LOAD tests (Qt-removal plan R6.4).

Exercises backend/session_load.py's restore_chat_into_document against
backend/canvas.py's real SceneDocument - no mocking of the document itself,
since the whole point of this module is that it drives the SAME public
SceneDocument API a live session already uses (add_note/create_frame/
connect/etc). Payload shapes below are copied from the ACTUAL serialize
side (graphlink_session/serializers.py, plus the pre-R5-closeout recovery
via `git show af72ffd~1:...` for the 5 deleted plugin kinds) - not
approximated.
"""

import pytest

import backend.agents as agents_module  # noqa: F401 - see test_canvas.py's own import-order note
from backend.canvas import SceneDocument
from backend.session_load import restore_chat_into_document


def _chat(index_id, *, is_user=True, raw_content="hi", **extra):
    payload = {"node_type": "chat", "id": index_id, "raw_content": raw_content, "is_user": is_user,
               "position": {"x": 0.0, "y": 0.0}}
    payload.update(extra)
    return payload


def _restore(nodes=None, notes=None, pins=None, **chat_data_extra):
    document = SceneDocument()
    chat_data = {"nodes": nodes or []}
    chat_data.update(chat_data_extra)
    restore_chat_into_document(document, {"data": chat_data}, notes or [], pins or [])
    return document


# -- chat node -----------------------------------------------------------


def test_chat_node_is_user_defaults_true_when_key_missing():
    document = _restore(nodes=[{"node_type": "chat", "raw_content": "hey", "position": {"x": 0, "y": 0}}])
    node = next(iter(document.nodes.values()))
    assert node.state.is_user is True


def test_chat_node_raw_content_falls_back_to_legacy_text_key():
    document = _restore(nodes=[{"node_type": "chat", "text": "old shape", "position": {"x": 0, "y": 0}}])
    node = next(iter(document.nodes.values()))
    assert node.content == "old shape"


def test_untagged_node_type_defaults_to_chat():
    document = _restore(nodes=[{"raw_content": "no tag", "position": {"x": 0, "y": 0}}])
    assert len(document.nodes) == 1
    assert next(iter(document.nodes.values())).kind == "chat"


def test_a_non_hashable_node_type_is_skipped_not_raised():
    """SECURITY-FIX: node_type used to feed a dict lookup
    (_NODE_RESTORERS.get(node_type)) unguarded - a non-hashable JSON value
    (a list or dict) raised TypeError('unhashable type') uncaught, aborting
    the ENTIRE chat load over one malformed node in a hostile or hand-
    corrupted saved chat."""
    document = _restore(nodes=[
        {"node_type": ["nested", "list"], "position": {"x": 0, "y": 0}},
        _chat("survivor"),
    ])
    assert len(document.nodes) == 1
    assert next(iter(document.nodes.values())).kind == "chat"


def test_unrecognized_node_type_is_skipped_not_raised():
    document = _restore(nodes=[
        {"node_type": "some_future_kind", "position": {"x": 0, "y": 0}},
        _chat("a"),
    ])
    assert len(document.nodes) == 1


# -- hostile numeric fields must not cost the whole node ---------------------
# _restore_node swallows any restorer exception and drops the node, so a bare
# int() anywhere in a restorer turns one corrupt value into a vanished node -
# PR URL, stored diff, findings, scorecard and Q&A gone with no error shown.
# _non_negative_int is _finite_float's integer sibling and exists for exactly
# this; these tests pin the shape rather than the individual call sites.


def _code_review(**extra):
    # code_review is a _PARENT_NODE_INDEX_KINDS kind: without a resolvable
    # parent the node is skipped before its restorer is ever reached, so every
    # payload here is paired with _chat("parent") at index 0.
    payload = {"node_type": "code_review", "position": {"x": 0.0, "y": 0.0},
               "parent_node_index": 0, "pr_url": "https://github.com/o/r/pull/3"}
    payload.update(extra)
    return payload


def test_code_review_node_survives_a_non_numeric_score():
    document = _restore(nodes=[_chat("parent"),
                               _code_review(review={"scores": {"correctness": "n/a"}})])
    node = next(n for n in document.nodes.values() if n.kind == "code_review")
    assert node.state.code_review_pr_url == "https://github.com/o/r/pull/3"
    assert node.state.code_review_scores == {"correctness": 0}


def test_code_review_node_survives_non_numeric_counts():
    document = _restore(nodes=[_chat("parent"), _code_review(
        additions="lots", deletions=None, changed_files="many",
        diff_chars="?", diff_version="v2",
        pr_state={"number": "twelve"}, review={"quality_score": "high"},
    )])
    node = next(n for n in document.nodes.values() if n.kind == "code_review")
    assert node.state.code_review_additions == 0
    assert node.state.code_review_changed_files == 0
    assert node.state.code_review_pr_number == 0
    assert node.state.code_review_quality_score == 0


@pytest.mark.parametrize("bad_cap", [-5, "-5", "0", 0.5, 0, "twelve", None, "", []])
def test_plan_caps_never_restore_to_an_unusable_value(bad_cap):
    """A counter may be 0; a cap may not. builder._spend_breach rejects a plan
    with max_steps=0 on its first tick, so it lands on the canvas and can never
    run again.

    _non_negative_int was the wrong helper for these and was used anyway: it
    only falls back when int() RAISES, so every numeric route to zero got
    through - -5 and '0' and 0.5 all clamped to 0 rather than falling back."""
    document = _restore(nodes=[{
        "node_type": "plan", "position": {"x": 0.0, "y": 0.0}, "goal": "g",
        "max_steps": bad_cap, "max_tokens": bad_cap, "max_wall_seconds": bad_cap,
    }])
    node = next(n for n in document.nodes.values() if n.kind == "plan")
    assert node.state.builder_max_steps == 12
    assert node.state.builder_max_tokens == 150_000
    assert node.state.builder_max_wall_seconds == 900


@pytest.mark.parametrize("bad_cap", [-5, "-5", "0", 0, "x", None])
def test_harness_caps_never_restore_negative_or_zero(bad_cap):
    """The harness restorer had it worse than plan: its local `_int` helper
    passed negatives straight through, so a saved -5 restored as -5."""
    document = _restore(nodes=[{
        "node_type": "harness", "position": {"x": 0.0, "y": 0.0}, "goal": "g",
        "max_turns": bad_cap, "max_context_tokens": bad_cap,
    }])
    node = next(n for n in document.nodes.values() if n.kind == "harness")
    assert node.state.harness_max_turns == 16
    assert node.state.harness_max_context_tokens == 48_000


def test_real_caps_and_zero_counters_both_survive():
    """The other side: a legitimate cap is kept, and a counter of 0 - which is
    a real value, not a missing one - is not rewritten to a default."""
    document = _restore(nodes=[
        {"node_type": "plan", "position": {"x": 0.0, "y": 0.0}, "goal": "g",
         "max_steps": 7, "spent_steps": 0},
        {"node_type": "harness", "position": {"x": 0.0, "y": 0.0}, "goal": "g",
         "max_turns": 9, "spent_turns": 0, "compactions": 0},
    ])
    plan = next(n for n in document.nodes.values() if n.kind == "plan")
    harness = next(n for n in document.nodes.values() if n.kind == "harness")
    assert plan.state.builder_max_steps == 7
    assert plan.state.builder_spent_steps == 0
    assert harness.state.harness_max_turns == 9
    assert harness.state.harness_spent_turns == 0
    assert harness.state.harness_compactions == 0


def test_plan_node_survives_non_numeric_budget_fields():
    """The same bug class in the plan restorer: it already guarded activity
    elapsedMs against exactly this, then read six budget fields with a bare
    int() six lines further down."""
    document = _restore(nodes=[{
        "node_type": "plan", "position": {"x": 0.0, "y": 0.0}, "goal": "ship it",
        "max_steps": "twelve", "max_tokens": "lots", "max_wall_seconds": "soon",
        "spent_steps": "some", "spent_tokens": "many", "spent_wall_seconds": "a while",
    }])
    node = next(n for n in document.nodes.values() if n.kind == "plan")
    assert node.state.plan_goal == "ship it"
    # Unreadable caps fall back to their documented defaults, not to zero -
    # a restored plan with max_steps=0 could never run again.
    assert node.state.builder_max_steps == 12
    assert node.state.builder_max_tokens == 150_000
    assert node.state.builder_max_wall_seconds == 900
    assert node.state.builder_spent_steps == 0


def test_a_restorer_that_raises_is_logged_not_swallowed_in_silence(caplog):
    """The node is still dropped - one bad row must not fail the whole load -
    but it no longer disappears without a trace."""
    import logging

    import backend.session_load as session_load

    def _boom(payload, document):
        raise RuntimeError("restorer exploded")

    original = session_load._NODE_RESTORERS["code_review"]
    session_load._NODE_RESTORERS["code_review"] = _boom
    try:
        with caplog.at_level(logging.ERROR, logger="backend.session_load"):
            document = _restore(nodes=[_chat("parent"), _code_review()])
    finally:
        session_load._NODE_RESTORERS["code_review"] = original

    assert len(document.nodes) == 1  # the parent chat still loaded
    assert "code_review" in caplog.text
    assert "restorer exploded" in caplog.text


# -- parent-required kinds: parent_content_node_index ------------------------


def test_code_node_restores_and_connects_when_parent_resolves():
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "code", "code": "print(1)", "language": "python",
         "position": {"x": 10, "y": 10}, "parent_content_node_index": 0},
    ])
    assert len(document.nodes) == 2
    code_node = next(n for n in document.nodes.values() if n.kind == "code")
    chat_node = next(n for n in document.nodes.values() if n.kind == "chat")
    assert code_node.state.code == "print(1)" and code_node.state.language == "python"
    assert any(e.source == chat_node.id and e.target == code_node.id for e in document.edges.values())


def test_code_node_is_skipped_entirely_when_parent_index_does_not_resolve():
    document = _restore(nodes=[
        _chat("only"),
        {"node_type": "code", "code": "print(1)", "language": "python",
         "position": {"x": 10, "y": 10}, "parent_content_node_index": 5},
    ])
    assert len(document.nodes) == 1
    assert next(iter(document.nodes.values())).kind == "chat"


def test_code_node_cannot_reference_a_later_payload_position_as_parent():
    # The single-pass restore loop builds all_nodes_map incrementally - a
    # node at position 0 referencing position 1 (which hasn't been created
    # yet) must fail to resolve, exactly like legacy's own forward-only pass.
    document = _restore(nodes=[
        {"node_type": "code", "code": "x", "language": "python",
         "position": {"x": 0, "y": 0}, "parent_content_node_index": 1},
        _chat("later"),
    ])
    assert len(document.nodes) == 1
    assert next(iter(document.nodes.values())).kind == "chat"


# -- parent-required kinds: parent_node_index --------------------------------


def test_conversation_node_uses_parent_node_index_not_parent_content_node_index():
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "conversation", "conversation_history": [{"role": "user", "content": "hi"}],
         "is_collapsed": False, "position": {"x": 0, "y": 0}, "parent_node_index": 0},
    ])
    assert len(document.nodes) == 2
    conv = next(n for n in document.nodes.values() if n.kind == "conversation")
    assert conv.history == [{"role": "user", "content": "hi"}]


def test_code_sandbox_field_mapping():
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "code_sandbox", "prompt": "build x", "requirements": "numpy",
         "code": "import numpy", "output": "ok", "analysis": "good", "sandbox_id": "sbx-1",
         "position": {"x": 0, "y": 0}, "parent_node_index": 0},
    ])
    node = next(n for n in document.nodes.values() if n.kind == "code_sandbox")
    assert node.state.code_sandbox_requirements == "numpy" and node.state.code_sandbox_sandbox_id == "sbx-1"


def test_legacy_pycoder_ai_driven_payload_migrates_to_a_done_harness_node():
    # PLAN-2026-08-24 H5: Py-Coder is retired - a saved "pycoder" payload
    # can no longer restore as a pycoder node at all. An ai_driven-mode
    # payload's prompt becomes the harness goal and its analysis becomes
    # the reply, landed at a terminal "done" status so nothing renders a
    # stale spinner.
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "pycoder", "mode": "AI_DRIVEN", "prompt": "sort this list", "code": "x=1",
         "output": "1", "analysis": "sorted it", "position": {"x": 0, "y": 0}, "parent_node_index": 0},
    ])
    node = next(n for n in document.nodes.values() if n.kind == "harness")
    assert node.state.harness_goal == "sort this list"
    assert node.state.harness_reply == "sorted it"
    assert node.state.harness_status == "done"
    assert node.state.harness_workspace_id


def test_legacy_pycoder_manual_mode_payload_migrates_with_the_code_in_the_goal():
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "pycoder", "mode": "MANUAL", "prompt": "", "code": "print(6*7)",
         "output": "", "analysis": "", "position": {"x": 0, "y": 0}, "parent_node_index": 0},
    ])
    node = next(n for n in document.nodes.values() if n.kind == "harness")
    assert "print(6*7)" in node.state.harness_goal
    assert node.state.harness_reply == ""
    assert node.state.harness_status == "idle", "no reply means nothing has actually completed"


def test_legacy_pycoder_payload_reply_falls_back_to_output_when_analysis_is_blank():
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "pycoder", "mode": "AI_DRIVEN", "prompt": "run it", "code": "x=1",
         "output": "1", "analysis": "", "position": {"x": 0, "y": 0}, "parent_node_index": 0},
    ])
    node = next(n for n in document.nodes.values() if n.kind == "harness")
    assert node.state.harness_reply == "1"


def test_a_blank_legacy_pycoder_payload_migrates_to_a_safe_idle_harness_node():
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "pycoder", "mode": "MANUAL", "prompt": "", "code": "",
         "position": {"x": 0, "y": 0}, "parent_node_index": 0},
    ])
    node = next(n for n in document.nodes.values() if n.kind == "harness")
    assert node.state.harness_goal == ""
    assert node.state.harness_reply == ""
    assert node.state.harness_status == "idle"
    assert node.title == "Agent"


def test_artifact_node_reuses_instruction_as_content_and_content_as_artifact_content():
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "artifact", "instruction": "write a poem", "content": "roses are red",
         "position": {"x": 0, "y": 0}, "parent_node_index": 0},
    ])
    node = next(n for n in document.nodes.values() if n.kind == "artifact")
    assert node.content == "write a poem" and node.state.artifact_content == "roses are red"


def test_gitlink_node_unpacks_repo_state_and_synthesizes_proposal_markdown():
    document = _restore(nodes=[
        _chat("parent"),
        {
            "node_type": "gitlink", "task_prompt": "fix bug",
            "repo_state": {"repo": "org/repo", "branch": "main", "scope_mode": "all",
                            "local_root": "/tmp/repo", "imported_root": ""},
            "proposal_data": {"files": [{"path": "a.py"}, {"path": "b.py"}]},
            "position": {"x": 0, "y": 0}, "parent_node_index": 0,
        },
    ])
    node = next(n for n in document.nodes.values() if n.kind == "gitlink")
    assert node.state.gitlink_repo == "org/repo" and node.state.gitlink_branch == "main"
    assert node.state.gitlink_pending_changes == [{"path": "a.py"}, {"path": "b.py"}]
    assert "a.py" in node.state.gitlink_proposal_markdown and "b.py" in node.state.gitlink_proposal_markdown
    assert node.state.gitlink_change_state == "previewed"
    assert node.state.gitlink_change_fingerprint is None


def test_gitlink_node_with_no_pending_changes_is_draft_state():
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "gitlink", "task_prompt": "", "repo_state": {}, "proposal_data": {},
         "position": {"x": 0, "y": 0}, "parent_node_index": 0},
    ])
    node = next(n for n in document.nodes.values() if n.kind == "gitlink")
    assert node.state.gitlink_change_state == "draft"


# -- web research / research_result translation ------------------------------


def test_web_node_research_result_translates_snake_case_to_camel_case():
    document = _restore(nodes=[
        _chat("parent"),
        {
            "node_type": "web", "query": "best pasta recipe",
            "research_result": {
                "request_id": "req-1", "original_query": "pasta", "effective_query": "best pasta recipe",
                "answer_markdown": "Use fresh tomatoes.",
                "sources": [{"source_id": "s1", "title": "Recipe Site", "url": "https://example.com"}],
                "citations": [{"source_id": "s1", "marker": "[1]", "claim_context": "tomatoes"}],
                "warnings": [], "provider_snapshot": {},
            },
            "position": {"x": 0, "y": 0}, "parent_node_index": 0,
        },
    ])
    node = next(n for n in document.nodes.values() if n.kind == "web_research")
    result = node.state.research_result
    assert result["requestId"] == "req-1"
    assert result["answerMarkdown"] == "Use fresh tomatoes."
    assert result["sources"][0]["sourceId"] == "s1"
    assert result["citations"][0]["claimContext"] == "tomatoes"


def test_web_node_falls_back_to_summary_and_sources_for_older_shape_sessions():
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "web", "query": "q", "summary": "the answer", "sources": [{"title": "t"}],
         "research_result": {}, "position": {"x": 0, "y": 0}, "parent_node_index": 0},
    ])
    node = next(n for n in document.nodes.values() if n.kind == "web_research")
    assert node.state.research_result["answerMarkdown"] == "the answer"
    assert node.state.research_result["sources"] == [{"title": "t"}]


def test_web_node_kind_is_web_research_not_web():
    document = _restore(nodes=[
        _chat("parent"),
        {"node_type": "web", "query": "q", "position": {"x": 0, "y": 0}, "parent_node_index": 0},
    ])
    node = next(n for n in document.nodes.values() if n.kind != "chat")
    assert node.kind == "web_research"


# -- children_indices / children_ids ------------------------------------------


def test_children_indices_restore_as_real_edges_for_child_link_kinds():
    document = _restore(nodes=[
        {"node_type": "chat", "id": "root", "raw_content": "root", "is_user": True,
         "position": {"x": 0, "y": 0}, "children_indices": [1]},
        {"node_type": "chat", "id": "child", "raw_content": "child", "is_user": False,
         "position": {"x": 0, "y": 100}},
    ])
    root = next(n for n in document.nodes.values() if n.content == "root")
    child = next(n for n in document.nodes.values() if n.content == "child")
    assert any(e.source == root.id and e.target == child.id for e in document.edges.values())


def test_children_ids_are_id_preferred_over_children_indices():
    document = _restore(nodes=[
        {"node_type": "chat", "id": "root", "raw_content": "root", "is_user": True,
         "position": {"x": 0, "y": 0}, "children_ids": ["child-b"], "children_indices": [1]},
        {"node_type": "chat", "id": "child-a", "raw_content": "wrong child", "is_user": False,
         "position": {"x": 0, "y": 100}},
        {"node_type": "chat", "id": "child-b", "raw_content": "right child", "is_user": False,
         "position": {"x": 0, "y": 200}},
    ])
    root = next(n for n in document.nodes.values() if n.content == "root")
    right_child = next(n for n in document.nodes.values() if n.content == "right child")
    wrong_child = next(n for n in document.nodes.values() if n.content == "wrong child")
    assert any(e.source == root.id and e.target == right_child.id for e in document.edges.values())
    assert not any(e.source == root.id and e.target == wrong_child.id for e in document.edges.values())


def test_malformed_child_id_is_skipped_without_aborting_the_rest_of_the_load():
    # A hand-edited/imported save with a non-scalar children_ids entry
    # (valid JSON, wrong shape) must not raise TypeError and abort the
    # whole load - only that one malformed child is dropped, a well-formed
    # sibling entry at another position still resolves.
    document = _restore(nodes=[
        {"node_type": "chat", "id": "root", "raw_content": "root", "is_user": True,
         "position": {"x": 0, "y": 0}, "children_ids": [["nested", "list"], "child-b"]},
        {"node_type": "chat", "id": "child-a", "raw_content": "child-a", "is_user": False,
         "position": {"x": 0, "y": 100}},
        {"node_type": "chat", "id": "child-b", "raw_content": "child-b", "is_user": False,
         "position": {"x": 0, "y": 200}},
    ])
    root = next(n for n in document.nodes.values() if n.content == "root")
    child_b = next(n for n in document.nodes.values() if n.content == "child-b")
    assert len(document.edges) == 1
    assert any(e.source == root.id and e.target == child_b.id for e in document.edges.values())


# -- notes --------------------------------------------------------------


def test_notes_restore_position_content_and_flags():
    document = _restore(notes=[
        {"content": "sys prompt", "position": {"x": 12.0, "y": 34.0}, "size": {"width": 1, "height": 1},
         "color": "#abcdef", "header_color": None, "is_system_prompt": True, "is_summary_note": False},
    ])
    note = next(iter(document.nodes.values()))
    assert note.kind == "note" and note.content == "sys prompt"
    assert (note.x, note.y) == (12.0, 34.0)
    assert note.color == "#abcdef" and note.state.is_system_prompt is True


# -- charts ---------------------------------------------------------------


def test_chart_restores_with_size_and_aspect_lock_override():
    document = _restore(
        nodes=[_chat("parent")],
        charts=[{
            "id": "chart-1", "data": {"type": "bar", "title": "T", "labels": ["a"], "values": [1.0]},
            "position": {"x": 5, "y": 5}, "size": {"width": 600, "height": 400},
            "aspect_ratio_locked": False, "parent_node_index": 0,
        }],
    )
    chart = next(n for n in document.nodes.values() if n.kind == "chart")
    assert chart.state.chart_width == 600.0 and chart.state.chart_height == 400.0
    assert chart.state.chart_aspect_locked is False


def test_chart_aspect_lock_is_applied_before_resize_not_after():
    # Adversarial-review finding: 600x400 (both already above CHART_MIN_
    # WIDTH/HEIGHT) never actually exercises resize_chart's aspect-preserving
    # branch, since nothing needs clamping - the ordering bug (toggling
    # aspect_locked AFTER resize_chart runs, instead of before) would pass
    # that test even reverted. A requested size BELOW both minimums (100x100,
    # vs CHART_MIN_WIDTH=440/CHART_MIN_HEIGHT=320) genuinely diverges:
    # confirmed empirically that resizing while still locked (the bug) yields
    # a square 440x440 (the aspect-preserving re-derivation kicks in off the
    # clamped width), while unlocking first (the fix) yields 440x320 (each
    # dimension clamped independently, no re-derivation) - this test would
    # fail if the two calls in _restore_charts were reordered.
    document = _restore(
        nodes=[_chat("parent")],
        charts=[{
            "id": "chart-1", "data": {"type": "bar", "title": "T", "labels": ["a"], "values": [1.0]},
            "position": {"x": 0, "y": 0}, "size": {"width": 100, "height": 100},
            "aspect_ratio_locked": False, "parent_node_index": 0,
        }],
    )
    chart = next(n for n in document.nodes.values() if n.kind == "chart")
    assert (chart.state.chart_width, chart.state.chart_height) == (440.0, 320.0)


def test_chart_with_unsupported_type_is_skipped():
    document = _restore(
        nodes=[],
        charts=[{"id": "x", "data": {"type": "not-a-real-type"}, "position": {"x": 0, "y": 0}}],
    )
    assert not any(n.kind == "chart" for n in document.nodes.values())


# -- frames -----------------------------------------------------------------


def test_frame_membership_uses_items_key_not_item_indices():
    document = _restore(
        nodes=[_chat("a"), _chat("b")],
        frames=[{"items": [0, 1], "note": "Group", "position": {"x": 0, "y": 0},
                 "rect": {"x": 0, "y": 0, "width": 500, "height": 500}, "is_locked": True, "is_collapsed": False}],
    )
    frame = next(n for n in document.nodes.values() if n.kind == "frame")
    member_kinds = {document.nodes[i].kind for i in frame.item_ids}
    assert member_kinds == {"chat"}
    assert frame.state.group_manual_width is not None and frame.state.group_manual_height is not None


def test_frame_falls_back_to_legacy_nodes_key():
    document = _restore(
        nodes=[_chat("a")],
        frames=[{"nodes": [0], "note": "Old shape", "position": {"x": 0, "y": 0}}],
    )
    frame = next(n for n in document.nodes.values() if n.kind == "frame")
    assert len(frame.item_ids) == 1


def test_frame_with_no_resolvable_members_is_skipped():
    document = _restore(
        nodes=[_chat("a")],
        frames=[{"items": [99], "note": "Ghost", "position": {"x": 0, "y": 0}}],
    )
    assert not any(n.kind == "frame" for n in document.nodes.values())


def test_frame_unlocked_flag_is_applied():
    document = _restore(
        nodes=[_chat("a")],
        frames=[{"items": [0], "note": "Unlocked", "position": {"x": 0, "y": 0}, "is_locked": False}],
    )
    frame = next(n for n in document.nodes.values() if n.kind == "frame")
    assert frame.state.is_locked is False


# Every frame payload below sits a single member at the origin, whose padded
# estimated footprint is therefore (-40, -50) to (260, 210) - GROUP_PADDING 40
# / GROUP_PADDING_TOP 50 around GROUP_MEMBER_DEFAULT_WIDTH/HEIGHT 220x120
# (backend/domain/model.py). Saved rects here deliberately ENCLOSE that box,
# which is the ordinary case: a real saved frame contains its own members. The
# union test immediately below covers the case where it does not.


def test_frame_position_is_restored_from_rect_as_a_manual_anchor():
    """The saved rect's x/y is the frame's real position; create_frame's own
    bbox-of-members placement (from 220x120 member ESTIMATES) is not. It
    must land as a manual anchor, since that is the only form
    _recompute_group_bounds preserves."""
    document = _restore(
        nodes=[_chat("a")],
        frames=[{"items": [0], "note": "Moved", "position": {"x": -900, "y": -400},
                 "rect": {"x": -900, "y": -400, "width": 1400, "height": 900}}],
    )
    frame = next(n for n in document.nodes.values() if n.kind == "frame")
    assert (frame.state.group_manual_x, frame.state.group_manual_y) == (-900.0, -400.0)
    assert (frame.x, frame.y) == (-900.0, -400.0)


def test_frame_saved_rect_still_grows_to_enclose_a_member_outside_it():
    """The restored anchor is UNIONED with live content, never substituted
    for it - the same no-clip guarantee a live drag gets. Here the saved
    rect starts below its own member (only reachable via a hand-edited or
    cross-version payload), so the frame must grow up to re-enclose it
    rather than restore a rect that visually cuts the member off."""
    document = _restore(
        nodes=[_chat("a")],
        frames=[{"items": [0], "note": "Stale rect", "position": {"x": -500, "y": 250},
                 "rect": {"x": -500, "y": 250, "width": 800, "height": 700}}],
    )
    frame = next(n for n in document.nodes.values() if n.kind == "frame")
    member = document.nodes[frame.item_ids[0]]
    assert frame.x <= member.x and frame.y <= member.y
    assert frame.y == -50.0  # grown to the member's own padded top edge


def test_frame_position_falls_back_to_the_position_key_when_rect_has_no_xy():
    """The older "size"-only shape carries no rect x/y at all, but every era
    of the payload has a top-level "position"."""
    document = _restore(
        nodes=[_chat("a")],
        frames=[{"items": [0], "note": "Old shape", "position": {"x": -640, "y": -300},
                 "size": {"width": 1000, "height": 800}}],
    )
    frame = next(n for n in document.nodes.values() if n.kind == "frame")
    assert (frame.x, frame.y) == (-640.0, -300.0)


def test_frame_with_no_position_information_keeps_its_computed_placement():
    """Nothing to restore from - the bbox-of-members placement create_frame
    already produced stands, and no anchor is invented for it."""
    document = _restore(
        nodes=[_chat("a")],
        frames=[{"items": [0], "note": "No position"}],
    )
    frame = next(n for n in document.nodes.values() if n.kind == "frame")
    assert frame.state.group_manual_x is None and frame.state.group_manual_y is None


def test_collapsed_frame_restores_at_its_saved_position():
    """The collapse branch of _recompute_group_bounds snaps the size and
    leaves x/y alone, so the position must be restored before it runs."""
    document = _restore(
        nodes=[_chat("a")],
        frames=[{"items": [0], "note": "Collapsed", "position": {"x": -500, "y": -400},
                 "rect": {"x": -500, "y": -400, "width": 900, "height": 800},
                 "is_collapsed": True}],
    )
    frame = next(n for n in document.nodes.values() if n.kind == "frame")
    assert frame.is_collapsed is True
    assert (frame.x, frame.y) == (-500.0, -400.0)


# -- containers (nest frames/notes/charts, offset math) ----------------------


def test_container_default_title_matches_legacy_restore_time_default():
    document = _restore(
        nodes=[_chat("a")],
        containers=[{"items": [0]}],
    )
    container = next(n for n in document.nodes.values() if n.kind == "container")
    assert container.content == "Container"


def test_container_offsets_survive_a_skipped_node_bug_47_regression():
    # 3 node payloads; position 1 has a bad parent and gets skipped entirely.
    # notes/charts/frames offsets must be computed from the ORIGINAL 3-slot
    # node count, not from the 2 nodes that actually survived - otherwise the
    # note (originally at combined-space position node_slot_count+0 = 3)
    # would be misread as combined-space position 2, silently attaching the
    # container to the wrong item.
    document = _restore(
        nodes=[
            _chat("a"),
            {"node_type": "code", "code": "x", "language": "python",
             "position": {"x": 0, "y": 0}, "parent_content_node_index": 99},  # skipped
            _chat("c"),
        ],
        notes=[{"content": "the note", "position": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1},
                "color": "#fff", "header_color": None}],
        containers=[{"items": [3]}],  # node_slot_count(3) + note_index(0) = 3
    )
    assert len(document.nodes) == 4  # 2 chats + 1 note + 1 container
    container = next(n for n in document.nodes.values() if n.kind == "container")
    note = next(n for n in document.nodes.values() if n.kind == "note")
    assert container.item_ids == [note.id]


def test_container_can_reference_a_frame_via_the_full_offset_chain():
    document = _restore(
        nodes=[_chat("a"), _chat("b")],
        frames=[{"items": [0, 1], "note": "F", "position": {"x": 0, "y": 0}}],
        # node_slot_count(2) + note_slot_count(0) + chart_slot_count(0) + frame_index(0) = 2
        containers=[{"items": [2]}],
    )
    container = next(n for n in document.nodes.values() if n.kind == "container")
    frame = next(n for n in document.nodes.values() if n.kind == "frame")
    assert container.item_ids == [frame.id]


def test_nested_containers_restore_when_outer_payload_precedes_inner_dependency():
    document = _restore(
        nodes=[_chat("leaf")],
        containers=[
            # Container slots start after the one regular-node slot. The
            # outer payload is deliberately first but references slot 2,
            # occupied by the inner payload below, so restoration requires
            # the loader's deferred dependency pass rather than list order.
            {"items": [2], "title": "Outer"},
            {"items": [0], "title": "Inner"},
        ],
    )

    leaf = next(node for node in document.nodes.values() if node.kind == "chat")
    inner = next(node for node in document.nodes.values() if node.content == "Inner")
    outer = next(node for node in document.nodes.values() if node.content == "Outer")
    assert inner.item_ids == [leaf.id]
    assert outer.item_ids == [inner.id]


# -- basic connections (12 keys), system-prompt / group-summary -------------


def test_basic_connection_resolves_by_index():
    document = _restore(
        nodes=[_chat("a"), _chat("b")],
        connections=[{"start_node_index": 0, "end_node_index": 1}],
    )
    a, b = list(document.nodes.values())
    assert any(e.source == a.id and e.target == b.id for e in document.edges.values())


def test_basic_connection_resolves_by_id_when_index_is_wrong():
    document = _restore(
        nodes=[_chat("a"), _chat("b")],
        content_connections=[{"start_node_index": 99, "start_node_id": "a", "end_node_index": 99, "end_node_id": "b"}],
    )
    next(n for n in document.nodes.values() if n.content == "hi")
    assert len(document.edges) == 1


def test_plugin_era_connection_keys_still_resolve():
    # pycoder_connections/web_connections/etc only existed before R5-closeout
    # deleted those node kinds, but the connection can still legitimately
    # link two SURVIVING node kinds from an old save.
    document = _restore(
        nodes=[_chat("a"), _chat("b")],
        pycoder_connections=[{"start_node_index": 0, "end_node_index": 1}],
        gitlink_connections=[{"start_node_index": 1, "end_node_index": 0}],
    )
    assert len(document.edges) == 2


def test_system_prompt_connection_resolves_chat_node_via_chat_payload_ordinal():
    document = _restore(
        nodes=[
            {"node_type": "document", "title": "t", "content": "c", "position": {"x": 0, "y": 0},
             "parent_content_node_index": 99},  # skipped - shifts chat ordinal test
            _chat("only-chat"),
        ],
        notes=[{"content": "sp", "position": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1},
                "color": "#fff", "header_color": None, "is_system_prompt": True}],
        system_prompt_connections=[{"start_note_index": 0, "end_node_index": 0}],
    )
    note = next(n for n in document.nodes.values() if n.kind == "note")
    chat = next(n for n in document.nodes.values() if n.kind == "chat")
    assert any(e.source == note.id and e.target == chat.id for e in document.edges.values())


def test_group_summary_connection_chat_to_note():
    document = _restore(
        nodes=[_chat("only-chat")],
        notes=[{"content": "summary", "position": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1},
                "color": "#fff", "header_color": None, "is_summary_note": True}],
        group_summary_connections=[{"start_node_index": 0, "end_note_index": 0}],
    )
    note = next(n for n in document.nodes.values() if n.kind == "note")
    chat = next(n for n in document.nodes.values() if n.kind == "chat")
    assert any(e.source == chat.id and e.target == note.id for e in document.edges.values())


def test_malformed_connection_id_is_skipped_without_aborting_the_rest_of_the_load():
    # A hand-edited/imported save with a non-scalar start_node_id (valid
    # JSON, wrong shape) must not raise TypeError out of _resolve_ref and
    # abort the whole load - only that one malformed entry is dropped, the
    # next (well-formed) entry in the same list still restores.
    document = _restore(
        nodes=[_chat("a"), _chat("b"), _chat("c")],
        connections=[
            {"start_node_id": ["nested", "list"], "end_node_id": "b"},
            {"start_node_index": 0, "end_node_index": 2},
        ],
    )
    a, b, c = list(document.nodes.values())
    assert len(document.edges) == 1
    assert any(e.source == a.id and e.target == c.id for e in document.edges.values())


# -- pins / view state / tokens ------------------------------------------


def test_pins_restore_via_navigation_pin_record():
    document = _restore(pins=[
        {"pin_id": "pin-1", "title": "My Pin", "note": "n", "position": {"x": 1.0, "y": 2.0},
         "anchor_item_id": None, "sort_order": 0, "created_at": "2026-01-01 00:00:00"},
    ])
    assert len(document.pins.records) == 1
    assert document.pins.records[0].title == "My Pin"


def test_view_state_and_total_session_tokens_restore():
    document = _restore(
        view_state={"zoom_factor": 2.0, "scroll_position": {"x": 100, "y": 200}},
        total_session_tokens=555,
    )
    assert document.zoom_factor == 2.0
    assert (document.scroll_x, document.scroll_y) == (100.0, 200.0)
    assert document.total_session_tokens == 555


# -- top-level orchestrator contract -----------------------------------------


def test_restore_chat_into_document_raises_for_non_mapping_chat():
    document = SceneDocument()
    try:
        restore_chat_into_document(document, "not-a-dict", [], [])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_restore_clears_prior_document_state_first():
    document = SceneDocument()
    document.add_node(0, 0, "stale")
    restore_chat_into_document(document, {"data": {"nodes": [_chat("fresh")]}}, [], [])
    assert len(document.nodes) == 1
    assert next(iter(document.nodes.values())).content == "hi"


def test_resolve_node_payload_list_tolerates_legacy_items_key():
    document = SceneDocument()
    restore_chat_into_document(document, {"data": {"items": [_chat("legacy-shape")]}}, [], [])
    assert len(document.nodes) == 1


def test_resolve_node_payload_list_tolerates_double_nested_legacy_shape():
    document = SceneDocument()
    restore_chat_into_document(document, {"data": {"data": {"nodes": [_chat("nested")]}}}, [], [])
    assert len(document.nodes) == 1


# -- ADR-002 Workstream 1: "Branch status and lifecycle" ---------------------
#
# Includes the confirmed, pre-existing gap fixed inline in this same pass:
# provider/model/is_branch_synthesis/synthesis_instructions/item_ids
# (Synthesize Branches) and is_branch_comparison/item_ids (Compare Branches)
# already synced live to the frontend but were silently dropped on load
# before this fix - see backend/session_save.py's own comment on the
# matching serializers.


def test_restore_chat_payload_restores_synthesis_provenance_scalars():
    document = _restore(nodes=[
        _chat(
            "n0", raw_content="Combined answer", is_user=False,
            provider="Anthropic Claude", model="claude-sonnet-5",
            is_branch_synthesis=True, synthesis_instructions="merge them",
            branch_status="accepted",
        ),
    ])
    node = next(iter(document.nodes.values()))
    assert node.state.provider == "Anthropic Claude"
    assert node.state.model == "claude-sonnet-5"
    assert node.state.is_branch_synthesis is True
    assert node.state.synthesis_instructions == "merge them"
    assert node.state.branch_status == "accepted"


def test_restore_chat_payload_downgrades_an_unrecognized_branch_status_to_active():
    document = _restore(nodes=[_chat("n0", branch_status="archived")])
    node = next(iter(document.nodes.values()))
    assert node.state.branch_status == "active"


def test_restore_chat_payload_restores_a_model_override_pin():
    # ADR-018 stage 18.3.
    document = _restore(nodes=[
        _chat("n0", override_provider="Anthropic Claude", override_model_id="claude-opus-5"),
    ])
    node = next(iter(document.nodes.values()))
    assert node.state.override_provider == "Anthropic Claude"
    assert node.state.override_model_id == "claude-opus-5"


def test_restore_chat_payload_defaults_branch_status_to_active_when_absent():
    document = _restore(nodes=[_chat("n0")])
    node = next(iter(document.nodes.values()))
    assert node.state.branch_status == "active"


def test_restore_notes_restores_is_branch_comparison():
    document = _restore(notes=[
        {"content": "cmp", "position": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1},
         "is_branch_comparison": True},
    ])
    note = next(iter(document.nodes.values()))
    assert note.state.is_branch_comparison is True


def test_restore_translates_a_synthesis_nodes_item_ids_to_the_re_minted_source_ids():
    document = _restore(nodes=[
        _chat("src-1", raw_content="first branch reply"),
        _chat("src-2", raw_content="second branch reply"),
        _chat(
            "result", raw_content="Combined answer", is_user=False,
            is_branch_synthesis=True, item_ids=["src-1", "src-2"],
        ),
    ])
    src1 = next(n for n in document.nodes.values() if n.content == "first branch reply")
    src2 = next(n for n in document.nodes.values() if n.content == "second branch reply")
    result = next(n for n in document.nodes.values() if n.content == "Combined answer")
    assert set(result.item_ids) == {src1.id, src2.id}
    assert src1.id != "src-1", "ids must be re-minted on load, not reused verbatim - a weak assertion here would miss a translation bug"


def test_restore_translates_a_comparison_notes_item_ids_to_the_re_minted_source_ids():
    document = _restore(
        nodes=[
            _chat("src-1", raw_content="first branch reply"),
            _chat("src-2", raw_content="second branch reply"),
        ],
        notes=[
            {"content": "Branch Comparison", "position": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1},
             "is_branch_comparison": True, "item_ids": ["src-1", "src-2"]},
        ],
    )
    src1 = next(n for n in document.nodes.values() if n.content == "first branch reply")
    src2 = next(n for n in document.nodes.values() if n.content == "second branch reply")
    note = next(n for n in document.nodes.values() if n.kind == "note")
    assert set(note.item_ids) == {src1.id, src2.id}


def test_restore_drops_a_synthesis_item_id_that_does_not_resolve_to_any_node():
    document = _restore(nodes=[
        _chat(
            "result", raw_content="Combined answer", is_user=False,
            is_branch_synthesis=True, item_ids=["deleted-before-save", "also-missing"],
        ),
    ])
    result = next(iter(document.nodes.values()))
    assert result.item_ids == []


def test_restore_only_translates_item_ids_for_a_flagged_synthesis_or_comparison_node():
    # An ordinary chat/note node's item_ids (unset, empty by default) must
    # not be touched by the second-pass translation just because OTHER
    # nodes in the same load happen to be flagged.
    document = _restore(
        nodes=[
            _chat("src-1", raw_content="source"),
            _chat("ordinary", raw_content="just a normal reply"),
            _chat(
                "result", raw_content="Combined answer", is_user=False,
                is_branch_synthesis=True, item_ids=["src-1"],
            ),
        ],
    )
    ordinary = next(n for n in document.nodes.values() if n.content == "just a normal reply")
    assert ordinary.item_ids == []


def test_restore_translates_final_deliverable_node_id():
    # "legacy-uuid-1234" (not "n0"/"n1"/...) is deliberate: a fresh
    # SceneDocument's own id counter also happens to mint "n0" first, so a
    # payload id of "n0" would coincidentally match the real new id even if
    # translation were silently broken - this id can never collide.
    document = _restore(
        nodes=[_chat("legacy-uuid-1234", raw_content="the deliverable")],
        final_deliverable_node_id="legacy-uuid-1234",
    )
    node = next(iter(document.nodes.values()))
    assert document.final_deliverable_node_id == node.id
    assert node.id != "legacy-uuid-1234", "ids must be re-minted on load - a weak assertion here would miss a translation bug"


def test_restore_final_deliverable_node_id_stale_reference_is_dropped():
    document = _restore(nodes=[_chat("n0")], final_deliverable_node_id="does-not-exist")
    assert document.final_deliverable_node_id is None


def test_restore_final_deliverable_node_id_rejects_a_resolved_non_chat_node():
    # Found by adversarial review: the live setter (SceneDocument.
    # set_final_deliverable) rejects non-chat nodes; this defends the same
    # invariant against a malformed/hand-edited save file that references
    # one, which this load path has no SceneError to raise against.
    document = _restore(
        nodes=[
            _chat("parent"),
            {"node_type": "code", "id": "code-1", "code": "print(1)", "language": "python",
             "position": {"x": 0, "y": 0}, "parent_content_node_index": 0},
        ],
        final_deliverable_node_id="code-1",
    )
    assert document.final_deliverable_node_id is None


# -- SECURITY-FIX: a persisted audio_file part is an arbitrary local path the
# -- provider layer would read and upload; neutralized on load ----------------


def test_restored_audio_file_part_is_neutralized_not_left_as_a_readable_path():
    """A hostile saved chat carrying {"type":"audio_file","path":<any local
    file>} is invisible in the UI and would be read + uploaded to the model
    provider on the next turn. Restore must strip the path to an inert
    placeholder."""
    document = _restore(nodes=[{
        "node_type": "chat",
        "raw_content": [
            {"type": "text", "text": "look at this"},
            {"type": "audio_file", "path": "C:/Users/victim/.ssh/id_rsa"},
        ],
        "position": {"x": 0, "y": 0},
    }])
    node = next(iter(document.nodes.values()))
    parts = node.state.content_parts
    assert all(p.get("type") != "audio_file" for p in parts), "the audio_file path must not survive load"
    serialized = repr(parts)
    assert "id_rsa" not in serialized and ".ssh" not in serialized, "the arbitrary path must be gone entirely"
    # The ordinary text part is untouched.
    assert any(p.get("type") == "text" and p.get("text") == "look at this" for p in parts)


def test_restored_image_bytes_part_still_round_trips_unaffected_by_the_audio_fix():
    import base64
    from backend.domain.content_codec import _content_codec

    raw = _content_codec.process_content_for_deserialization([
        {"type": "image_bytes", "data": base64.b64encode(b"PNGDATA").decode("ascii")},
    ])
    assert raw[0]["type"] == "image_bytes"
    assert raw[0]["data"] == b"PNGDATA"


# -- SECURITY-FIX: non-finite floats from a hostile saved chat must not reach
# -- the live document (they'd emit NaN/Infinity tokens that wedge the wire) --


def test_non_finite_position_is_sanitized_on_load_not_carried_into_the_document():
    import json
    import math

    # json.loads accepts these non-standard literals by default.
    raw = json.loads('{"x": NaN, "y": Infinity}')
    document = _restore(nodes=[{
        "node_type": "chat", "raw_content": "hi", "position": raw,
    }])
    node = next(iter(document.nodes.values()))
    assert math.isfinite(node.x) and math.isfinite(node.y), (
        "a non-finite coordinate must be coerced to a finite default on load"
    )


def test_a_chat_with_non_finite_values_still_serializes_to_valid_json_for_the_wire():
    import json

    document = _restore(
        nodes=[{"node_type": "chat", "raw_content": "hi",
                "position": json.loads('{"x": NaN, "y": -Infinity}')}],
        view_state={"zoom_factor": float("inf"), "scroll_x": float("nan"), "scroll_y": 0.0},
    )
    # Exactly what starlette's send_json does (separators + default allow_nan
    # would emit bare NaN/Infinity tokens the SPA's JSON.parse rejects).
    wire = json.dumps(document.scene_payload(), separators=(",", ":"), allow_nan=False)
    # allow_nan=False raises if any non-finite survived - reaching here proves
    # none did. Belt-and-suspenders: no literal token slipped through either.
    assert "NaN" not in wire and "Infinity" not in wire
