"""Bounded text extraction for workspace-agent PDF, Word and Excel reads.

The fs.read handler supplies bytes already limited to its existing 8 MiB
input cap. Reuse the attachment pipeline's PDF stream and Office ZIP expansion
guards; Office documents are parsed in memory so a file replacement after
the bounded read cannot evade that cap. No macro, formula or linked content
is executed.
"""

from __future__ import annotations

import io
import posixpath
import xml.etree.ElementTree as ET
import zipfile

from backend import attachments

_XLSX_SHEET_CAP = 400
_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class DocumentReadError(ValueError):
    """Document is unsupported, invalid, or would exceed extraction limits."""


def _selected_range(start: int | None, end: int | None, count: int, kind: str) -> range:
    first = 1 if start is None else start
    last = count if end is None else end
    if (type(first) is not int or type(last) is not int
            or first < 1 or last < first or last > count):
        raise DocumentReadError(
            f"Invalid {kind} range: use 1-based, inclusive indices within 1–{count}."
        )
    return range(first, last + 1)


def _append_bounded(parts: list[str], text: str, length: int) -> int:
    length += len(text) + 1
    if length > attachments.MAX_DOCUMENT_TEXT_CHARS:
        raise DocumentReadError("Document exceeds the extracted-text limit.")
    parts.append(text)
    return length


