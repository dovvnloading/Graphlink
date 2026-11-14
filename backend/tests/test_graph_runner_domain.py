"""The branch preview must predict the input every individual write will see."""

import pytest

import backend.agents  # noqa: F401 - initialize the ordinary canvas import graph
from backend.domain.graph import SceneDocument
from backend.domain.graph_runner import execute_graph_step, plan_graph_run, validate_graph_run_ancestors
from backend.domain.model import SceneError
from backend.domain.nodes_content import _text_digest


def test_selected_note_uses_ancestors_and_descendants_without_running_ancestor_siblings():
    doc = SceneDocument()
    source = doc.add_chat_node(0, 0, "three short words", True)
    leaf, selected, upstream, sibling = [doc.add_note(0, 0) for _ in range(4)]
    doc.bind_note_text_input(source.id, upstream.id)
    doc.bind_note_text_input(upstream.id, selected.id, "word_count")
    doc.bind_note_text_input(upstream.id, sibling.id)
    doc.bind_note_text_input(selected.id, leaf.id)
    doc.connect(selected.id, source.id)  # Ordinary reverse-direction edges are irrelevant.

    plan = plan_graph_run(doc, [selected.id])
    assert [step["nodeId"] for step in plan] == [upstream.id, selected.id, leaf.id]
    assert [step["status"] for step in plan] == ["ready"] * 3
    assert all(node.content == "Add note..." for node in (upstream, selected, leaf, sibling))
    with pytest.raises(SceneError, match="input changed"):
        execute_graph_step(doc, plan[-1])
    assert [execute_graph_step(doc, step) for step in plan] == [
        _text_digest("three short words"), _text_digest("3"), _text_digest("3"),
    ]
    assert (upstream.content, selected.content, leaf.content, sibling.content) == ("three short words", "3", "3", "Add note...")
    assert [step["status"] for step in plan_graph_run(doc, [leaf.id])] == ["current"] * 3

    source.content = "three fresh words"  # New input, same numeric output downstream.
    assert [step["status"] for step in plan_graph_run(doc, [selected.id])] == ["ready", "ready", "current"]
    assert {step["nodeId"] for step in plan_graph_run(doc, [source.id])} == {leaf.id, upstream.id, selected.id, sibling.id}


def test_report_to_text_chain_blocks_local_errors_and_can_keep_edited_current_reports():
    doc = SceneDocument()
    source = doc.add_table(0, 0, "group,sales,stock\nNorth,9,4")
    report = doc.create_table_output(source.id, "artifact", "group", "sales")
    chart = doc.create_table_output(source.id, "chart", "group", "stock")
    note = doc.add_note(0, 0)
    doc.bind_note_text_input(report.id, note.id)
    initial = report.state.artifact_content

    doc.set_table_source(source.id, "group,sales,stock\nNorth,broken,6", source.content)
    plan = {step["nodeId"]: step for step in plan_graph_run(doc, [])}
    assert {key: step["status"] for key, step in plan.items()} == {report.id: "blocked", chart.id: "ready", note.id: "blocked"}
    assert "finite number" in plan[report.id]["detail"]
    assert "upstream" in plan[note.id]["detail"]
    assert report.state.artifact_content == initial
    assert chart.state.table_output.current.version == 1
    execute_graph_step(doc, plan[chart.id])
    assert chart.state.chart_data["values"] == [6]

    doc.set_table_source(source.id, "group,sales,stock\nNorth,12,6", source.content)
    plan = plan_graph_run(doc, [note.id])
    assert [step["status"] for step in plan] == ["ready", "ready"]
    for step in plan:
        execute_graph_step(doc, step)
    assert note.content == report.state.artifact_content
    assert "12" in note.content

    report.state.artifact_content = "Edited by hand"
    plan = plan_graph_run(doc, [note.id])
    assert [step["status"] for step in plan] == ["current", "ready"]
    execute_graph_step(doc, plan[1])
    assert note.content == "Edited by hand"
    doc.set_table_source(source.id, "group,sales,stock\nNorth,13,6", source.content)
    assert [step["status"] for step in plan_graph_run(doc, [note.id])] == ["blocked", "blocked"]


