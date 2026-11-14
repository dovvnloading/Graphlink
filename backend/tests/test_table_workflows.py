"""Executable CSV tables, derived outputs and their durable revision history."""

import pytest

import backend.agents  # noqa: F401 - use the normal canvas/agent import order
import backend.domain.table_workflows as tables
from backend.chat_library import get_all_workspaces, import_workspace_rows_atomically, load_chat_snapshot, save_chat_atomically_row
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.workspace_archive import export_archive


def test_selected_columns_drive_independent_consumers_and_handle_reordering_and_removal():
    doc = SceneDocument()
    source = doc.add_table(0, 0, " Team ,Hours,Cost\nBeta,0.1,30\nAlpha,4,20\nBeta,0.2,15")
    chart = doc.create_table_output(source.id, "chart", "Team", "Hours")
    report = doc.create_table_output(source.id, "artifact", "Team", "Cost")
    assert chart.state.chart_data["labels"] == ["Beta", "Alpha"]
    assert chart.state.chart_data["values"] == [0.3, 4]
    assert chart.x >= source.x + 350 + 80
    assert not doc.edges
    assert "| ` Beta ` | 45 |" in report.state.artifact_content
    doc.set_table_source(source.id, "Cost,Hours,Team\n30,0.1,Beta\n20,4,Alpha\n16,0.2,Beta", source.content)
    assert doc._node_wire(chart)["tableInputStatus"] == "current"
    assert doc._node_wire(report)["tableInputStatus"] == "stale"
    doc.run_table_output(chart.id)
    assert doc.get_table_output_history(chart.id)["current"]["version"] == 1
    doc.run_table_output(report.id)
    history = doc.get_table_output_history(report.id)
    assert "| ` Beta ` | 46 |" in history["current"]["markdown"]
    assert "| ` Beta ` | 45 |" in history["previous"]["markdown"]
    doc.set_table_source(source.id, "Team,Cost\nBeta,16", source.content)
    assert doc._node_wire(chart)["tableInputStatus"] == "stale"
    with pytest.raises(SceneError, match="Hours.*missing"):
        doc.run_table_output(chart.id)
    assert chart.state.chart_data["values"] == [0.3, 4]


@pytest.mark.parametrize("text", [
    "A,A\n1,2", "A,\n1,2", "A,B\n1", "A,B", 'A,B\n1,"unclosed', "one column\n1",
    "A,B\n1,2\n\n", "A,B\x00\n1,2", "A,B\n" + "x" * tables.MAX_TABLE_CHARS,
])
def test_invalid_csv_never_creates_or_modifies_a_table(text):
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_table(0, 0, text)
    assert not doc.nodes
    source = doc.add_table(0, 0, 'Label,Value\n"new\nline",1')
    before = source.content
    with pytest.raises(SceneError):
        doc.set_table_source(source.id, text, before)
    assert source.content == before


def test_text_input_and_ordinary_note_mutations_cannot_corrupt_a_table():
    doc = SceneDocument()
    table = doc.add_table(0, 0, "Team,Cost\nOne,7")
    plain = doc.add_note(0, 0)
    with pytest.raises(SceneError):
        doc.set_note_content(table.id, "invalid")
    with pytest.raises(SceneError):
        doc.bind_note_text_input(plain.id, table.id)
    with pytest.raises(SceneError):
        doc.bind_note_text_input(table.id, plain.id)
    with pytest.raises(SceneError):
        doc.mark_branch_comparison_note(table.id, [])
    with pytest.raises(SceneError):
        doc.set_table_source(table.id, "Team,Cost\nOne,8", "stale value")
    assert not plain.state.text_input_source_id
    assert table.content.endswith("One,7")


@pytest.mark.parametrize("value", ["nine", "NaN", "Infinity", "1e400", "1e-450", "9007199254740993"])
def test_invalid_numeric_cells_do_not_create_or_overwrite_results(value):
    doc = SceneDocument()
    source = doc.add_table(0, 0, "Team,Cost\nbilling,1")
    chart = doc.create_table_output(source.id, "chart", "Team", "Cost")
    before = doc.get_table_output_history(chart.id)
    doc.set_table_source(source.id, f"Team,Cost\nbilling,{value}", source.content)
    with pytest.raises(SceneError, match="Data row 1"):
        doc.run_table_output(chart.id)
    with pytest.raises(SceneError):
        doc.create_table_output(source.id, "artifact", "Team", "Cost")
    assert len(doc.nodes) == 2
    assert doc.get_table_output_history(chart.id) == before


