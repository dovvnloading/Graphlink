"""Workspace-agent document reads: real parsers, ranges, and the fs boundary."""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from backend.harness.loop import HarnessRunContext
from backend.harness.tools_fs import FS_READ_SPEC, register_harness_fs_tools
from backend.providers.base import ToolCall
from backend.tools import FS_READ, ToolRegistry


@pytest.fixture
def fs(tmp_path):
    registry = ToolRegistry()
    register_harness_fs_tools(registry)

    async def deny(_call):
        return False

    ctx = HarnessRunContext(
        granted_scopes=frozenset({FS_READ}),
        request_approval=deny,
        harness_workspace_dir=tmp_path,
    )

    def read(path: str, **kwargs):
        return asyncio.run(registry.invoke(
            ToolCall(id="1", name="fs.read", arguments={"path": path, **kwargs}),
            ctx,
        ))

    return tmp_path, read


def _pdf(path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for message in ("First page", "Second page", "Third page"):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        contents = DecodedStreamObject()
        contents.set_data(
            f"BT /F1 12 Tf 40 720 Td ({message}) Tj ET".encode("ascii")
        )
        page[NameObject("/Contents")] = writer._add_object(contents)
    writer.write(path)


def _docx(path):
    from docx import Document

    doc = Document()
    doc.add_paragraph("Before table")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Field"
    table.cell(0, 1).text = "Value"
    doc.add_paragraph("After table")
    doc.save(path)


def _xlsx(path):
    """Minimal OOXML package with shared, inline, formula, and boolean cells."""
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    workbook = (
        f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
        '<sheet name="Overview" sheetId="1" r:id="rId1"/>'
        '<sheet name="Details" sheetId="2" r:id="rId2"/>'
        '<sheet name="Empty" sheetId="3" r:id="rId3"/>'
        '</sheets></workbook>'
    )
    relationships = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/>'
        '</Relationships>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", relationships)
        zf.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{main}"><si><t>Shared value</t></si></sst>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{main}"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c>'
            '<c r="B1"><v>17</v></c></row>'
            '</sheetData></worksheet>',
        )
        zf.writestr(
            "xl/worksheets/sheet2.xml",
            f'<worksheet xmlns="{main}"><sheetData>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>Inline text</t></is></c>'
            '<c r="B2" t="b"><v>1</v></c></row>'
            '<row r="3"><c r="C3"><f>SUM(B1:B2)</f></c></row>'
            '</sheetData></worksheet>',
        )
        zf.writestr(
            "xl/worksheets/sheet3.xml",
            f'<worksheet xmlns="{main}"><sheetData/></worksheet>',
        )


def test_fs_read_schema_exposes_bounded_ranges():
    properties = FS_READ_SPEC.input_schema["properties"]
    assert {"path", "offset", "limit", "page_start", "page_end",
            "sheet_start", "sheet_end", "sheet"} <= set(properties)


def test_pdf_read_and_page_range(fs):
    root, read = fs
    _pdf(root / "report.pdf")
    result = read("report.pdf", page_start=2, page_end=3)
    assert not result.is_error, result.content
    assert "[Page 2]" in result.content and "Second page" in result.content
    assert "[Page 3]" in result.content and "Third page" in result.content
    assert "First page" not in result.content

    whole = read("report.pdf")
    assert not whole.is_error and "First page" in whole.content
    assert "Third page" in whole.content


@pytest.mark.parametrize("page_start,page_end", [(0, 2), (3, 2), (1, 4), ("1", 2)])
def test_pdf_invalid_range_is_error(fs, page_start, page_end):
    root, read = fs
    _pdf(root / "report.pdf")
    result = read("report.pdf", page_start=page_start, page_end=page_end)
    assert result.is_error and "page range" in result.content.lower()


def test_docx_read_preserves_paragraph_and_table_order(fs):
    root, read = fs
    _docx(root / "report.docx")
    result = read("report.docx")
    assert not result.is_error, result.content
    assert result.content.index("Before table") < result.content.index("Field\tValue")
    assert result.content.index("Field\tValue") < result.content.index("After table")
    assert read("report.docx", page_start=1).is_error


