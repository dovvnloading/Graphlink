"""Real file-attachment classification and text extraction (R8a).

Ported faithfully from the deleted graphlink_app/graphlink_file_handler.py
and graphlink_window.py's _stage_attachment_file (git show 6c919f6~1:...) -
this is not a redesign, it is the same three-way dispatch, the same
extension sets, the same byte-sniff heuristic, and the same graceful
missing-optional-dependency messages, made Qt-free.

Classification (extension-based, exactly as legacy decided it):
  - image  (.png/.jpg/.jpeg/.webp)      -> multimodal image_bytes content-part
  - audio  (graphlink_audio.py's set)   -> multimodal audio_file content-part,
                                            duration-validated via inspect_audio_file
  - document (everything else FileHandler could read: ~44 text/code
    extensions, named files like Dockerfile/Makefile, .pdf via pypdf,
    .docx via python-docx, or a byte-sniff heuristic for anything else) ->
    extracted TEXT, never sent as raw bytes

Anything outside all three is rejected with "Unsupported file type." - the
exact legacy message, kept unchanged since it is user-facing copy, not an
implementation detail.

Unlike legacy, there is no PDF_AVAILABLE/DOCX_AVAILABLE class-level cache
computed once at FileHandler() construction time: pypdf/python-docx are
imported lazily, per call, inside a try/except - simpler for a stateless
module-level API, and the cost of re-attempting a failed import is one
dict lookup in sys.modules, not a real re-scan.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from graphlink_audio import (
    AudioValidationError,
    format_duration,
    inspect_audio_file,
)
from graphlink_token_estimator import TokenEstimator

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# Verbatim from FileHandler.PLAIN_TEXT_EXTENSIONS.
PLAIN_TEXT_EXTENSIONS = {
    ".bat", ".c", ".cc", ".cfg", ".conf", ".cpp", ".cs", ".css", ".csv",
    ".env", ".go", ".h", ".hpp", ".html", ".ini", ".java", ".js", ".json",
    ".jsx", ".kt", ".kts", ".log", ".lua", ".md", ".mdx", ".php", ".ps1",
    ".py", ".rb", ".rs", ".rst", ".sh", ".sql", ".svg", ".swift", ".tex",
    ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}
# Verbatim from FileHandler.SUPPORTED_FILENAMES.
SUPPORTED_FILENAMES = {
    ".editorconfig", ".env", ".gitignore", "Dockerfile", "Gemfile",
    "Makefile", "Procfile", "README", "README.md", "requirements.txt",
}
# Verbatim from _looks_like_code_text's own extension set (used only for the
# cosmetic "Code" vs "Text" context label, not for the read/reject decision).
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".cs", ".cpp", ".c",
    ".h", ".hpp", ".go", ".rs", ".php", ".rb", ".swift", ".kt",
    ".sql", ".sh", ".ps1", ".json", ".yaml", ".yml", ".xml", ".html", ".css",
}

PDF_INSTALL_MESSAGE = "PDF support is not installed. Please run: pip install pypdf"
DOCX_INSTALL_MESSAGE = "Word document support is not installed. Please run: pip install python-docx"


class AttachmentError(Exception):
    """A file could not be attached. The message is user-facing (surfaced
    verbatim via NotificationBanner), matching every rejection string
    _stage_attachment_file/FileHandler.read_file returned."""


@dataclass
class StagedAttachment:
    """One attachment sitting in the composer, waiting for Send. Lives only
    in backend memory (ComposerDocument.staged_attachments) - the raw bytes/
    extracted text never round-trip to the frontend; only metadata does
    (see to_wire())."""

    id: str = field(default_factory=lambda: uuid4().hex)
    kind: str = ""  # "image" | "audio" | "document"
    name: str = ""
    path: str = ""
    byte_size: int = 0
    context_label: str = ""
    mime_type: str = ""
    duration_seconds: float | None = None
    # Populated for kind == "image"/"audio" only: the exact content-part
    # shape api_provider.py's _prepare_ollama_messages/
    # _anthropic_content_block_from_part/_gemini_part_from_content already
    # know how to convert (image_bytes.data as raw bytes; audio_file.path as
    # a plain path string, read lazily downstream via _read_attachment_bytes -
    # matches how audio already worked, no eager read needed here).
    content_part: dict[str, Any] | None = None
    # Populated for kind == "document" only: the extracted text, merged into
    # the sent message's plain text rather than becoming a content-part -
    # every existing consumer of SceneNode.content already expects a plain
    # string, so a document attachment never touches the multimodal path.
    extracted_text: str | None = None
    token_count: int = 0

    def to_wire(self) -> dict[str, Any]:
        """The ONLY shape the frontend ever sees for a staged attachment -
        display metadata, never path/bytes/extracted content."""
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "byteSize": self.byte_size,
            "contextLabel": self.context_label,
            "tokenCount": self.token_count,
        }


def _looks_like_text_file(sample: bytes) -> bool:
    """Verbatim port of FileHandler._looks_like_text_file."""
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    text_bytes = bytes(range(32, 127)) + b"\n\r\t\f\b"
    non_text_count = sum(byte not in text_bytes for byte in sample)
    return (non_text_count / len(sample)) < 0.30


def _can_read_as_document(path: Path) -> bool:
    """Verbatim port of FileHandler.can_read_file, minus the PDF_AVAILABLE/
    DOCX_AVAILABLE gate - .pdf/.docx are always classified as document kind
    here (matching legacy's own SUPPORTED_EXTENSIONS, which only ADDED
    .pdf/.docx when the optional lib imported successfully at startup); the
    graceful "not installed" message now surfaces at read_file() time
    instead, which is the one behavioral difference and it is strictly
    better - legacy silently routed a .pdf to "Unsupported file type" on a
    machine without pypdf, rather than telling the user why."""
    ext = path.suffix.lower()
    if ext in PLAIN_TEXT_EXTENSIONS or ext in (".pdf", ".docx"):
        return True
    if path.name in SUPPORTED_FILENAMES:
        return True
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return False
    return _looks_like_text_file(sample)


def _read_text(path: Path) -> str:
    """Verbatim port of FileHandler._read_text's encoding fallback chain."""
    raw_bytes = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore")


