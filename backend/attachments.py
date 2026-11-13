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
import codecs
import hashlib
import os
import zipfile
import zlib
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

# Hard ceilings before untrusted file bytes or expanded document text enter
# memory. Text/PDF/DOCX parsers can expand a much smaller on-disk file.
MAX_STAGED_FILE_BYTES = 128 * 1024 * 1024
MAX_DOCUMENT_TEXT_CHARS = 1_000_000
MAX_PDF_PAGES = 400
# A highly compressed PDF content stream can expand to many MB and force
# pypdf's parser to allocate large intermediate arrays BEFORE it returns
# any text for MAX_DOCUMENT_TEXT_CHARS to reject. Bound each page's TOTAL
# decoded /Contents stream size before calling extract_text.
MAX_PDF_CONTENT_STREAM_BYTES = 1024 * 1024
# Bound nested Form XObjects as well as direct /Contents. Forms can contain
# compressed drawing/text streams and their own /Resources /XObject chains.
MAX_PDF_FORM_OBJECTS = 128
MAX_PDF_XOBJECT_RESOURCE_ENTRIES = 256
MAX_PDF_FONT_RESOURCE_ENTRIES = 256
# The 1 MiB page budget applies to every decoded content AND font CMap
# stream; also cap total decoded streams across a multi-page document.
MAX_PDF_DOCUMENT_STREAM_BYTES = 32 * 1024 * 1024
# DOCX is a ZIP container. File size and extracted-text caps alone do not
# bound the XML/ZIP expansion that python-docx parses before text extraction.
MAX_DOCX_MEMBERS = 2_048
MAX_DOCX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_DOCX_XML_PART_BYTES = 16 * 1024 * 1024


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


def _read_bounded_file(path: Path) -> bytes:
    # Stat-based staging checks alone have a growth race; bound the actual
    # bytes read as well, including image content-part staging.
    with path.open("rb") as handle:
        data = handle.read(MAX_STAGED_FILE_BYTES + 1)
    if len(data) > MAX_STAGED_FILE_BYTES:
        raise AttachmentError("Attachment exceeds the maximum supported file size.")
    return data


def _fingerprint_staged_audio(path: Path) -> str:
    """Bind a lazy audio path to the bytes actually checked at attachment time.

    A file can grow while the user composes a message. Bound the *read*, not
    just the earlier stat size, and hold only a SHA-256 digest, never a second
    full audio buffer in memory.
    """
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            total += len(chunk)
            if total > MAX_STAGED_FILE_BYTES:
                raise AttachmentError("Attachment exceeds the maximum supported file size.")
            digest.update(chunk)
    return digest.hexdigest()


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
        # Read only what the sniff needs, not the entire unknown file.
        with path.open("rb") as handle:
            sample = handle.read(4096)
    except OSError:
        return False
    return _looks_like_text_file(sample)


