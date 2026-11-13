"""Real coverage for backend/attachments.py (R8a).

This module is a faithful port of the deleted graphlink_app/
graphlink_file_handler.py + graphlink_window.py's _stage_attachment_file
(git show 6c919f6~1:...) - these tests exercise the SAME classification
decisions against real files on disk, not mocked ones, since the whole
point of this feature was that the previous "it works" claim (a DOM node
existing) was never actually tested against real behavior.
"""

from __future__ import annotations

import os

import pytest

from backend.attachments import AttachmentError, stage_file


@pytest.fixture
def tmp_file(tmp_path):
    def _write(name: str, content: bytes | str) -> str:
        path = tmp_path / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
        return str(path)

    return _write


class TestImageClassification:
    def test_png_is_classified_as_image_with_real_bytes_in_the_content_part(self, tmp_file):
        real_png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        path = tmp_file("photo.png", real_png_header)

        staged = stage_file(path)

        assert staged.kind == "image"
        assert staged.context_label == "Vision"
        assert staged.content_part == {"type": "image_bytes", "data": real_png_header}
        assert staged.extracted_text is None

    @pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg", ".webp"])
    def test_every_documented_image_extension_classifies_as_image(self, tmp_file, ext):
        path = tmp_file(f"pic{ext}", b"\x00" * 16)
        assert stage_file(path).kind == "image"


