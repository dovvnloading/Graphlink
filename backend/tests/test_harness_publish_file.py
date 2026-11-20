"""End-to-end workspace file publication: format, provenance, confinement, undo."""

from __future__ import annotations

import asyncio
import io
import json
import threading
import zipfile

import pytest
from PIL import Image

from backend.domain.commands import UndoRefusedError
from backend.domain.graph import SceneDocument
from backend.harness.loop import HarnessRunContext
from backend.harness.tools_publish import PUBLISH_FILE_SPEC, register_workspace_tools
from backend.providers.base import ToolCall
from backend.tools import FS_READ, GRAPH_MUTATE, ToolRegistry


@pytest.fixture
def publisher(tmp_path):
    document = SceneDocument()
    agent = document.add_harness_node(50, 70, "Make a report")
    agent.state.harness_run_id = "run-123"
    agent.pending_request_id = "run-123"
    registry = ToolRegistry()
    register_workspace_tools(registry, None, document)
    approvals = []

    async def approve(call):
        approvals.append(call.name)
        return True

    ctx = HarnessRunContext(
        granted_scopes=frozenset({FS_READ, GRAPH_MUTATE}),
        request_approval=approve,
        harness_workspace_dir=tmp_path,
        harness_node_id=agent.id,
        run_id="run-123",
    )

    def publish(path, **arguments):
        return asyncio.run(registry.invoke(
            ToolCall(id="call-1", name=PUBLISH_FILE_SPEC.name,
                     arguments={"path": path, **arguments}),
            ctx,
        ))

    return tmp_path, document, agent, ctx, registry, approvals, publish


def _created(publisher, path, **arguments):
    _root, document, agent, _ctx, _registry, approvals, publish = publisher
    result = publish(path, **arguments)
    assert not result.is_error, result.content
    data = json.loads(result.content)
    node = document.nodes[data["node_id"]]
    assert data["kind"] == node.kind
    assert node.x > agent.x  # New output is to the right of its agent.
    assert any(edge.source == agent.id and edge.target == node.id
               for edge in document.edges.values())
    assert document.command_log[-1].run_id == "run-123"
    assert document.command_log[-1].provenance == "agent"
    assert approvals[-1] == "graph.publish_file"
    return node


@pytest.mark.parametrize("filename,mime", [
    ("report.md", "text/markdown"),
    ("notes.markdown", "text/markdown"),
    ("summary.txt", "text/plain"),
])
def test_text_and_markdown_publish_as_document_nodes(publisher, filename, mime):
    root, document, agent, *_ = publisher
    (root / filename).write_text("# Hello\nsome content\n", encoding="utf-8")
    node = _created(publisher, filename)
    assert node.kind == "document"
    assert node.title == filename
    # Path.write_text uses CRLF on Windows; publication preserves actual bytes.
    assert node.content == (root / filename).read_bytes().decode("utf-8")
    assert node.state.mime_type == mime
    assert node.state.byte_size == len(node.content.encode())
    assert not node.state.file_path  # Snapshot, never an unsafe live disk path.
    assert len(document.nodes) == 2


def test_csv_is_a_native_editable_table_and_undoes_with_its_link(publisher):
    root, document, agent, *_ = publisher
    (root / "sales.csv").write_text("Name,Amount\nA,10\nB,20\n", encoding="utf-8")
    node = _created(publisher, "sales.csv")
    assert node.kind == "note" and node.state.is_table_source
    assert node.title == "sales.csv"
    assert node.content.replace("\r\n", "\n") == "Name,Amount\nA,10\nB,20"

    agent.pending_request_id = None
    assert document.undo_run("run-123") == 1
    assert set(document.nodes) == {agent.id}
    assert not document.edges
    document.redo()
    assert node.id in document.nodes
    assert any(edge.target == node.id for edge in document.edges.values())


def test_valid_image_is_served_from_assets_and_undone_with_run(publisher):
    root, document, agent, *_ = publisher
    picture = io.BytesIO()
    Image.new("RGB", (4, 3)).save(picture, format="PNG")
    (root / "figure.png").write_bytes(picture.getvalue())
    node = _created(publisher, "figure.png")
    assert node.kind == "image"
    assert node.title == "figure.png"
    asset_id = node.state.image_asset_id
    assert document.get_image_asset(asset_id) == (picture.getvalue(), "image/png")

    agent.pending_request_id = None
    assert document.undo_run("run-123") == 1
    assert node.id not in document.nodes
    assert document.get_image_asset(asset_id) is None
    assert not document.edges
    document.redo()
    assert document.get_image_asset(asset_id) == (picture.getvalue(), "image/png")