def _bomless_utf16_encoding(data: bytes) -> str | None:
    """Detect the unambiguous alternating-NUL shape of ASCII-heavy UTF-16.

    Without a BOM, arbitrary UTF-16 is not reliably distinguishable from
    legacy single-byte encodings. Only claim an encoding when most pairs
    have NUL bytes on exactly one side; otherwise use normal fallbacks.
    """
    sample = data[:4096]
    pairs = len(sample) // 2
    if pairs < 2:
        return None
    even = sample[:pairs * 2:2].count(0)
    odd = sample[1:pairs * 2:2].count(0)
    threshold = max(2, (3 * pairs + 4) // 5)
    if odd >= threshold and even * 5 < pairs:
        return "utf-16-le"
    if even >= threshold and odd * 5 < pairs:
        return "utf-16-be"
    return None


def _read_text(path: Path) -> str:
    """Decode text without retaining a BOM or misreading UTF-16 as NULs."""
    raw_bytes = _read_bounded_file(path)
    if raw_bytes.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return raw_bytes.decode("utf-32")
    if raw_bytes.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw_bytes.decode("utf-16")
    if raw_bytes.startswith(codecs.BOM_UTF8):
        return raw_bytes.decode("utf-8-sig")
    detected = _bomless_utf16_encoding(raw_bytes)
    if detected is not None:
        return raw_bytes.decode(detected)
    for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
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


def _pdf_object(value):
    """Dereference a pypdf IndirectObject without decompressing its stream."""
    return value.get_object() if hasattr(value, "get_object") else value


def _pdf_expanded_stream_size(stream, remaining: int) -> int:
    """Preflight one encoded content/Form stream, bounded by remaining bytes.

    Never call get_data()/ContentStream: those expand the entire stream before
    the caller can apply a byte cap. _data holds the encoded source bytes.
    """
    raw = getattr(stream, "_data", None)
    if not isinstance(raw, bytes):
        raise AttachmentError("PDF contains an unreadable page/Form content stream.")
    filters = stream.get("/Filter")
    filters_list = [str(item) for item in filters] if isinstance(filters, list) else (
        [str(filters)] if filters is not None else []
    )
    if not filters_list:
        if len(raw) > remaining:
            raise AttachmentError("PDF page/Form content exceeds the expanded-stream attachment limit.")
        return len(raw)
    if filters_list not in (["/FlateDecode"], ["/Fl"]):
        raise AttachmentError("PDF page/Form uses a compression filter that cannot be safely bounded.")

    decoder = zlib.decompressobj()
    expanded = 0
    try:
        for offset in range(0, len(raw), 4096):
            pending = raw[offset:offset + 4096]
            while pending:
                inflated = decoder.decompress(pending, remaining - expanded + 1)
                expanded += len(inflated)
                if expanded > remaining:
                    raise AttachmentError("PDF page/Form content exceeds the expanded-stream attachment limit.")
                pending = decoder.unconsumed_tail
        expanded += len(decoder.flush(remaining - expanded + 1))
    except zlib.error as exc:
        raise AttachmentError("PDF has an invalid compressed page/Form stream.") from exc
    if expanded > remaining:
        raise AttachmentError("PDF page/Form content exceeds the expanded-stream attachment limit.")
    return expanded


def _check_pdf_content_size(page, *, remaining_document_bytes: int = MAX_PDF_DOCUMENT_STREAM_BYTES) -> int:
    """Guard page streams, nested Forms and font ToUnicode CMaps before parsing.

    Direct /Contents alone is insufficient: a 10-byte /Fx Do can reference a
    Flate-compressed Form containing millions of characters. pypdf expands
    those Forms in extract_text(), before our extracted-text cap is applied.

    Walk each resource tree *iteratively* with a shared page-wide byte budget
    and an explicit Form count. Detect cycles to avoid a recursive XObject
    graph causing unbounded pypdf parsing; reject unsupported filters instead
    of trusting them to obey the cap. Image XObjects aren't text-extracted.
    """
    # Each frame: (content stream(s), resource dictionary, ancestor form IDs)
    pending: list[tuple[Any, Any, frozenset[int]]] = [
        (page.get("/Contents"), page.get("/Resources"), frozenset()),
    ]
    page_budget = min(MAX_PDF_CONTENT_STREAM_BYTES, remaining_document_bytes)
    decoded_total = 0
    forms_seen = 0
    fonts_seen = 0

    while pending:
        contents, resources, ancestors = pending.pop()
        if contents is not None:
            content_obj = _pdf_object(contents)
            entries = content_obj if isinstance(content_obj, list) else [content_obj]
            if len(entries) > MAX_PDF_XOBJECT_RESOURCE_ENTRIES:
                raise AttachmentError("PDF contains too many page content streams.")
            for entry in entries:
                stream = _pdf_object(entry)
                decoded_total += _pdf_expanded_stream_size(
                    stream, page_budget - decoded_total,
                )

        resource_dict = _pdf_object(resources)
        if resource_dict is None:
            continue
        if not isinstance(resource_dict, dict):
            raise AttachmentError("PDF contains an invalid resource dictionary.")
        # pypdf decodes a font's /ToUnicode CMap while preparing glyph
        # maps, before our extracted-text cap has any effect. Treat those
        # streams like page/Form contents, under the SAME cumulative budget.
        font_resources = _pdf_object(resource_dict.get("/Font"))
        if font_resources is not None:
            if not isinstance(font_resources, dict):
                raise AttachmentError("PDF contains an invalid font resource dictionary.")
            fonts_seen += len(font_resources)
            if fonts_seen > MAX_PDF_FONT_RESOURCE_ENTRIES:
                raise AttachmentError("PDF contains too many font resources.")
            for font_ref in font_resources.values():
                font = _pdf_object(font_ref)
                if not isinstance(font, dict):
                    raise AttachmentError("PDF contains an invalid font resource.")
                cmap = font.get("/ToUnicode")
                if cmap is None:
                    continue
                decoded_total += _pdf_expanded_stream_size(
                    _pdf_object(cmap), page_budget - decoded_total,
                )

        objects = _pdf_object(resource_dict.get("/XObject"))
        if objects is None:
            continue
        if not isinstance(objects, dict):
            raise AttachmentError("PDF contains an invalid XObject dictionary.")
        if len(objects) > MAX_PDF_XOBJECT_RESOURCE_ENTRIES:
            raise AttachmentError("PDF contains too many XObject resources.")
        for ref in objects.values():
            obj = _pdf_object(ref)
            if not isinstance(obj, dict):
                raise AttachmentError("PDF contains an invalid XObject resource.")
            if str(obj.get("/Subtype")) != "/Form":
                continue
            identity = id(obj)
            if identity in ancestors:
                raise AttachmentError("PDF contains cyclic Form XObject resources.")
            forms_seen += 1
            if forms_seen > MAX_PDF_FORM_OBJECTS:
                raise AttachmentError("PDF contains too many Form XObjects.")
            pending.append((
                obj, obj.get("/Resources"), ancestors | {identity},
            ))
    return decoded_total


def _read_pdf(path: Path) -> str:
    """Verbatim port of FileHandler._read_pdf. Raises AttachmentError (not
    ValueError) when nothing extractable is found, and when the optional
    dependency is missing - both cases legacy surfaced as a rejection
    reason string, never an exception the caller had to know about."""
    try:
        import pypdf as pdf_reader_lib
    except ImportError:
        try:
            # The two packages are the same library either side of its
            # rename, and this code only touches the PdfReader/extract_text
            # surface they share - but mypy has no way to express "either of
            # these modules, whichever imported" and reads the fallback as a
            # redefinition of the name the first import already bound.
            import PyPDF2 as pdf_reader_lib  # type: ignore[no-redef]  # noqa: N813
        except ImportError:
            raise AttachmentError(PDF_INSTALL_MESSAGE) from None

    if path.stat().st_size > MAX_STAGED_FILE_BYTES:
        raise AttachmentError("PDF exceeds the attachment file size limit.")
    content: list[str] = []
    extracted_characters = 0
    decoded_document_bytes = 0
    with open(path, "rb") as f:
        reader = pdf_reader_lib.PdfReader(f)
        if len(reader.pages) > MAX_PDF_PAGES:
            raise AttachmentError(f"PDF exceeds the {MAX_PDF_PAGES}-page attachment limit.")
        for page_number, page in enumerate(reader.pages, start=1):
            decoded_document_bytes += _check_pdf_content_size(
                page,
                remaining_document_bytes=MAX_PDF_DOCUMENT_STREAM_BYTES - decoded_document_bytes,
            )
            page_text = _extract_pdf_page_text(page)
            if page_text:
                normalized_text = page_text.strip()
                extracted_characters += len(normalized_text)
                if extracted_characters > MAX_DOCUMENT_TEXT_CHARS:
                    raise AttachmentError("PDF exceeds the extracted-text attachment limit.")
                content.append(normalized_text)
            else:
                content.append(f"[Page {page_number}: no extractable text found]")

    if extracted_characters == 0:
        raise AttachmentError(
            "No readable text could be extracted from this PDF. It may be "
            "image-based, scanned, encrypted, or use an unsupported text encoding."
        )
    return "\n\n".join(part for part in content if part)


def _check_docx_archive(path: Path) -> None:
    """Refuse compressed packages that would inflate without a useful bound.

    Check ZIP central-directory sizes *before* python-docx loads/parses XML.
    The application-level text limit is a separate later safety check, not a
    substitute for a bound on the structure being parsed.
    """
    if path.stat().st_size > MAX_STAGED_FILE_BYTES:
        raise AttachmentError("DOCX exceeds the attachment file size limit.")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_DOCX_MEMBERS:
                raise AttachmentError("DOCX contains too many ZIP members.")
            uncompressed = 0
            for member in members:
                uncompressed += member.file_size
                if uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise AttachmentError("DOCX exceeds the uncompressed ZIP size limit.")
                if (member.filename.lower().endswith((".xml", ".rels"))
                        and member.file_size > MAX_DOCX_XML_PART_BYTES):
                    raise AttachmentError("DOCX XML part exceeds the expanded-size limit.")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise AttachmentError("DOCX is not a readable ZIP archive.") from exc


def _read_docx(path: Path) -> str:
    """Parse a bounded DOCX package and preserve its text/table ordering."""
    try:
        import docx
    except ImportError:
        raise AttachmentError(DOCX_INSTALL_MESSAGE) from None
    # python-docx annotates Document() as str | IO[bytes] | None, but its
    # runtime contract is wider: anything that is not a str is handed
    # straight to zipfile.ZipFile, which takes a Path. Passing str(path)
    # instead would not be an equivalent rewrite - the str branch runs an
    # os.path.isdir/is_zipfile probe first and raises PackageNotFoundError
    # for a missing file, where the Path branch raises FileNotFoundError.
    from docx.table import Table

    _check_docx_archive(path)
    document = docx.Document(path)  # type: ignore[arg-type]
    # document.paragraphs silently omits all table cells. Walk the source
    # blocks in order, preserving both paragraphs and table rows in context.
    parts: list[str] = []
    char_count = 0
    for block in document.iter_inner_content():
        rows = (
            ("\t".join(cell.text for cell in row.cells) for row in block.rows)
            if isinstance(block, Table) else (block.text,)
        )
        for text in rows:
            char_count += len(text) + 1
            if char_count > MAX_DOCUMENT_TEXT_CHARS:
                raise AttachmentError("DOCX exceeds the extracted-text attachment limit.")
            parts.append(text)
    return "\n".join(parts)


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
    if byte_size > MAX_STAGED_FILE_BYTES:
        raise AttachmentError(
            f"Attachment exceeds the {MAX_STAGED_FILE_BYTES // (1024 * 1024)} MiB file limit."
        )
    name = resolved.name

    if extension in IMAGE_EXTENSIONS:
        try:
            data = _read_bounded_file(resolved)
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
        try:
            staged_sha256 = _fingerprint_staged_audio(resolved)
        except OSError as exc:
            raise AttachmentError(f"Could not read '{name}': {exc}") from None
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
            content_part={"type": "audio_file", "path": str(resolved), "sha256": staged_sha256},
        )

    if _can_read_as_document(resolved):
        if extension == ".pdf":
            text = _read_pdf(resolved)
        elif extension == ".docx":
            text = _read_docx(resolved)
        else:
            text = _read_text(resolved)
        if len(text) > MAX_DOCUMENT_TEXT_CHARS:
            raise AttachmentError("Document exceeds the extracted-text attachment limit.")
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