class TestAudioClassification:
    def test_a_readable_wav_is_classified_as_audio_with_a_path_content_part(self, tmp_file):
        # A minimal but real, playable WAV: RIFF/WAVE header + one silent
        # PCM sample, so graphlink_audio.inspect_audio_file's real duration
        # probe succeeds rather than needing a mock.
        import struct
        import tempfile
        import wave

        fd, real_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with wave.open(real_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(struct.pack("<h", 0) * 8000)  # 1 second of silence

        try:
            staged = stage_file(real_path)
            assert staged.kind == "audio"
            import hashlib
            from pathlib import Path

            assert staged.content_part == {
                "type": "audio_file",
                "path": real_path,
                "sha256": hashlib.sha256(Path(real_path).read_bytes()).hexdigest(),
            }
            assert staged.duration_seconds == pytest.approx(1.0, abs=0.05)
            assert "Audio" in staged.context_label
            assert staged.extracted_text is None
        finally:
            os.unlink(real_path)

    def test_audio_over_the_duration_cap_is_rejected_not_silently_truncated(self, tmp_file, monkeypatch):
        import backend.attachments as attachments_module

        def _fake_inspect(path):
            raise attachments_module.AudioValidationError(
                "'huge.wav' is 5:00:00 long. The maximum supported length is 4:00:00."
            )

        monkeypatch.setattr(attachments_module, "inspect_audio_file", _fake_inspect)
        path = tmp_file("huge.wav", b"RIFF....WAVEfmt ")

        with pytest.raises(AttachmentError, match="maximum supported length"):
            stage_file(path)


class TestDocumentClassificationAndExtraction:
    def test_plain_text_extension_is_read_verbatim_with_a_real_token_count(self, tmp_file):
        path = tmp_file("notes.txt", "Hello, this is a real attachment body.")

        staged = stage_file(path)

        assert staged.kind == "document"
        assert staged.context_label == "Text"
        assert staged.extracted_text == "Hello, this is a real attachment body."
        assert staged.token_count > 0
        assert staged.content_part is None

    def test_code_extension_gets_the_code_label(self, tmp_file):
        path = tmp_file("script.py", "def f():\n    return 1\n")
        assert stage_file(path).context_label == "Code"

    def test_markdown_extension_gets_the_markdown_label(self, tmp_file):
        path = tmp_file("readme.md", "# Title\n\nBody text.")
        assert stage_file(path).context_label == "Markdown"

    def test_unrecognized_extension_with_text_like_bytes_is_sniffed_and_read(self, tmp_file):
        # No extension in PLAIN_TEXT_EXTENSIONS, but the byte-sniff heuristic
        # (ported verbatim from FileHandler._looks_like_text_file) should
        # still classify and read it as a document.
        path = tmp_file("data.xyz", "just plain ascii text content for sniffing purposes")

        staged = stage_file(path)

        assert staged.kind == "document"
        assert staged.extracted_text is not None

    def test_named_file_with_no_extension_is_recognized(self, tmp_file):
        path = tmp_file("Dockerfile", "FROM python:3.12\n")
        staged = stage_file(path)
        assert staged.kind == "document"

    def test_binary_garbage_with_unknown_extension_is_rejected(self, tmp_file):
        path = tmp_file("blob.xyz", bytes(range(256)) * 20)

        with pytest.raises(AttachmentError, match="Unsupported file type"):
            stage_file(path)

    def test_null_byte_containing_file_is_rejected_even_with_mostly_ascii_content(self, tmp_file):
        # The byte-sniff heuristic's own hard rule: any NUL byte disqualifies
        # a file outright, matching FileHandler._looks_like_text_file exactly.
        path = tmp_file("mixed.bin", b"mostly text here\x00but one null byte")
        with pytest.raises(AttachmentError, match="Unsupported file type"):
            stage_file(path)


class TestPdfAndDocx:
    def test_missing_pypdf_surfaces_the_exact_install_message(self, tmp_file, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name in ("pypdf", "PyPDF2"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocked_import)
        path = tmp_file("doc.pdf", b"%PDF-1.4 fake")

        with pytest.raises(AttachmentError, match="pip install pypdf"):
            stage_file(path)

    def test_missing_python_docx_surfaces_the_exact_install_message(self, tmp_file, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name == "docx":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocked_import)
        path = tmp_file("doc.docx", b"PK\x03\x04 fake docx bytes")

        with pytest.raises(AttachmentError, match="pip install python-docx"):
            stage_file(path)

    def test_a_real_docx_extracts_its_real_paragraph_text(self, tmp_file):
        pytest.importorskip("docx")
        import docx

        import tempfile

        fd, real_path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        document = docx.Document()
        document.add_paragraph("First real paragraph.")
        document.add_paragraph("Second real paragraph.")
        document.save(real_path)

        try:
            staged = stage_file(real_path)
            assert staged.kind == "document"
            assert staged.context_label == "DOCX"
            assert "First real paragraph." in staged.extracted_text
            assert "Second real paragraph." in staged.extracted_text
        finally:
            os.unlink(real_path)

    def test_small_compressed_pdf_that_expands_past_page_limit_is_refused_before_parsing(
        self, tmp_path, monkeypatch,
    ):
        import backend.attachments as attachments_module
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject
        from pypdf._page import PageObject

        pytest.importorskip("pypdf")
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        stream = DecodedStreamObject()
        stream.set_data(b"BT (" + b"x" * 25_000 + b") Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
        path = tmp_path / "compressed.pdf"
        writer.write(path)
        assert path.stat().st_size < 5000, "fixture must actually be compressed"

        monkeypatch.setattr(attachments_module, "MAX_PDF_CONTENT_STREAM_BYTES", 2048)

        def forbidden_parse(*_args, **_kwargs):
            pytest.fail("large expanded content must be rejected before extract_text")

        monkeypatch.setattr(PageObject, "extract_text", forbidden_parse)
        with pytest.raises(AttachmentError, match="expanded-stream"):
            stage_file(str(path))

    @pytest.mark.parametrize("nested", [False, True])
    def test_compressed_form_xobject_is_capped_before_any_text_extraction(
        self, tmp_path, monkeypatch, nested,
    ):
        import backend.attachments as attachments_module
        from pypdf import PdfWriter
        from pypdf._page import PageObject
        from pypdf.generic import (
            ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject,
        )

        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }))
        font_resources = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })

        def make_form(payload, resources):
            stream = DecodedStreamObject()
            stream.set_data(payload)
            stream.update({
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject([
                    FloatObject(0), FloatObject(0), FloatObject(100), FloatObject(100),
                ]),
                NameObject("/Resources"): resources,
            })
            return writer._add_object(stream.flate_encode())

        large_form = make_form(
            b"BT /F1 12 Tf (" + b"A" * 25_000 + b") Tj ET", font_resources,
        )
        if nested:
            inner_resources = DictionaryObject({
                NameObject("/XObject"): DictionaryObject({NameObject("/Inner"): large_form}),
            })
            target_form = make_form(b"q /Inner Do Q", inner_resources)
        else:
            target_form = large_form

        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Fx"): target_form}),
        })
        direct = DecodedStreamObject()
        direct.set_data(b"q /Fx Do Q")
        page[NameObject("/Contents")] = writer._add_object(direct)
        path = tmp_path / "large-form.pdf"
        writer.write(path)
        assert path.stat().st_size < 5000

        monkeypatch.setattr(attachments_module, "MAX_PDF_CONTENT_STREAM_BYTES", 2048)

        def forbidden_parse(*_args, **_kwargs):
            pytest.fail("nested Form expansion must be rejected before extract_text")

        monkeypatch.setattr(PageObject, "extract_text", forbidden_parse)
        with pytest.raises(AttachmentError, match="expanded-stream"):
            stage_file(str(path))

    @pytest.mark.parametrize("inside_form", [False, True])
    def test_compressed_font_cmap_is_refused_before_text_extraction(
        self, tmp_path, monkeypatch, inside_form,
    ):
        """ToUnicode is decoded by pypdf before Graphlink's extracted-text cap."""
        import backend.attachments as attachments_module
        from pypdf import PdfWriter
        from pypdf._page import PageObject
        from pypdf.generic import (
            ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject,
        )

        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        cmap = DecodedStreamObject()
        cmap.set_data(b"% harmless CMap padding\n" * 2_000)
        cmap_ref = writer._add_object(cmap.flate_encode())
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/ToUnicode"): cmap_ref,
        }))
        font_resources = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        direct = DecodedStreamObject()
        if inside_form:
            form = DecodedStreamObject()
            form.set_data(b"BT /F1 12 Tf (A) Tj ET")
            form.update({
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject([
                    FloatObject(0), FloatObject(0), FloatObject(100), FloatObject(100),
                ]),
                NameObject("/Resources"): font_resources,
            })
            form_ref = writer._add_object(form)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/XObject"): DictionaryObject({NameObject("/Fx"): form_ref}),
            })
            direct.set_data(b"q /Fx Do Q")
        else:
            page[NameObject("/Resources")] = font_resources
            direct.set_data(b"BT /F1 12 Tf (A) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(direct)
        path = tmp_path / "font-cmap.pdf"
        writer.write(path)
        assert path.stat().st_size < 5000

        monkeypatch.setattr(attachments_module, "MAX_PDF_CONTENT_STREAM_BYTES", 2048)

        def forbidden_parse(*_args, **_kwargs):
            pytest.fail("large font CMap must be rejected before extract_text")

        monkeypatch.setattr(PageObject, "extract_text", forbidden_parse)
        with pytest.raises(AttachmentError, match="expanded-stream"):
            stage_file(str(path))

    def test_pdf_total_decoded_stream_bytes_across_pages_is_bounded(
        self, tmp_path, monkeypatch,
    ):
        import backend.attachments as attachments_module
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }))
        for _ in range(3):
            page = writer.add_blank_page(width=612, height=792)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            })
            contents = DecodedStreamObject()
            contents.set_data(b"BT /F1 12 Tf 36 720 Td (Normal text here) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(contents)
        path = tmp_path / "three-pages.pdf"
        writer.write(path)

        monkeypatch.setattr(attachments_module, "MAX_PDF_DOCUMENT_STREAM_BYTES", 100)
        with pytest.raises(AttachmentError, match="expanded-stream"):
            stage_file(str(path))

    def test_regular_compressed_pdf_content_still_extracts(self, tmp_path):
        pytest.importorskip("pypdf")
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        # A real font resource and text state are needed on pypdf 6.x:
        # the previous minimal BT/Tj fixture was tolerated by 5.x only.
        helvetica = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font = writer._add_object(helvetica)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 36 720 Td (Small valid page) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
        path = tmp_path / "normal.pdf"
        writer.write(path)
        assert "Small valid page" in stage_file(str(path)).extracted_text

    def test_a_real_pdf_with_no_extractable_text_is_rejected_not_silently_empty(self, tmp_file):
        pytest.importorskip("pypdf")
        from pypdf import PdfWriter

        import tempfile

        fd, real_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)  # a real page, zero text
        with open(real_path, "wb") as f:
            writer.write(f)

        try:
            with pytest.raises(AttachmentError, match="No readable text could be extracted"):
                stage_file(real_path)
        finally:
            os.unlink(real_path)