def _preflight_office_zip(data: bytes, label: str) -> zipfile.ZipFile:
    """Check central-directory expansion before an XML/Office parser runs."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        try:
            members = archive.infolist()
            if len(members) > attachments.MAX_DOCX_MEMBERS:
                raise DocumentReadError(f"{label} contains too many ZIP members.")
            total = 0
            for member in members:
                total += member.file_size
                if total > attachments.MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise DocumentReadError(f"{label} exceeds the uncompressed ZIP size limit.")
                if (member.filename.lower().endswith((".xml", ".rels"))
                        and member.file_size > attachments.MAX_DOCX_XML_PART_BYTES):
                    raise DocumentReadError(f"{label} XML part exceeds the expanded-size limit.")
        except BaseException:
            archive.close()
            raise
        return archive
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise DocumentReadError(f"{label} is not a readable ZIP archive.") from exc


def _read_pdf(data: bytes, start: int | None, end: int | None) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentReadError(attachments.PDF_INSTALL_MESSAGE) from exc

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise DocumentReadError("Encrypted PDFs cannot be read by the workspace agent.")
    page_count = len(reader.pages)
    if page_count > attachments.MAX_PDF_PAGES:
        raise DocumentReadError(f"PDF exceeds the {attachments.MAX_PDF_PAGES}-page limit.")

    parts: list[str] = []
    length = 0
    stream_bytes = 0
    for page_number in _selected_range(start, end, page_count, "page"):
        page = reader.pages[page_number - 1]
        try:
            stream_bytes += attachments._check_pdf_content_size(
                page,
                remaining_document_bytes=attachments.MAX_PDF_DOCUMENT_STREAM_BYTES - stream_bytes,
            )
        except attachments.AttachmentError as exc:
            raise DocumentReadError(str(exc)) from exc
        extracted = attachments._extract_pdf_page_text(page).strip()
        length = _append_bounded(
            parts,
            f"[Page {page_number}]\n{extracted or '[no extractable text]'}",
            length,
        )
    return "\n\n".join(parts)


def _read_docx(data: bytes) -> str:
    try:
        import docx
        from docx.table import Table
    except ImportError as exc:
        raise DocumentReadError(attachments.DOCX_INSTALL_MESSAGE) from exc

    with _preflight_office_zip(data, "DOCX"):
        document = docx.Document(io.BytesIO(data))
        parts: list[str] = []
        length = 0
        for block in document.iter_inner_content():
            lines = (
                ("\t".join(cell.text for cell in row.cells) for row in block.rows)
                if isinstance(block, Table) else (block.text,)
            )
            for line in lines:
                length = _append_bounded(parts, line, length)
        return "\n".join(parts)


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    """Read a bounded XML member and refuse DTD/entity declarations."""
    try:
        with archive.open(name) as stream:
            raw = stream.read(attachments.MAX_DOCX_XML_PART_BYTES + 1)
    except KeyError as exc:
        raise DocumentReadError(f"XLSX is missing {name}.") from exc
    if len(raw) > attachments.MAX_DOCX_XML_PART_BYTES:
        raise DocumentReadError("XLSX XML part exceeds the expanded-size limit.")
    if b"<!doctype" in raw.lower() or b"<!entity" in raw.lower():
        raise DocumentReadError("XLSX XML entity declarations are not allowed.")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DocumentReadError(f"XLSX contains malformed XML in {name}.") from exc


def _sheet_target(target: str) -> str:
    """Resolve an internal workbook relationship without visiting external URLs."""
    if target.startswith("/"):
        resolved = posixpath.normpath(target.lstrip("/"))
    else:
        resolved = posixpath.normpath(posixpath.join("xl", target))
    if not resolved.startswith("xl/") or not resolved.endswith(".xml"):
        raise DocumentReadError("XLSX has an invalid worksheet relationship.")
    return resolved


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _xml(archive, "xl/sharedStrings.xml")
    text_tag = f"{{{_SPREADSHEET_NS}}}t"
    return [
        "".join(text.text or "" for text in item.iter(text_tag))
        for item in root.findall(f"{{{_SPREADSHEET_NS}}}si")
    ]


def _sheet_rows(archive: zipfile.ZipFile, member: str, strings: list[str]):
    root = _xml(archive, member)
    ns = f"{{{_SPREADSHEET_NS}}}"
    for row in root.iter(f"{ns}row"):
        cells: list[str] = []
        for cell in row.findall(f"{ns}c"):
            value = cell.find(f"{ns}v")
            formula = cell.find(f"{ns}f")
            cell_type = cell.get("t")
            raw = (value.text or "") if value is not None else ""
            if cell_type == "s" and raw:
                try:
                    raw = strings[int(raw)]
                except (ValueError, IndexError) as exc:
                    raise DocumentReadError("XLSX has an invalid shared-string index.") from exc
            elif cell_type == "inlineStr":
                inline = cell.find(f"{ns}is")
                raw = (
                    "".join(text.text or "" for text in inline.iter(f"{ns}t"))
                    if inline is not None else ""
                )
            elif cell_type == "b" and raw:
                raw = "TRUE" if raw == "1" else "FALSE"
            elif not raw and formula is not None:
                raw = "=" + (formula.text or "")
            if raw:
                cells.append(f"{cell.get('r') or '?'}: {raw}")
        if cells:
            yield "\t".join(cells)


def _read_xlsx(
    data: bytes,
    start: int | None,
    end: int | None,
    sheet: str | None,
) -> str:
    with _preflight_office_zip(data, "XLSX") as archive:
        workbook = _xml(archive, "xl/workbook.xml")
        relationships = _xml(archive, "xl/_rels/workbook.xml.rels")
        rels = {
            item.get("Id"): _sheet_target(item.get("Target") or "")
            for item in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")
            if item.get("Type", "").endswith("/worksheet")
            and item.get("TargetMode") != "External"
        }
        sheets = workbook.find(f"{{{_SPREADSHEET_NS}}}sheets")
        if sheets is None:
            raise DocumentReadError("XLSX has no worksheets.")
        entries = [
            (entry.get("name") or "Unnamed", rels.get(entry.get(f"{{{_OFFICE_REL_NS}}}id")))
            for entry in sheets.findall(f"{{{_SPREADSHEET_NS}}}sheet")
        ]
        if len(entries) > _XLSX_SHEET_CAP:
            raise DocumentReadError(f"XLSX exceeds the {_XLSX_SHEET_CAP}-sheet limit.")
        if sheet is not None:
            if not isinstance(sheet, str) or not sheet.strip() or start is not None or end is not None:
                raise DocumentReadError("Use either sheet (name) or sheet_start/sheet_end (indices).")
            matches = [i for i, (name, _) in enumerate(entries, start=1) if name.casefold() == sheet.casefold()]
            if not matches:
                raise DocumentReadError(f"XLSX sheet {sheet!r} was not found.")
            selected = matches
        else:
            selected = list(_selected_range(start, end, len(entries), "sheet"))

        strings = _shared_strings(archive)
        parts: list[str] = []
        length = 0
        for index in selected:
            name, member = entries[index - 1]
            if member is None:
                raise DocumentReadError(f"XLSX sheet {name!r} has no internal worksheet.")
            length = _append_bounded(parts, f"[Sheet {index}: {name}]", length)
            found = False
            for row in _sheet_rows(archive, member, strings):
                found = True
                length = _append_bounded(parts, row, length)
            if not found:
                length = _append_bounded(parts, "[empty sheet]", length)
        return "\n".join(parts)


def extract_document_text(
    data: bytes,
    suffix: str,
    *,
    page_start: int | None = None,
    page_end: int | None = None,
    sheet_start: int | None = None,
    sheet_end: int | None = None,
    sheet: str | None = None,
) -> str:
    """Return plain text with source page/sheet markers for fs.read."""
    pages = page_start is not None or page_end is not None
    sheets = sheet is not None or sheet_start is not None or sheet_end is not None
    if suffix == ".pdf":
        if sheets:
            raise DocumentReadError("Sheet selection is only available for Excel files.")
        return _read_pdf(data, page_start, page_end)
    if suffix == ".docx":
        if pages or sheets:
            raise DocumentReadError("Page/sheet selection is not supported for Word documents.")
        return _read_docx(data)
    if suffix in (".xlsx", ".xlsm"):
        if pages:
            raise DocumentReadError("Page selection is only available for PDFs.")
        return _read_xlsx(data, sheet_start, sheet_end, sheet)
    raise DocumentReadError(f"Unsupported document type: {suffix}.")
