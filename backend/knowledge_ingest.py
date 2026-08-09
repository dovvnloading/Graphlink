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

import re
from pathlib import Path
from typing import Any

from backend.attachments import (
    AttachmentError,
    _can_read_as_document,
    _read_docx,
    _read_pdf,
    _read_text,
)
from backend.knowledge_chunking import chunk_text
from backend.knowledge_store import DEFAULT_DB_PATH, IngestOutcome, add_document_with_chunks
from backend.notifications import NotificationState

_HTML_EXTENSIONS = {".html", ".htm"}


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

    extension = path.suffix.lower()
    if extension == ".pdf":
        return _read_pdf(path), "application/pdf"
    if extension == ".docx":
        return _read_docx(path), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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
    text, mime = extract_text(resolved)
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
