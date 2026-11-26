"""XLSX knowledge text keeps cell coordinates and real sheet names (#1098)."""

from __future__ import annotations

from zipfile import ZipFile

import pytest

from backend.knowledge_ingest import IngestError, extract_text

NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _cell(ref, value):
    return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>' if not value.isdigit() \
        else f'<c r="{ref}"><v>{value}</v></c>'


def _sheet(rows):
    body = "".join(
        f'<row r="{number}">' + "".join(_cell(ref, value) for ref, value in cells) + "</row>"
        for number, cells in rows)
    return f"<worksheet {NS}><sheetData>{body}</sheetData></worksheet>"


def _workbook(path, sheets):
    """sheets: [(name, part_file, rows)] in workbook order."""
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", f'<workbook {NS} xmlns:r="{REL}"><sheets>' + "".join(
            f'<sheet name="{name}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, (name, _, _) in enumerate(sheets))
            + "</sheets></workbook>")
        archive.writestr("xl/_rels/workbook.xml.rels",
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         + "".join(f'<Relationship Id="rId{i + 1}" Type="{REL}/worksheet" Target="worksheets/{part}"/>'
                                   for i, (_, part, _) in enumerate(sheets))
                         + "</Relationships>")
        for _, part, rows in sheets:
            archive.writestr(f"xl/worksheets/{part}", _sheet(rows))
    return path


def test_sparse_rows_keep_their_columns_and_the_real_sheet_name(tmp_path):
    path = _workbook(tmp_path / "m.xlsx", [("Measurements", "sheet1.xml", [
        (1, [("A1", "Sample"), ("B1", "Control"), ("C1", "Treatment")]),
        (2, [("A2", "S01"), ("C2", "42")]),
        (3, [("A3", "S02"), ("B3", "7")]),
    ])])
    text, _mime = extract_text(path)
    assert text.splitlines() == [
        "# Measurements",
        "A1: Sample\tB1: Control\tC1: Treatment",
        "A2: S01\tC2: 42",
        "A3: S02\tB3: 7",
    ]


def test_leading_blanks_skipped_rows_and_distant_columns(tmp_path):
    path = _workbook(tmp_path / "d.xlsx", [("Data", "sheet1.xml", [
        (2, [("C2", "leading-blank")]),
        (40, [("A40", "after-gap"), ("XFD40", "far-right")]),
    ])])
    text, _mime = extract_text(path)
    assert "C2: leading-blank" in text
    assert "A40: after-gap\tXFD40: far-right" in text


def test_workbook_order_and_nonsequential_parts(tmp_path):
    path = _workbook(tmp_path / "o.xlsx", [
        ("Summary", "sheet7.xml", [(1, [("A1", "first")])]),
        ("Raw data", "sheet2.xml", [(1, [("A1", "second")])]),
    ])
    text, _mime = extract_text(path)
    assert text.index("# Summary") < text.index("A1: first") < text.index("# Raw data") < text.index("A1: second")


def test_invalid_cell_references_are_rejected(tmp_path):
    path = _workbook(tmp_path / "bad.xlsx", [("S", "sheet1.xml", [(1, [("ZZZZ1", "x")])])])
    with pytest.raises(IngestError, match="invalid XLSX"):
        extract_text(path)