class TestRejections:
    def test_missing_file_is_rejected(self, tmp_path):
        with pytest.raises(AttachmentError, match="File not found"):
            stage_file(str(tmp_path / "does-not-exist.txt"))

    def test_a_directory_is_rejected_not_read_as_a_file(self, tmp_path):
        with pytest.raises(AttachmentError, match="File not found"):
            stage_file(str(tmp_path))

def test_staging_rejects_oversize_files_before_reading(tmp_file, monkeypatch):
    import backend.attachments as attachments_module

    path = tmp_file("large.txt", "A" * 32)
    monkeypatch.setattr(attachments_module, "MAX_STAGED_FILE_BYTES", 16)
    with pytest.raises(AttachmentError, match="file limit"):
        stage_file(path)


def test_staging_bounds_extracted_plain_text(tmp_file, monkeypatch):
    import backend.attachments as attachments_module

    path = tmp_file("many-lines.txt", "important " * 20)
    monkeypatch.setattr(attachments_module, "MAX_DOCUMENT_TEXT_CHARS", 20)
    with pytest.raises(AttachmentError, match="extracted-text"):
        stage_file(path)


def test_docx_extracts_tables_in_document_order(tmp_file):
    pytest.importorskip("docx")
    from docx import Document
    from pathlib import Path

    document = Document()
    document.add_paragraph("Before table")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Key"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Priority"
    table.cell(1, 1).text = "High"
    document.add_paragraph("After table")

    path = Path(tmp_file("table-report.docx", b""))
    document.save(path)
    staged = stage_file(str(path))
    assert staged.extracted_text == "Before table\nKey\tValue\nPriority\tHigh\nAfter table"