def test_reports_present_literal_csv_text_and_do_not_inherit_chart_point_limit():
    doc = SceneDocument()
    source = doc.add_table(0, 0, "Group,Value\n"
                           '"><img src=x> | ![logo](https://example.com/z) ``*",1\n' +
                           "\n".join(f"group{i},1" for i in range(310)))
    report = doc.create_table_output(source.id, "artifact", "Group", "Value")
    assert "` ><img src=x>  `&#124;```  ![logo](https://example.com/z) ``* ```" in report.state.artifact_content
    assert tables._markdown_literal(r"a\|b") == r"` a\ `&#124;` b `"
    assert "group309" in report.state.artifact_content
    with pytest.raises(SceneError, match="data points"):
        doc.create_table_output(source.id, "chart", "Group", "Value")


def test_versus_manual_edit_pending_proposal_and_stale_revision_token():
    doc = SceneDocument()
    source = doc.add_table(0, 0, "Group,Cost\nA,1")
    report = doc.create_table_output(source.id, "artifact", "Group", "Cost")
    doc.set_table_source(source.id, "Group,Cost\nA,2", source.content)
    doc.run_table_output(report.id)
    history = doc.get_table_output_history(report.id)
    doc.set_artifact_content(report.id, "human edits", report.state.artifact_content)
    assert doc._node_wire(report)["tableOutputEdited"]
    with pytest.raises(SceneError, match="edited"):
        doc.restore_table_output(report.id, history["revisionToken"])
    with pytest.raises(SceneError, match="edited"):
        doc.run_table_output(report.id)
    doc.set_artifact_content(report.id, history["current"]["markdown"], "human edits")
    report.state.artifact_proposal_id = "pending-candidate"
    with pytest.raises(SceneError, match="pending"):
        doc.restore_table_output(report.id, history["revisionToken"])
    report.state.artifact_proposal_id = ""
    doc.restore_table_output(report.id, history["revisionToken"])
    assert report.state.artifact_content == history["previous"]["markdown"]
    assert doc.get_table_output_history(report.id)["current"]["version"] == 3
    with pytest.raises(SceneError, match="changed"):
        doc.restore_table_output(report.id, history["revisionToken"])


def test_chart_revision_is_independent_of_later_chart_edits_and_history_reads():
    doc = SceneDocument()
    source = doc.add_table(0, 0, "group,value\nA,2")
    chart = doc.create_table_output(source.id, "chart", "group", "value")
    doc.get_table_output_history(chart.id)["current"]["chartData"]["values"][0] = 888
    chart.state.chart_data["values"][0] = 999
    assert doc.get_table_output_history(chart.id)["current"]["chartData"]["values"] == [2]
    with pytest.raises(SceneError, match="edited"):
        doc.run_table_output(chart.id)


@pytest.mark.parametrize("detached", [False, True])
def test_revisions_and_report_survive_sqlite_archive_and_source_remapping(detached, tmp_path):
    doc = SceneDocument()
    discarded = [doc.add_note(0, 0).id for _ in range(4)]
    doc.remove_nodes(discarded)
    source = doc.add_table(0, 0, "Team,Cost\nAlpha,9")
    targets = [doc.create_table_output(source.id, k, "Team", "Cost") for k in ("chart", "artifact")]
    doc.set_table_source(source.id, "Team,Cost\nAlpha,12", source.content)
    for target in targets:
        doc.run_table_output(target.id)
    history = {target.kind: doc.get_table_output_history(target.id) for target in targets}
    if detached:
        doc.record_command("removeNodes", "user", lambda: doc.remove_nodes([source.id]), node_ids=[source.id])
        for target in targets:
            assert doc._node_wire(target).get("tableInputSourceId") is None
        doc.undo()
        for target in targets:
            assert doc._node_wire(doc.nodes[target.id])["tableInputSourceId"] == source.id
        doc.redo()
    data = build_chat_data(doc)
    notes, pins = data.pop("notes_data"), data.pop("pins_data")
    db = tmp_path / "original.db"
    graph_id, _ = save_chat_atomically_row(db, None, "Table workflow", data, notes, pins)
    row, notes, pins = load_chat_snapshot(db, graph_id)
    archive = tmp_path / "reports.graphlink"
    export_archive(archive, [{**row, "notes": notes, "pins": pins}])
    imported = tmp_path / "imported.db"
    workspace_id = get_all_workspaces(imported)[0]["id"]
    new_id, = import_workspace_rows_atomically(imported, archive, workspace_id)
    restored = SceneDocument()
    restore_chat_into_document(restored, *load_chat_snapshot(imported, new_id))
    for target in (n for n in restored.nodes.values() if n.kind in history):
        info = restored.get_table_output_history(target.id)
        assert info == history[target.kind]
        if not detached:
            restored_source = next(n for n in restored.nodes.values() if n.kind == "note")
            assert restored._node_wire(target)["tableInputSourceId"] == restored_source.id != source.id
        else:
            assert "tableInputSourceId" not in restored._node_wire(target)
            with pytest.raises(SceneError):
                restored.run_table_output(target.id)
        restored.restore_table_output(target.id, info["revisionToken"])
        assert restored.get_table_output_history(target.id)["current"]["version"] == 3
    assert {n.kind for n in restored.nodes.values()} >= {"artifact", "chart"}


