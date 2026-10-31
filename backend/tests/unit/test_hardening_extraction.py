"""Part B RED tests for input limits and PDF OCR safety (DM-2, IN-3)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pymupdf
import pytest
from docx import Document

from app.adapters.extractors.factory import extract_document
from app.adapters.extractors.text import decode_text
from app.core.errors import CorruptFileError, FileTooLargeError


def _pdf(path: Path, *, pages: int = 1, image: bool = False) -> None:
    """Create a small selectable PDF or full-page scanned image."""
    pdf = pymupdf.open()
    for index in range(pages):
        page = pdf.new_page()
        page.insert_text((45, 55), f"Content on page {index + 1}")
        if image:
            image_doc = pymupdf.open()
            image_doc.new_page().insert_text((40, 80), "Printed inside image")
            pix = image_doc[0].get_pixmap()
            page.insert_image(page.rect, stream=pix.tobytes("png"))
            image_doc.close()
    pdf.save(path)
    pdf.close()


def test_odd_utf16_bom_maps_to_corrupt_error() -> None:
    """Malformed UTF-16 never leaks a UnicodeDecodeError."""
    with pytest.raises(CorruptFileError):
        decode_text(b"\xff\xfeA")


def test_utf16_no_bom_detected() -> None:
    """Decode UTF-16LE using alternating NUL byte structure."""
    text, warnings = decode_text("Hello UTF16".encode("utf-16-le"))
    assert text == "Hello UTF16"
    assert any("UTF-16" in warning for warning in warnings)


def test_pdf_signature_after_preamble(tmp_path: Path) -> None:
    """Allow PDF headers placed inside the first 1024 bytes."""
    path = tmp_path / "prefixed.pdf"
    source = tmp_path / "normal.pdf"
    _pdf(source)
    path.write_bytes(b"%comment\n" * 12 + source.read_bytes())
    result = extract_document(path)
    assert result.page_count == 1


def test_docx_zip_compression_ratio_limit(tmp_path: Path) -> None:
    """Compressed oversized XML is blocked before the DOCX parser."""
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "x" * 120_000)
        archive.writestr("[Content_Types].xml", "content")
    with pytest.raises(FileTooLargeError) as exc:
        extract_document(path)
    assert exc.value.code == "zip_compression_ratio_exceeded"


def test_docx_uncompressed_limit(tmp_path: Path) -> None:
    """Cap aggregate uncompressed archive size with a test override."""
    doc = Document()
    doc.add_paragraph("x" * 4096)
    path = tmp_path / "large.docx"
    doc.save(path)
    with pytest.raises(FileTooLargeError) as exc:
        extract_document(path, max_zip_uncompressed_mb=0)
    assert exc.value.code == "zip_uncompressed_too_large"


def test_pdf_page_limit_before_extraction(tmp_path: Path) -> None:
    """Reject too many pages with a distinct error code."""
    path = tmp_path / "multipage.pdf"
    _pdf(path, pages=2)
    with pytest.raises(FileTooLargeError) as exc:
        extract_document(path, max_pages=1)
    assert exc.value.code == "too_many_pages"


def test_extracted_character_limit(tmp_path: Path) -> None:
    """Enforce character limits across non-PDF and PDF extractors."""
    path = tmp_path / "large.txt"
    path.write_text("A long paragraph " * 15, encoding="utf-8")
    with pytest.raises(FileTooLargeError) as exc:
        extract_document(path, max_extracted_chars=10)
    assert exc.value.code == "extracted_text_too_large"


def test_scanned_page_with_footer_triggers_ocr(tmp_path: Path) -> None:
    """A full-page image plus short selectable footer still needs OCR."""
    path = tmp_path / "image-with-footer.pdf"
    _pdf(path, image=True)
    result = extract_document(path)
    assert result.units[0].needs_ocr
    assert "partial_extraction" in result.warnings


def test_fake_ocr_provider_is_used(tmp_path: Path) -> None:
    """An available injected offline OCR provider supplies the page text."""
    path = tmp_path / "ocr.pdf"
    _pdf(path, image=True)

    class FakeOcr:
        available = True

        def recognize(self, page: pymupdf.Page) -> str:
            return "Recognized meaningful words from the scan"

    result = extract_document(path, ocr_provider=FakeOcr())
    assert "Recognized meaningful" in result.units[0].markdown
    assert not result.units[0].needs_ocr


def test_markdown_converter_exception_uses_block_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve page text after a conversion exception."""
    import app.adapters.extractors.pdf as module

    path = tmp_path / "fallback.pdf"
    _pdf(path)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("forced conversion error")

    monkeypatch.setattr(module.pymupdf4llm, "to_markdown", broken)
    result = extract_document(path)
    assert "Content on page" in result.units[0].markdown
    assert any("block fallback used" in warning for warning in result.warnings)


def test_empty_markdown_uses_block_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fallback also handles valid converters returning empty Markdown."""
    import app.adapters.extractors.pdf as module

    path = tmp_path / "empty-conversion.pdf"
    _pdf(path)
    monkeypatch.setattr(module.pymupdf4llm, "to_markdown", lambda *a, **kw: "")
    result = extract_document(path)
    assert "Content on page" in result.units[0].markdown
    assert any("empty Markdown" in warning for warning in result.warnings)


def test_unreadable_blank_pdf(tmp_path: Path) -> None:
    """A blank PDF without images is an empty document, not a success."""
    from app.core.errors import EmptyDocumentError

    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "blank.pdf"
    doc.save(path)
    doc.close()
    with pytest.raises(EmptyDocumentError):
        extract_document(path)


def test_ocr_provider_failure_is_nonfatal_on_partial_scan(tmp_path: Path) -> None:
    """OCR failure retains readable footer and reports the incident."""
    path = tmp_path / "failed-ocr.pdf"
    _pdf(path, image=True)

    class BrokenOcr:
        available = True

        def recognize(self, page: pymupdf.Page) -> str:
            raise RuntimeError("temporary OCR failure")

    result = extract_document(path, ocr_provider=BrokenOcr())
    assert result.units[0].needs_ocr
    assert any("OCR failed" in warning for warning in result.warnings)


def test_tesseract_adapter_availability_and_offline_recognition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover opt-in adapter without installing Tesseract or calling a network."""
    from types import SimpleNamespace

    import app.adapters.extractors.ocr as ocr

    adapter = ocr.TesseractOcrProvider()
    monkeypatch.setattr(ocr.shutil, "which", lambda executable: None)
    assert not adapter.available
    with pytest.raises(RuntimeError, match="unavailable"):
        adapter.recognize(None)  # type: ignore[arg-type]

    monkeypatch.setattr(ocr.shutil, "which", lambda executable: "/fake/tesseract")
    real_import = ocr.importlib.import_module

    def fake_import(name: str) -> object:
        if name == "pytesseract":
            return SimpleNamespace(image_to_string=lambda img: "OCR result")
        if name == "PIL.Image":
            return SimpleNamespace(open=lambda contents: contents)
        return real_import(name)

    monkeypatch.setattr(ocr.importlib, "import_module", fake_import)
    assert adapter.available
    pixmap = SimpleNamespace(tobytes=lambda kind: b"fake-png")
    page = SimpleNamespace(get_pixmap=lambda **kwargs: pixmap)
    assert adapter.recognize(page) == "OCR result"  # type: ignore[arg-type]