def _xlsx(path, *, chartsheet_first=False, sparse_first_row=False, whitespace_header=False,
          bad_shared_index=None):
    """A two-sheet OOXML workbook with shared strings and sparse second sheet."""
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if chartsheet_first:
            z.writestr("xl/chartsheets/sheet1.xml", f'<chartsheet xmlns="{main}"/>')
        z.writestr("xl/workbook.xml", (
            f'<workbook xmlns="{main}" xmlns:r="{office}"><sheets>'
            + ('<sheet name="Charts" sheetId="3" r:id="r3"/>' if chartsheet_first else '')
            + '<sheet name="Overview" sheetId="1" r:id="r1"/>'
            '<sheet name="Details" sheetId="2" r:id="r2"/>'
            '</sheets></workbook>'
        ))
        z.writestr("xl/_rels/workbook.xml.rels", (
            f'<Relationships xmlns="{package}">'
            f'<Relationship Id="r1" Type="{office}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="r2" Type="{office}/worksheet" Target="worksheets/sheet2.xml"/>'
            + (f'<Relationship Id="r3" Type="{office}/chartsheet" '
               'Target="chartsheets/sheet1.xml"/>' if chartsheet_first else '')
            + '</Relationships>'
        ))
        z.writestr("xl/sharedStrings.xml", (
            f'<sst xmlns="{main}"><si><t>Name</t></si></sst>'
        ))
        if sparse_first_row:
            overview = (
                f'<worksheet xmlns="{main}"><sheetData>'
                '<row r="1"><c r="A1" t="inlineStr"><is><t>Name</t></is></c>'
                + ('<c r="B1" t="inlineStr"><is><t xml:space="preserve"> </t></is></c>'
                   if whitespace_header else '')
                + '</row>'
                '<row r="2"><c r="A2" t="inlineStr"><is><t>Alice</t></is></c>'
                '<c r="B2" t="inlineStr"><is><t>Sales</t></is></c></row>'
                '</sheetData></worksheet>'
            )
        else:
            overview = (
                f'<worksheet xmlns="{main}"><sheetData>'
                '<row r="1"><c r="A1" t="s"><v>0</v></c>'
                '<c r="B1" t="inlineStr"><is><t>Amount</t></is></c></row>'
                '<row r="2"><c r="A2" t="inlineStr"><is><t>Alpha</t></is></c>'
                '<c r="B2"><v>7</v></c></row>'
                '</sheetData></worksheet>'
            )
        if bad_shared_index is not None:
            overview = overview.replace(
                '<c r="A2" t="inlineStr"><is><t>Alpha</t></is></c>',
                f'<c r="A2" t="s"><v>{bad_shared_index}</v></c>',
            )
        z.writestr("xl/worksheets/sheet1.xml", overview)
        z.writestr("xl/worksheets/sheet2.xml", (
            f'<worksheet xmlns="{main}"><sheetData>'
            '<row r="3"><c r="C3" t="s"><v>0</v></c>'
            '<c r="D3" t="inlineStr"><is><t>Amount</t></is></c></row>'
            '<row r="4"><c r="C4" t="inlineStr"><is><t>Beta</t></is></c>'
            '<c r="D4"><v>13</v></c></row>'
            '</sheetData></worksheet>'
        ))


@pytest.mark.parametrize("extension", [".xlsx", ".xlsm"])
def test_excel_uses_first_sheet_by_default_or_a_named_sheet(publisher, extension):
    root, document, agent, *_ = publisher
    filename = f"workbook{extension}"
    _xlsx(root / filename)
    first = _created(publisher, filename)
    assert first.state.is_table_source
    assert first.content.replace("\r\n", "\n") == "Name,Amount\nAlpha,7"

    named = _created(publisher, filename, sheet="details")
    assert named.content.replace("\r\n", "\n") == "Name,Amount\nBeta,13"
    assert named.id != first.id
    # The second output packs without colliding with the first output.
    assert (named.x, named.y) != (first.x, first.y)
    assert len([e for e in document.edges.values() if e.source == agent.id]) == 2

    agent.pending_request_id = None
    assert document.undo_run("run-123") == 2
    assert set(document.nodes) == {agent.id}
    assert not document.edges


def test_excel_sparse_header_expands_to_all_occupied_columns(publisher):
    root, *_ = publisher
    _xlsx(root / "sparse.xlsx", sparse_first_row=True)
    node = _created(publisher, "sparse.xlsx")
    assert node.state.is_table_source
    # The missing B1 header must not hide the populated B2 cell; canvas
    # requires a name for every CSV column, so one is generated for B.
    assert node.content.replace("\r\n", "\n") == "Name,Column 2\nAlice,Sales"


def test_excel_whitespace_only_header_is_repaired_and_publishable(publisher):
    root, *_ = publisher
    _xlsx(root / "whitespace.xlsx", sparse_first_row=True, whitespace_header=True)
    node = _created(publisher, "whitespace.xlsx")
    assert node.state.is_table_source
    assert node.content.replace("\r\n", "\n") == "Name,Column 2\nAlice,Sales"