def _extract_pdf_page_text(page) -> str:
    """Verbatim port of FileHandler._extract_pdf_page_text - tries a
    layout-preserving extraction first (not every pypdf version's
    extract_text accepts extraction_mode), falls back to the plain call."""
    for kwargs in ({"extraction_mode": "layout"}, {}):
        try:
            text = page.extract_text(**kwargs)
        except TypeError:
            continue
        except Exception:
            text = None
        if text and text.strip():
            return text
    return ""


def _read_pdf(path: Path) -> str:
    """Verbatim port of FileHandler._read_pdf. Raises AttachmentError (not
    ValueError) when nothing extractable is found, and when the optional
    dependency is missing - both cases legacy surfaced as a rejection
    reason string, never an exception the caller had to know about."""
    try:
        import pypdf as pdf_reader_lib
    except ImportError:
        try:
            import PyPDF2 as pdf_reader_lib  # noqa: N813
        except ImportError:
            raise AttachmentError(PDF_INSTALL_MESSAGE) from None

    content: list[str] = []
    extracted_characters = 0
    with open(path, "rb") as f:
        reader = pdf_reader_lib.PdfReader(f)
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = _extract_pdf_page_text(page)
            if page_text:
                normalized_text = page_text.strip()
                content.append(normalized_text)
                extracted_characters += len(normalized_text)
            else:
                content.append(f"[Page {page_number}: no extractable text found]")

    if extracted_characters == 0:
        raise AttachmentError(
            "No readable text could be extracted from this PDF. It may be "
            "image-based, scanned, encrypted, or use an unsupported text encoding."
        )
    return "\n\n".join(part for part in content if part)


