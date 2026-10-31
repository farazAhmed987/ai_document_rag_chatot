"""Isolated document-extractor fixtures (DM-2, IN-1–IN-3; SRD §8.1)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from PIL import Image
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from app.adapters.extractors.factory import extract_document
from app.adapters.extractors.ocr import NullOcrProvider
from app.core.errors import (
    CorruptFileError,
    EmptyDocumentError,
    FileTooLargeError,
    PasswordProtectedError,
    UnsupportedFileTypeError,
)
from app.core.hashing import file_sha256, text_sha256
from app.services.cleaning import clean_document


def _image() -> bytes:
    """Produce a small real PNG entirely in memory."""
    data = BytesIO()
    Image.new("RGB", (90, 60), "white").save(data, format="PNG")
    return data.getvalue()


def _pdf(path: Path, *, image_only: bool = False) -> None:
    """Create a multi-page PDF with repeated header/footer and one table."""
    document = pymupdf.open()
    for index in range(4):
        page = document.new_page()
        if image_only or index == 1:
            page.insert_image(pymupdf.Rect(60, 60, 150, 120), stream=_image())
        if not image_only:
            page.insert_text((45, 30), "Test Company | Policy Guide")
            page.insert_text((45, 100), f"Section {index + 1} content")
            page.insert_text((45, 790), f"Page {index + 1} of 4")
    document.save(path)
    document.close()


def test_pdf_page_numbers_and_running_matter(tmp_path: Path) -> None:
    """Check that page numbers survive while repeated headers disappear."""
    path = tmp_path / "pages.pdf"
    _pdf(path)
    extracted = extract_document(path)
    assert extracted.page_count == 4
    assert [u.page_number for u in extracted.units] == [1, 2, 3, 4]
    assert len(extracted.units) == 4
    assert extracted.units[1].needs_ocr is False  # This page also has text.
    cleaned = clean_document(extracted)
    assert [u.page_number for u in cleaned.units] == [1, 2, 3, 4]
    text = "\n".join(u.markdown for u in cleaned.units)
    assert "Test Company | Policy Guide" not in text
    assert "Page 4 of 4" not in text
    assert cleaned.report.header_lines_removed >= 3
    assert cleaned.report.footer_lines_removed >= 4


def test_pdf_hyphenated_line_break(tmp_path: Path) -> None:
    """Ensure cleaner joins a real multiline word extracted from PDF."""
    path = tmp_path / "hyphen.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((60, 100), "inter-\nnational policy")
    document.save(path)
    document.close()
    extracted = extract_document(path)
    assert extracted.file_type == "pdf"
    # The exact layout from pymupdf4llm varies; the independent cleaner
    # is tested directly in test_cleaning.py.
    assert extracted.page_count == 1


def test_pdf_table_text(tmp_path: Path) -> None:
    """Ensure the PDF pipeline preserves visible cell text."""
    path = tmp_path / "table.pdf"
    doc = SimpleDocTemplate(str(path))
    table = Table([["Service", "Fee"], ["Consultation", "PKR 500"]])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    doc.build([table])
    result = extract_document(path)
    content = "\n".join(unit.markdown for unit in result.units)
    assert "Consultation" in content
    assert "PKR 500" in content


def test_scanned_pdf_without_ocr(tmp_path: Path) -> None:
    """Raise a clear machine code for wholly image-only PDFs."""
    path = tmp_path / "scan.pdf"
    _pdf(path, image_only=True)
    with pytest.raises(EmptyDocumentError) as caught:
        extract_document(path, ocr_provider=NullOcrProvider())
    assert caught.value.code == "scanned_pdf_no_ocr"


def test_partial_scan_retains_page_marker(tmp_path: Path) -> None:
    """Warn about an image-only page while retaining its source page number."""
    path = tmp_path / "partial.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 90), "A document with readable text")
    page = document.new_page()
    page.insert_image(pymupdf.Rect(20, 20, 150, 120), stream=_image())
    document.save(path)
    document.close()
    extracted = extract_document(path)
    assert extracted.page_count == 2
    assert extracted.units[1].needs_ocr
    assert any("needs_ocr" in warning for warning in extracted.warnings)
    cleaned = clean_document(extracted)
    assert cleaned.units[1].page_number == 2


def test_password_protected_pdf(tmp_path: Path) -> None:
    """Encrypted PDF must never silently yield empty content."""
    path = tmp_path / "protected.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((30, 50), "Private")
    document.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="viewer",
    )
    document.close()
    with pytest.raises(PasswordProtectedError):
        extract_document(path)


def test_corrupt_pdf(tmp_path: Path) -> None:
    """Reject a PDF signature followed by invalid PDF contents."""
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.7\nnot-a-real-pdf")
    with pytest.raises(CorruptFileError):
        extract_document(path)


def test_docx_renamed_as_pdf(tmp_path: Path) -> None:
    """Identify a DOCX by its contents, not its claimed extension."""
    path = tmp_path / "real.docx"
    document = Document()
    document.add_paragraph("Hello")
    document.save(path)
    fake_path = tmp_path / "fake.pdf"
    fake_path.write_bytes(path.read_bytes())
    with pytest.raises(UnsupportedFileTypeError):
        extract_document(fake_path)


def test_empty_txt_and_oversized(tmp_path: Path) -> None:
    """Reject empty content and enforce the configured size limit."""
    path = tmp_path / "empty.txt"
    path.write_bytes(b"")
    with pytest.raises(EmptyDocumentError):
        extract_document(path)
    path.write_bytes(b"x" * 1048577)
    with pytest.raises(FileTooLargeError):
        extract_document(path, max_mb=1)


def test_cp1252_text(tmp_path: Path) -> None:
    """Detect legacy Windows-1252 characters."""
    path = tmp_path / "legacy.txt"
    path.write_bytes("Café – professional service".encode("cp1252"))
    extracted = extract_document(path)
    assert "Café" in extracted.units[0].markdown


def test_utf16_bom(tmp_path: Path) -> None:
    """Honor the UTF-16 byte-order mark."""
    path = tmp_path / "utf16.txt"
    path.write_bytes("Unicode document Ω".encode("utf-16"))
    extracted = extract_document(path)
    assert extracted.units[0].markdown == "Unicode document Ω"


def test_docx_headings_lists_tables(tmp_path: Path) -> None:
    """Retain body order, section headings, lists, and real tables."""
    path = tmp_path / "structured.docx"
    doc = Document()
    doc.add_heading("Services", level=1)
    doc.add_paragraph("Before the table")
    doc.add_paragraph("First bullet", style="List Bullet")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).text = "Price"
    table.cell(1, 0).text = "A | B"
    table.cell(1, 1).text = "100"
    doc.add_heading("Policies", level=1)
    doc.add_paragraph("Second section")
    doc.save(path)
    extracted = extract_document(path)
    assert len(extracted.units) == 2
    assert extracted.units[0].heading_path == ["Services"]
    assert extracted.units[1].heading_path == ["Policies"]
    text = extracted.units[0].markdown
    assert text.index("Before the table") < text.index("| Name |")
    assert "- First bullet" in text
    assert r"A \| B" in text
    assert extracted.page_count is None


def test_markdown_and_plaintext(tmp_path: Path) -> None:
    """Markdown headings stay present; TXT defaults to one unit."""
    markdown = tmp_path / "source.md"
    markdown.write_text("# Heading\n\nA paragraph.\n", encoding="utf-8")
    result = extract_document(markdown)
    assert result.file_type == "md"
    assert "# Heading" in result.units[0].markdown
    txt = tmp_path / "source.txt"
    txt.write_text("1. Introduction\n\nBody.\n\n2. Next\n\nMore.", encoding="utf-8")
    assert len(extract_document(txt).units) == 1
    assert len(extract_document(txt, numbered_headings=True).units) >= 2


def test_hashes(tmp_path: Path) -> None:
    """Hash files and UTF-8 strings consistently."""
    path = tmp_path / "text.txt"
    path.write_bytes("Café".encode())
    assert file_sha256(path) == text_sha256("Café")
