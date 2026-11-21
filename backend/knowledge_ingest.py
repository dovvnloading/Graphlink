"""ADR-017 stage 17.1: the ingestion pipeline - extract -> chunk -> store.

Extraction reuses backend/attachments.py's own `_read_pdf`/`_read_docx`/
`_read_text` directly (not a copy - see this module's own import comment)
for exactly the extensions ADR-017 names (pdf/docx/md/code), PLUS a new
HTML-specific extractor (`_extract_html`) that strips markup instead of
indexing raw tag soup - `.html`/`.htm` are already in
attachments.PLAIN_TEXT_EXTENSIONS (readable as a one-shot attachment
today), but raw-tag text makes a genuinely worse retrieval chunk than
cleaned text does, and beautifulsoup4 is already a hard dependency (used
by graphlink_plugins/web_research/providers.py's own
BeautifulSoupContentExtractor). `_extract_html` mirrors that class's own
script/style/nav-stripping technique on a PLAIN (html_text) -> str
signature rather than importing the class itself - it is built around
FetchedPayload/ResearchLimits/CancellationToken, none of which a local
file has any reason to construct, matching this codebase's own established
"small, self-contained algorithm duplicated with a comment beats a new
cross-module dependency" precedent (see backend/chat_library.py's own
_quarantine_corrupt_chats_db docstring for the same call made elsewhere).
"""

from __future__ import annotations

import os
import re
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, LargeZipFile, ZipFile
from xml.etree import ElementTree
from pathlib import Path
from typing import Any

from backend.attachments import (
    AttachmentError,
    _can_read_as_document,
    _read_docx,
    _read_pdf,
    _read_text,
    PLAIN_TEXT_EXTENSIONS,
    SUPPORTED_FILENAMES,
    MAX_DOCUMENT_TEXT_CHARS,
)
from backend.knowledge_chunking import chunk_text
from backend.knowledge_store import DEFAULT_DB_PATH, IngestOutcome, add_document_with_chunks
from backend.notifications import NotificationState

_HTML_EXTENSIONS = {".html", ".htm"}
# Cap directly-ingested files as well as background jobs.
MAX_KNOWLEDGE_FILE_BYTES = 25 * 1024 * 1024
# Office XML parsers materialize trees much larger than their serialized XML.
# Check every package before any eager XML parser loads it.
MAX_OOXML_MEMBERS = 2048
MAX_OOXML_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_OOXML_XML_PART_BYTES = 4 * 1024 * 1024



# Import a known set of document and source-code types, not arbitrary
# "looks like text" files (which may contain binary data or secrets).
SUPPORTED_KNOWLEDGE_EXTENSIONS = (PLAIN_TEXT_EXTENSIONS - {".env"}) | {".pdf", ".docx", ".xlsx", ".htm"}
SUPPORTED_KNOWLEDGE_FILENAMES = SUPPORTED_FILENAMES - {".env", ".gitignore"}


def is_supported_knowledge_file(path: Path) -> bool:
    return (
        path.suffix.lower() in SUPPORTED_KNOWLEDGE_EXTENSIONS
        or path.name in SUPPORTED_KNOWLEDGE_FILENAMES
    )


def _check_ooxml_archive(path: Path) -> None:
    """Bound the full Office package before a DOCX or XLSX XML parser opens it."""
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_OOXML_MEMBERS:
                raise IngestError("Office document contains too many ZIP members.")
            expanded = 0
            for member in members:
                if member.flag_bits & 0x1:
                    raise IngestError("Encrypted Office ZIP members cannot be indexed.")
                expanded += member.file_size
                if expanded > MAX_OOXML_EXPANDED_BYTES:
                    raise IngestError("Office document exceeds the safe expanded-size limit.")
                if (member.filename.lower().endswith((".xml", ".rels"))
                        and member.file_size > MAX_OOXML_XML_PART_BYTES):
                    raise IngestError("Office XML part exceeds the safe expanded-size limit.")
    except (BadZipFile, LargeZipFile) as exc:
        raise IngestError("Office document is not a readable ZIP archive.") from exc