@pytest.mark.parametrize("extension", [".xlsx", ".xlsm"])
def test_xlsx_sheet_ranges_and_sheet_names(fs, extension):
    root, read = fs
    _xlsx(root / f"book{extension}")
    result = read(f"book{extension}", sheet_start=2, sheet_end=3)
    assert not result.is_error, result.content
    assert "[Sheet 2: Details]" in result.content
    assert "A2: Inline text" in result.content
    assert "B2: TRUE" in result.content
    assert "C3: =SUM(B1:B2)" in result.content
    assert "[Sheet 3: Empty]" in result.content
    assert "[empty sheet]" in result.content
    assert "Shared value" not in result.content

    one = read(f"book{extension}", sheet="overview")
    assert not one.is_error, one.content
    assert "A1: Shared value" in one.content and "B1: 17" in one.content
    assert "[Sheet 2:" not in one.content
    all_sheets = read(f"book{extension}")
    assert not all_sheets.is_error and "[Sheet 3: Empty]" in all_sheets.content


def test_sheet_selectors_and_cross_format_args_are_validated(fs):
    root, read = fs
    _xlsx(root / "book.xlsx")
    _pdf(root / "report.pdf")
    for kwargs in (
        {"sheet_start": 0},
        {"sheet_start": 3, "sheet_end": 2},
        {"sheet_end": 4},
        {"sheet": "Absent"},
        {"sheet": "Details", "sheet_start": 1},
        {"page_start": 1},
    ):
        result = read("book.xlsx", **kwargs)
        assert result.is_error, (kwargs, result.content)
    assert read("report.pdf", sheet="Details").is_error
    (root / "text.txt").write_text("hello", encoding="utf-8")
    assert read("text.txt", page_start=1).is_error
    assert not read("text.txt").is_error


@pytest.mark.parametrize("extension,fixture", [
    (".pdf", _pdf), (".docx", _docx), (".xlsx", _xlsx),
])
def test_input_cap_is_enforced_before_document_parsing(fs, monkeypatch, extension, fixture):
    from backend.harness import tools_fs

    root, read = fs
    path = root / f"large{extension}"
    fixture(path)
    assert path.stat().st_size > 64
    monkeypatch.setattr(tools_fs, "_MAX_FS_FILE_BYTES", 64)
    result = read(path.name)
    assert result.is_error and "64-byte harness read limit" in result.content


@pytest.mark.parametrize("extension,fixture", [
    (".pdf", _pdf), (".docx", _docx), (".xlsx", _xlsx),
])
def test_extracted_output_is_capped(fs, monkeypatch, extension, fixture):
    from backend.harness import tools_fs

    root, read = fs
    path = root / f"truncated{extension}"
    fixture(path)
    monkeypatch.setattr(tools_fs, "_READ_CAP_CHARS", 35)
    result = read(path.name)
    assert not result.is_error, result.content
    assert "[truncated at 35 characters]" in result.content


def test_xlsx_zip_expansion_guard(fs, monkeypatch):
    from backend import attachments

    root, read = fs
    _xlsx(root / "book.xlsx")
    monkeypatch.setattr(attachments, "MAX_DOCX_XML_PART_BYTES", 50)
    result = read("book.xlsx")
    assert result.is_error and "XML part" in result.content


def test_pdf_stream_expansion_guard_is_reused(fs, monkeypatch):
    from backend import attachments

    root, read = fs
    _pdf(root / "report.pdf")
    monkeypatch.setattr(attachments, "MAX_PDF_CONTENT_STREAM_BYTES", 10)
    result = read("report.pdf")
    assert result.is_error and "expanded-stream" in result.content


def test_path_confinement_still_applies_to_documents(fs):
    root, read = fs
    outside = root.parent / "outside.pdf"
    _pdf(outside)
    assert read("../outside.pdf").is_error


@pytest.mark.parametrize("extension", [".doc", ".xls"])
def test_legacy_binary_office_formats_are_not_misdecoded(fs, extension):
    root, read = fs
    (root / f"old{extension}").write_bytes(b"\xd0\xcf\x11\xe0")
    result = read(f"old{extension}")
    assert result.is_error and "Convert" in result.content
