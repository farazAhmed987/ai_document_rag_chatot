"""Part B RED tests for cleaning safety (IN-2; SRD §8.1)."""

from __future__ import annotations

from app.schemas.extraction import ExtractedDocument, ExtractedUnit
from app.services.cleaning import clean_document, normalize_unicode, rejoin_hard_wrapped


def _doc(file_type: str, *pages: str) -> ExtractedDocument:
    """Build small page/section fixtures without changing sample files."""
    is_pdf = file_type == "pdf"
    return ExtractedDocument(
        filename=f"fixture.{file_type}",
        file_type=file_type,
        units=[
            ExtractedUnit(
                "page" if is_pdf else "section",
                index,
                index + 1 if is_pdf else None,
                [],
                text,
            )
            for index, text in enumerate(pages)
        ],
        page_count=len(pages) if is_pdf else None,
    )


def test_boundary_page_numbers_preserve_body_numbers() -> None:
    """Do not delete stand-alone years or table values in PDF body."""
    cleaned = clean_document(_doc("pdf", "Page 1 of 2\nIntroduction\n2026\n42\nNotes"))
    text = cleaned.units[0].markdown
    assert "Page 1 of 2" not in text
    assert "2026" in text and "42" in text


def test_bare_page_number_only_boundary_removed() -> None:
    """Keep a body 3 but strip boundary page 3 on page three."""
    cleaned = clean_document(_doc("pdf", "Body\n3\nMore", "Body\n2", "3\nBody"))
    assert "3" in cleaned.units[0].markdown
    assert "3" not in cleaned.units[2].markdown


def test_markdown_and_docx_units_do_not_rejoin_lines() -> None:
    """MD and DOCX source line breaks must not be reconstructed."""
    for kind in ("md", "docx"):
        cleaned = clean_document(
            _doc(kind, "Phone: 123\nEmail: a@example.test\n---\n> quote\nNext")
        )
        lines = cleaned.units[0].markdown.splitlines()
        assert "Phone: 123" in lines
        assert "Email: a@example.test" in lines
        assert "---" in lines
        assert "> quote" in lines


def test_pdf_prose_joins_without_crossing_barriers() -> None:
    """PDF prose may join but dividers, quotes and code must remain isolated."""
    text = "Long prose\ncontinued text\n---\n> quote\n```python\nx = 1\n```\nContact: yes\nnext"
    result = rejoin_hard_wrapped(text)
    assert "Long prose continued text" in result
    for barrier in ("---", "> quote", "```python", "Contact: yes"):
        assert barrier in result.splitlines()


def test_urdu_zwnj_and_zwj_are_preserved() -> None:
    """Arabic-script joining marks are retained, Latin ones removed."""
    assert "ا\u200cب" in normalize_unicode("ا\u200cب")
    assert "ا\u200dب" in normalize_unicode("ا\u200dب")
    assert normalize_unicode("a\u200cb") == "ab"
    assert normalize_unicode("a\u200db") == "ab"