def test_docx_table_only_is_not_silently_empty(tmp_file):
    pytest.importorskip("docx")
    from docx import Document
    from pathlib import Path

    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Only text is here"
    path = Path(tmp_file("table-only.docx", b""))
    document.save(path)

    staged = stage_file(str(path))
    assert "Only text is here" in staged.extracted_text

@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_bomless_utf16_plain_text_is_read_correctly(tmp_file, encoding):
    path = tmp_file("utf16.txt", "Hello UTF16".encode(encoding))
    staged = stage_file(path)
    assert staged.extracted_text == "Hello UTF16"


def test_utf8_bom_is_stripped_before_token_counting(tmp_file):
    path = tmp_file("with-bom.txt", "Hello world".encode("utf-8-sig"))
    staged = stage_file(path)
    assert staged.extracted_text == "Hello world"


def test_docx_xml_size_is_checked_before_python_docx_parser(tmp_file, monkeypatch):
    import zipfile
    import backend.attachments as attachments_module

    pytest.importorskip("docx")
    path = tmp_file("inflated.docx", b"")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"X" * 20_000)
    monkeypatch.setattr(attachments_module, "MAX_DOCX_XML_PART_BYTES", 1000)
    with pytest.raises(AttachmentError, match="XML part"):
        stage_file(path)


def test_docx_aggregate_expansion_is_bounded(tmp_file, monkeypatch):
    import zipfile
    import backend.attachments as attachments_module

    pytest.importorskip("docx")
    path = tmp_file("many-parts.docx", b"")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"A" * 400)
        archive.writestr("word/styles.xml", b"B" * 400)
    monkeypatch.setattr(attachments_module, "MAX_DOCX_UNCOMPRESSED_BYTES", 500)
    with pytest.raises(AttachmentError, match="uncompressed ZIP"):
        stage_file(path)