def test_publish_parses_once_and_hashes_once_for_readers_of_each_projection(monkeypatch):
    doc = SceneDocument()
    source = doc.add_table(0, 0, "Team,Hours,Cost\nA,4,9")
    for _ in range(4):
        for field in ("Hours", "Cost"):
            doc.create_table_output(source.id, "chart", "Team", field)
    calls = []
    parsed, digest = tables.parse_table, tables._Table.fingerprint

    def parse(*args):
        calls.append("parse")
        return parsed(*args)

    def fingerprint(*args):
        calls.append("hash")
        return digest(*args)

    monkeypatch.setattr(tables, "parse_table", parse)
    monkeypatch.setattr(tables._Table, "fingerprint", fingerprint)
    doc.scene_payload()
    assert calls.count("parse") == 1
    assert calls.count("hash") == 2


def test_contributor_rows_page_original_cells_in_source_order_and_verify_generation():
    doc = SceneDocument()
    csv = "team,cost,other\n" + "\n".join(
        f"{' Ops ' if i % 2 else ' Sales '}, {i}.00 ,original" for i in range(106)
    )
    source = doc.add_table(0, 0, csv)
    chart = doc.create_table_output(source.id, "chart", "team", "cost")
    report = doc.create_table_output(source.id, "artifact", "team", "cost")
    for node in (chart, report):
        page = doc.get_table_contributors(node.id, "Ops", 0)
        assert page["rows"] == [{"rowNumber": n + 1, "value": f" {n}.00 "} for n in range(1, 100, 2)]
        assert page["group"] == "Ops" and page["totalRows"] == 53
        assert page["sourceHash"] == doc.get_table_output_history(node.id)["current"]["sourceHash"]
        assert doc.get_table_contributors(node.id, "Ops", 50)["rows"] == [
            {"rowNumber": n + 1, "value": f" {n}.00 "} for n in (101, 103, 105)]
        for group, offset in (("unknown", 0), ("Ops", -1), ("Ops", True), ("Ops", 53)):
            with pytest.raises(SceneError):
                doc.get_table_contributors(node.id, group, offset)
    doc.set_table_source(source.id, source.content.replace("original", "updated"), source.content)
    assert len(doc.get_table_contributors(chart.id, "Ops", 10)["rows"]) == 43
    doc.set_table_source(source.id, source.content.replace(" 1.00 ", " 3.00 "), source.content)
    for node in (chart, report):
        with pytest.raises(SceneError, match="changed"):
            doc.get_table_contributors(node.id, "Ops", 0)
    doc.run_table_output(chart.id)
    assert doc.get_table_contributors(chart.id, "Ops", 0)["rows"][0] == {"rowNumber": 2, "value": " 3.00 "}
    chart.state.chart_data["labels"][0] = "Altered"
    with pytest.raises(SceneError, match="edited"):
        doc.get_table_contributors(chart.id, "Ops", 0)
    doc.remove_nodes([source.id])
    with pytest.raises(SceneError, match="unavailable"):
        doc.get_table_contributors(report.id, "Ops", 0)