def _extract_xlsx(path: Path) -> str:
    """Read OOXML cell values without writing files or executing macros.

    Bounded member count, expanded bytes, XML size and output prevent a
    compressed workbook from consuming unbounded resources. Supports shared
    strings, inline strings, numbers, cached formula values and many sheets.
    """
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    _check_ooxml_archive(path)
    try:
        with ZipFile(path) as workbook:
            def xml_part(name: str):
                part = workbook.getinfo(name)
                if part.file_size > MAX_OOXML_XML_PART_BYTES:
                    raise IngestError("Spreadsheet XML part is too large.")
                data = workbook.read(part)
                # OOXML needs neither DTDs nor entity declarations; deny
                # entities before ElementTree constructs its in-memory tree.
                if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                    raise IngestError("Spreadsheet XML contains prohibited DTD or entities.")
                return ElementTree.fromstring(data)

            shared: list[str] = []
            shared_total = 0
            if "xl/sharedStrings.xml" in workbook.namelist():
                root = xml_part("xl/sharedStrings.xml")
                for item in root.iter(ns + "si"):
                    value = "".join(node.text or "" for node in item.iter(ns + "t"))
                    shared.append(value)
                    shared_total += len(value)
                    if shared_total > MAX_DOCUMENT_TEXT_CHARS:
                        raise IngestError("Spreadsheet text exceeds the indexing limit.")

            sheets = sorted(
                name for name in workbook.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
            if not sheets:
                raise IngestError("Spreadsheet contains no readable worksheets.")
            lines: list[str] = []
            total = 0
            cells_found = False
            for name in sheets:
                lines.append(f"# {Path(name).stem}")
                root = xml_part(name)
                for row in root.iter(ns + "row"):
                    values = []
                    for cell in row.findall(ns + "c"):
                        value = cell.find(ns + "v")
                        kind = cell.get("t")
                        if kind == "inlineStr":
                            inline = cell.find(ns + "is")
                            text = "".join(item.text or "" for item in inline.iter(ns + "t")) if inline is not None else ""
                        elif value is not None and value.text:
                            raw = value.text
                            if kind == "s":
                                index = int(raw)
                                if index < 0 or index >= len(shared):
                                    raise IngestError("Spreadsheet contains an invalid shared-string index.")
                                text = shared[index]
                            else:
                                text = raw
                        else:
                            formula = cell.find(ns + "f")
                            text = (formula.text or "") if formula is not None else ""
                        values.append(text)
                    if any(value.strip() for value in values):
                        cells_found = True
                        line = "\t".join(values)
                        total += len(line) + 1
                        if total > MAX_DOCUMENT_TEXT_CHARS:
                            raise IngestError("Spreadsheet text exceeds the indexing limit.")
                        lines.append(line)
            if not cells_found:
                raise IngestError("Spreadsheet contains no indexable cells.")
            return "\n".join(lines).strip()
    except (BadZipFile, KeyError, ElementTree.ParseError, ValueError, OverflowError) as exc:
        raise IngestError(f"Could not read spreadsheet '{path.name}': invalid XLSX content.") from exc


class IngestError(Exception):
    """A file could not be ingested - message is safe to surface to the
    user verbatim, matching AttachmentError's own posture (this module
    reuses attachments.py's extraction, so its errors flow straight
    through unchanged; this class exists for the cases specific to
    ingestion itself, e.g. a genuinely unreadable path)."""


def _extract_html(html_text: str) -> str:
    """Strips script/style/nav/footer/header/aside/form/noscript/template,
    then reads heading/paragraph/list/quote/code-block text from
    main/article/body (whichever exists first) - same element set and
    fallback order as graphlink_plugins/web_research/providers.py's own
    BeautifulSoupContentExtractor, see this module's own docstring for why
    it isn't imported directly. Falls back to the whole document's own
    plain text when none of those elements exist (a page that is nothing
    but, say, a bare <div> soup) - never raises just because a document
    doesn't use semantic tags."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    for element in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "template"]):
        element.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    sections = tuple(
        re.sub(r"\s+", " ", element.get_text(" ", strip=True))
        for element in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre"])
        if element.get_text(" ", strip=True)
    )
    text = "\n".join(sections) if sections else re.sub(r"\s+", " ", main.get_text(" ", strip=True))
    return text.strip()


def extract_text(path: Path) -> tuple[str, str]:
    """Returns (text, mime) for `path`, or raises IngestError/AttachmentError
    with a user-facing message. `mime` is a coarse label
    ("application/pdf" | "application/vnd...docx" | "text/html" |
    "text/plain") - good enough for `documents.mime` display, not a real
    sniffed MIME type."""
    if not path.is_file():
        raise IngestError(f"File not found: {path}")
    if path.stat().st_size > MAX_KNOWLEDGE_FILE_BYTES:
        raise IngestError("File exceeds the 25 MiB Knowledge Library limit.")

    extension = path.suffix.lower()
    if extension == ".pdf":
        return _read_pdf(path), "application/pdf"
    if extension == ".docx":
        _check_ooxml_archive(path)
        return _read_docx(path), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if extension == ".xlsx":
        return _extract_xlsx(path), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if extension in _HTML_EXTENSIONS:
        try:
            raw = _read_text(path)
        except AttachmentError:
            raise
        text = _extract_html(raw)
        if not text:
            raise IngestError(f"'{path.name}' contained no readable text after stripping markup.")
        return text, "text/html"
    if _can_read_as_document(path):
        return _read_text(path), "text/plain"
    raise IngestError(f"Unsupported file type: {path.name}")


def _chunk_and_store(
    *,
    text: str,
    source_uri: str,
    title: str,
    mime: str,
    collection_id: int,
    db_path: Path | None,
    notifications: NotificationState | None,
    last_saved: dict[str, Any] | None,
    target_tokens: int | None,
    overlap_tokens: int | None,
) -> IngestOutcome:
    """The shared tail of both ingest_file() and ingest_text() (stage
    17.5): chunk -> store, idempotent by content hash (see
    backend.knowledge_store's own docstring for the exact idempotency
    scope). `target_tokens`/`overlap_tokens` default to
    backend.knowledge_chunking's own module defaults when omitted (None,
    not the defaults themselves, so a future config surface can distinguish
    "caller didn't ask" from "caller explicitly wants the default value")."""
    chunk_kwargs: dict[str, int] = {}
    if target_tokens is not None:
        chunk_kwargs["target_tokens"] = target_tokens
    if overlap_tokens is not None:
        chunk_kwargs["overlap_tokens"] = overlap_tokens
    chunks = chunk_text(text, **chunk_kwargs)
    if not chunks:
        raise IngestError(f"'{title}' contained no indexable text.")

    return add_document_with_chunks(
        db_path if db_path is not None else DEFAULT_DB_PATH,
        source_uri=source_uri,
        title=title,
        mime=mime,
        text=text,
        chunks=chunks,
        collection_id=collection_id,
        notifications=notifications,
        last_saved=last_saved,
    )


def ingest_file(
    path: str,
    *,
    collection_id: int = 0,
    db_path: Path | None = None,
    notifications: NotificationState | None = None,
    last_saved: dict[str, Any] | None = None,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> IngestOutcome:
    """The one call ADR-017 stage 17.1's exit criterion names: extract ->
    chunk -> store."""
    resolved = Path(path).resolve()
    # Direct ingestion uses the same no-follow, size-bounded descriptor
    # boundary as asynchronous file/folder imports. A later pathname swap
    # cannot redirect PDF/Office parsers to a different source.
    from backend.harness.tools_fs import _read_bounded_bytes

    try:
        data = _read_bounded_bytes(resolved, max_bytes=MAX_KNOWLEDGE_FILE_BYTES)
    except (OSError, ValueError) as exc:
        raise IngestError(f"Could not safely read '{resolved.name}': {exc}") from exc

    with TemporaryDirectory(prefix="graphlink-knowledge-ingest-") as directory:
        staged = Path(directory) / resolved.name
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as target:
            target.write(data)
        text, mime = extract_text(staged)

    return _chunk_and_store(
        text=text, source_uri=str(resolved), title=resolved.name, mime=mime,
        collection_id=collection_id, db_path=db_path, notifications=notifications,
        last_saved=last_saved, target_tokens=target_tokens, overlap_tokens=overlap_tokens,
    )


def ingest_text(
    text: str,
    *,
    source_uri: str,
    title: str,
    mime: str = "text/plain",
    collection_id: int = 0,
    db_path: Path | None = None,
    notifications: NotificationState | None = None,
    last_saved: dict[str, Any] | None = None,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> IngestOutcome:
    """ADR-017 stage 17.5: ingests already-in-memory text with no file on
    disk to extract from - web-research retention (a fetched page's own
    text) and branch indexing (a branch's assembled chat history) both
    have TEXT, not a path extract_text()'s extension-dispatch could do
    anything with. Shares ingest_file()'s own chunk+store tail exactly
    (_chunk_and_store) - the only difference is where `text`/`mime` come
    from. `source_uri` is caller-supplied rather than derived from a path
    (a URL for web research, a synthetic `branch:<node_id>` marker for
    branch indexing - see each caller's own convention)."""
    return _chunk_and_store(
        text=text, source_uri=source_uri, title=title, mime=mime,
        collection_id=collection_id, db_path=db_path, notifications=notifications,
        last_saved=last_saved, target_tokens=target_tokens, overlap_tokens=overlap_tokens,
    )