@pytest.mark.parametrize("changed", ["source", "destination", "operation", "connection", "missing"])
def test_changes_after_preview_are_rejected_before_overwrite(changed):
    doc = SceneDocument()
    source, target, other = [doc.add_note(0, 0) for _ in range(3)]
    source.content = other.content = "same content"
    doc.bind_note_text_input(source.id, target.id)
    step, = plan_graph_run(doc, [])
    if changed == "source":
        source.content = "new source content"
    elif changed == "destination":
        target.content = "manual edit"
    elif changed == "operation":
        doc.bind_note_text_input(source.id, target.id, "word_count")
    elif changed == "connection":
        doc.unbind_note_text_input(target.id)
        doc.bind_note_text_input(other.id, target.id)
    else:
        doc.remove_nodes([target.id])
    with pytest.raises(SceneError, match="changed"):
        execute_graph_step(doc, step)
    assert target.content == ("manual edit" if changed == "destination" else "Add note...")


def test_unrelated_table_columns_do_not_invalidate_projection_or_planned_run():
    doc = SceneDocument()
    source = doc.add_table(0, 0, "k,x,y\na,1,2")
    output = doc.create_table_output(source.id, "chart", "k", "x")
    doc.set_table_source(source.id, "k,x,y\na,3,2", source.content)
    step, = plan_graph_run(doc, [])
    doc.set_table_source(source.id, "k,x,y\na,3,4", source.content)
    execute_graph_step(doc, step)
    assert output.state.chart_data["values"] == [3]
    assert plan_graph_run(doc, [output.id])[0]["status"] == "current"


def test_malformed_cycles_do_not_stop_disconnected_branches():
    doc = SceneDocument()
    a, b, leaf, src, other = [doc.add_note(0, 0) for _ in range(5)]
    doc.bind_note_text_input(a.id, b.id)
    doc.bind_note_text_input(b.id, leaf.id)
    a.state.text_input_source_id = b.id  # Simulate an invalid persisted binding.
    doc.bind_note_text_input(src.id, other.id)
    plan = plan_graph_run(doc, [])
    assert [step["nodeId"] for step in plan if step["status"] == "ready"] == [other.id]
    assert all("cycle" in step["detail"] for step in plan if step["status"] == "blocked")
    with pytest.raises(SceneError, match="selected node"):
        plan_graph_run(doc, ["missing"])


@pytest.mark.parametrize("cached", [True, False])
def test_resume_checks_transitive_input_pins_even_when_intermediate_content_still_matches(cached):
    doc = SceneDocument()
    raw, upstream, downstream = [doc.add_note(0, 0) for _ in range(3)]
    doc.bind_note_text_input(raw.id, upstream.id)
    doc.bind_note_text_input(upstream.id, downstream.id)
    if cached:
        doc.run_note_text_input(upstream.id)
    first, second = plan_graph_run(doc, [downstream.id])
    if not cached:
        first["outputHash"] = execute_graph_step(doc, first)
        first["status"] = "completed"
    steps = {upstream.id: first, downstream.id: second}
    validate_graph_run_ancestors(doc, second, steps)
    raw.content = "changed while paused"
    assert _text_digest(upstream.content) == second["inputHash"]
    with pytest.raises(SceneError, match="upstream input"):
        validate_graph_run_ancestors(doc, second, steps)


def test_grouped_table_feeds_two_consumers_and_preserves_committed_versions():
    doc = SceneDocument()
    source = doc.add_table(0, 0, 'Team,Cost,Notes\n"A,B",2,first\nN,1,second\n"A,B",3,third')
    grouped = doc.create_table_output(source.id, "table", "Team", "Cost")
    chart = doc.create_table_output(grouped.id, "chart", "Team", "Cost")
    report = doc.create_table_output(grouped.id, "artifact", "Team", "Cost")
    assert grouped.content == 'Team,Cost\r\n"A,B",5\r\nN,1'
    assert doc._node_wire(grouped)["tableInputSourceId"] == source.id

    doc.set_table_source(source.id, source.content.replace(",3,third", ",9,third"), source.content)
    plan = plan_graph_run(doc, [grouped.id])
    assert [(s["kind"], s["status"]) for s in plan] == [("table", "ready"), ("chart", "ready"), ("artifact", "ready")]
    for step in plan:
        execute_graph_step(doc, step)
    assert chart.state.chart_data["values"] == [11, 1]
    assert "11" in report.state.artifact_content
    history = doc.get_table_output_history(grouped.id)
    assert history["current"]["csv"] == grouped.content
    assert history["previous"]["csv"] == 'Team,Cost\r\n"A,B",5\r\nN,1'
    doc.restore_table_output(grouped.id, history["revisionToken"])
    assert grouped.content == history["previous"]["csv"]
    assert doc._node_wire(chart)["tableInputStatus"] == "stale"

    # Hand changes to the destination CSV have the ordinary output overwrite protection.
    doc.set_table_source(grouped.id, "Team,Cost\nA,100", grouped.content)
    assert all(s["status"] == "blocked" for s in plan_graph_run(doc, [source.id]))
    assert doc._node_wire(grouped)["tableOutputEdited"] is True
    doc.record_command("removeNodes", "user", lambda: doc.remove_nodes([source.id]), node_ids=[source.id])
    assert not grouped.state.table_output.source_id
    doc.undo()
    assert doc.nodes[grouped.id].state.table_output.source_id == source.id