def test_excel_first_actual_worksheet_skips_chartsheet(publisher):
    root, document, agent, _ctx, _registry, _approvals, publish = publisher
    _xlsx(root / "charts-first.xlsx", chartsheet_first=True)
    node = _created(publisher, "charts-first.xlsx")
    assert node.content.replace("\r\n", "\n") == "Name,Amount\nAlpha,7"
    named = _created(publisher, "charts-first.xlsx", sheet="Details")
    assert named.content.replace("\r\n", "\n") == "Name,Amount\nBeta,13"
    before = len(document.nodes)
    invalid = publish("charts-first.xlsx", sheet="Charts")
    assert invalid.is_error
    assert len(document.nodes) == before




@pytest.mark.parametrize("index", [-1, 1])
def test_invalid_excel_shared_string_does_not_publish_a_table(publisher, index):
    root, document, agent, _ctx, _registry, _approvals, publish = publisher
    _xlsx(root / "bad.xlsx", bad_shared_index=index)
    result = publish("bad.xlsx")
    assert result.is_error and "invalid shared-string index" in result.content
    assert set(document.nodes) == {agent.id}
    assert not document.command_log
    assert not document.edges


@pytest.mark.parametrize("identity_field", ["current_chat_id", "current_workspace_id"])
def test_publish_discarded_when_canvas_identity_changes_during_load(
    publisher, monkeypatch, identity_field,
):
    from backend.harness import tools_publish

    root, document, agent, ctx, registry, _approvals, _publish = publisher
    (root / "report.md").write_text("# Original workspace", encoding="utf-8")
    setattr(document, identity_field, "original")
    entered, release = threading.Event(), threading.Event()
    load = tools_publish._load_file

    def delayed_load(path, sheet):
        entered.set()
        if not release.wait(timeout=2):
            raise RuntimeError("Timed out waiting for a workspace switch")
        return load(path, sheet)

    monkeypatch.setattr(tools_publish, "_load_file", delayed_load)

    async def drive():
        task = asyncio.create_task(registry.invoke(
            ToolCall(id="switch", name="graph.publish_file",
                     arguments={"path": "report.md"}), ctx,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            setattr(document, identity_field, "different")
        finally:
            release.set()
        return await task

    result = asyncio.run(drive())
    assert result.is_error and "chat or workspace changed" in result.content
    assert set(document.nodes) == {agent.id}
    assert not document.edges
    assert not document.command_log
    assert not document.image_assets


def test_file_confinement_and_invalid_inputs_never_mutate_scene(publisher, tmp_path):
    root, document, agent, _ctx, _registry, _approvals, publish = publisher
    (tmp_path.parent / "outside.md").write_text("private", encoding="utf-8")
    (root / "broken.csv").write_text("OnlyOneColumn\n1\n", encoding="utf-8")
    (root / "binary.txt").write_bytes(b"\xff")
    (root / "wrong.png").write_bytes(b"not-an-image")
    (root / "unknown.bin").write_bytes(b"binary")
    (root / "bad.xlsx").write_bytes(b"not-a-zip")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret", encoding="utf-8")

    for path, args in [
        ("../outside.md", {}),
        (".git/config", {}),
        ("missing.md", {}),
        ("unknown.bin", {}),
        ("binary.txt", {}),
        ("broken.csv", {}),
        ("wrong.png", {}),
        ("bad.xlsx", {}),
        ("broken.csv", {"sheet": "Details"}),
        ("", {}),
        ("broken.csv", {"sheet": ""}),
    ]:
        result = publish(path, **args)
        assert result.is_error, (path, result.content)
        assert set(document.nodes) == {agent.id}
        assert not document.command_log
        assert not document.edges
        assert not document.image_assets


def test_symlink_cannot_escape_workspace(publisher):
    root, document, agent, _ctx, _registry, _approvals, publish = publisher
    outside = root.parent / "outside-target.md"
    outside.write_text("secret", encoding="utf-8")
    try:
        (root / "link.md").symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks unavailable on this platform")
    result = publish("link.md")
    assert result.is_error and "outside" in result.content.lower()
    assert set(document.nodes) == {agent.id}


def test_denial_scope_and_stale_run_are_noops(publisher):
    root, document, agent, ctx, registry, approvals, publish = publisher
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    ctx.granted_scopes = frozenset({FS_READ})
    assert publish("notes.txt").is_error
    assert approvals == []  # Scope rejection happens before an approval.
    ctx.granted_scopes = frozenset({FS_READ, GRAPH_MUTATE})

    async def deny(call):
        return False

    ctx.request_approval = deny
    assert publish("notes.txt").is_error
    ctx.request_approval = lambda call: asyncio.sleep(0, result=True)

    agent.state.harness_run_id = "another-run"
    result = publish("notes.txt")
    assert result.is_error and "no longer active" in result.content
    assert len(document.nodes) == 1 and not document.command_log


def test_undo_refuses_to_revert_a_live_workspace_run(publisher):
    root, document, agent, *_ = publisher
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    _created(publisher, "notes.txt")
    with pytest.raises(UndoRefusedError):
        document.undo_run("run-123")
    agent.pending_request_id = None
    assert document.undo_run("run-123") == 1