def _read_docx(path: Path) -> str:
    """Verbatim port of FileHandler._read_docx."""
    try:
        import docx
    except ImportError:
        raise AttachmentError(DOCX_INSTALL_MESSAGE) from None
    document = docx.Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _looks_like_code_text(extension: str) -> bool:
    """Extension-only slice of FileHandler._describe_document_attachment's
    code check - legacy also sniffed the CONTENT for code-like syntax when
    the extension alone didn't match; that half is dropped here as a
    cosmetic-label nicety not worth porting (it never affects whether a
    file can be read, only whether its label says "Code" or "Text"). Every
    extension that would have been misclassified by dropping it still gets
    a reasonable "Text" label instead of "Code" - the label is display-only."""
    return extension in _CODE_EXTENSIONS


def _describe_document(name: str, extension: str) -> str:
    """Port of FileHandler._describe_document_attachment (content-sniffing
    half dropped, see _looks_like_code_text)."""
    if extension == ".pdf":
        return "PDF"
    if extension == ".docx":
        return "DOCX"
    if _looks_like_code_text(extension):
        return "Code"
    if extension in {".md", ".mdx"}:
        return "Markdown"
    return "Text"


def stage_file(path: str) -> StagedAttachment:
    """Classify and resolve a file for attaching, exactly as legacy's
    _stage_attachment_file did. Raises AttachmentError with a user-facing
    message on any rejection - callers surface it via NotificationBanner,
    never a raw traceback."""
    resolved = Path(os.path.abspath(path))
    if not resolved.is_file():
        raise AttachmentError("File not found.")

    extension = resolved.suffix.lower()
    byte_size = resolved.stat().st_size
    name = resolved.name

    if extension in IMAGE_EXTENSIONS:
        try:
            data = resolved.read_bytes()
        except OSError as exc:
            raise AttachmentError(f"Could not read '{name}': {exc}") from None
        return StagedAttachment(
            kind="image",
            name=name,
            path=str(resolved),
            byte_size=byte_size,
            context_label="Vision",
            content_part={"type": "image_bytes", "data": data},
        )

    from graphlink_audio import SUPPORTED_AUDIO_EXTENSIONS

    if extension in SUPPORTED_AUDIO_EXTENSIONS:
        try:
            audio_info = inspect_audio_file(str(resolved))
        except AudioValidationError as exc:
            raise AttachmentError(str(exc)) from None
        duration = audio_info["duration_seconds"]
        return StagedAttachment(
            kind="audio",
            name=name,
            path=str(resolved),
            byte_size=byte_size,
            mime_type=audio_info["mime_type"],
            duration_seconds=duration,
            context_label=f"Audio | {format_duration(duration)}",
            # A plain path, not bytes - api_provider.py's own
            # _read_attachment_bytes reads it lazily at send time, exactly
            # the same lazy-read shape audio already used before this
            # feature existed (it just had nothing that ever populated one).
            content_part={"type": "audio_file", "path": str(resolved)},
        )

    if _can_read_as_document(resolved):
        if extension == ".pdf":
            text = _read_pdf(resolved)
        elif extension == ".docx":
            text = _read_docx(resolved)
        else:
            text = _read_text(resolved)
        return StagedAttachment(
            kind="document",
            name=name,
            path=str(resolved),
            byte_size=byte_size,
            context_label=_describe_document(name, extension),
            extracted_text=text,
            token_count=TokenEstimator().count_tokens(text),
        )

    raise AttachmentError("Unsupported file type.")


def encode_content_part_for_wire(part: dict[str, Any]) -> dict[str, Any]:
    """Base64-encode a content-part's raw bytes for JSON - same idiom as
    backend/canvas.py's own _content_parts_wire, reused here (not imported:
    that function lives in canvas.py, which does not import this module, to
    avoid a circular import - see canvas.py's own _content_codec for why a
    tiny duplicated encode step is preferred over restructuring imports for
    one function)."""
    if isinstance(part.get("data"), (bytes, bytearray)):
        return {**part, "data": base64.b64encode(bytes(part["data"])).decode("utf-8")}
    return dict(part)