def test_grouped_table_versions_and_both_bindings_remap_through_graph_save_and_reopen(tmp_path):
    from backend.chat_library import load_chat_snapshot, save_chat_atomically_row
    from backend.session_load import restore_chat_into_document
    from backend.session_save import build_chat_data

    doc = SceneDocument()
    unused = doc.add_note(0, 0)
    doc.remove_nodes([unused.id])  # Force the reopened scene's IDs to differ.
    raw = doc.add_table(0, 0, "Category,Qty\nA,2\nA,3")
    grouped = doc.create_table_output(raw.id, "table", "Category", "Qty")
    chart = doc.create_table_output(grouped.id, "chart", "Category", "Qty")
    doc.set_table_source(raw.id, "Category,Qty\nA,2\nA,4", raw.content)
    for step in plan_graph_run(doc, [chart.id]):
        execute_graph_step(doc, step)
    history = doc.get_table_output_history(grouped.id)
    data = build_chat_data(doc)
    notes, pins = data.pop("notes_data"), data.pop("pins_data")
    db = tmp_path / "tables.db"
    saved, _ = save_chat_atomically_row(db, None, "Grouped table", data, notes, pins)
    reopened = SceneDocument()
    restore_chat_into_document(reopened, *load_chat_snapshot(db, saved))

    reopened_group, reopened_chart = plan_graph_run(reopened, [])
    assert [reopened_group["status"], reopened_chart["status"]] == ["current", "current"]
    assert reopened_group["nodeId"] != grouped.id
    assert reopened_chart["sourceId"] == reopened_group["nodeId"]
    assert reopened.get_table_output_history(reopened_group["nodeId"]) == history
    assert reopened.nodes[reopened_chart["nodeId"]].state.chart_data["values"] == [6]


def test_unchanged_group_totals_skip_consumers_and_missing_columns_block_only_their_branches():
    doc = SceneDocument()
    raw = doc.add_table(0, 0, "Team,Hours,Cost\nA,1,10\nA,2,11")
    grouped = doc.create_table_output(raw.id, "table", "Team", "Hours")
    chart = doc.create_table_output(grouped.id, "chart", "Team", "Hours")
    report = doc.create_table_output(raw.id, "artifact", "Team", "Cost")
    doc.set_table_source(raw.id, "Team,Hours\nA,0\nA,3", raw.content)
    plan = {step["nodeId"]: step for step in plan_graph_run(doc, [raw.id])}
    assert plan[grouped.id]["status"] == "ready"
    assert plan[chart.id]["status"] == "current"
    assert plan[report.id]["status"] == "blocked" and "Cost" in plan[report.id]["detail"]
    execute_graph_step(doc, plan[grouped.id])
    assert doc.get_table_output_history(grouped.id)["current"]["version"] == 2
    assert doc.get_table_output_history(chart.id)["current"]["version"] == 1


def test_grouped_csv_preserves_exact_decimals_above_json_number_precision():
    doc = SceneDocument()
    raw = doc.add_table(0, 0, "Group,Value\nlarge,9007199254740992\nlarge,1\nsmall,0.10\nsmall,0.20")
    grouped = doc.create_table_output(raw.id, "table", "Group", "Value")
    next_grouped = doc.create_table_output(grouped.id, "table", "Group", "Value")
    assert next_grouped.content == grouped.content == "Group,Value\r\nlarge,9007199254740993\r\nsmall,0.3"
    with pytest.raises(SceneError, match="precision of a chart number"):
        doc.create_table_output(grouped.id, "chart", "Group", "Value")
    doc.set_table_source(raw.id, "Group,Value\nlarge,1e700\nlarge,1", raw.content)
    steps = plan_graph_run(doc, [grouped.id])
    assert [s["status"] for s in steps] == ["blocked", "blocked"]
    assert "decimal precision" in steps[0]["detail"]
