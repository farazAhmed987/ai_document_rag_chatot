"""Rule-by-rule cleaning tests (IN-2, SRD §8.1 step 4)."""

from __future__ import annotations

from app.schemas.extraction import ExtractedDocument, ExtractedUnit
from app.services.cleaning import (
    CleaningConfig,
    clean_document,
    dehyphenate,
    normalize_unicode,
    normalize_whitespace,
    rejoin_hard_wrapped,
    table_warnings,
)


def _doc(*texts: str) -> ExtractedDocument:
    """Create PDF-like page units without relying on any sample files."""
    return ExtractedDocument(
        filename="fixture.pdf",
        file_type="pdf",
        units=[
            ExtractedUnit("page", index, index + 1, [], text)
            for index, text in enumerate(texts)
        ],
        page_count=len(texts),
    )


def test_unicode_controls_and_nbsp() -> None:
    """NFKC and control removal keep meaningful punctuation."""
    text = "Ａ\u00a0B\u200b\ufeff\u00ad\x01\n‘quoted’ — test"
    assert normalize_unicode(text) == "A B\n‘quoted’ — test"


def test_dehyphenation_is_selective() -> None:
    """Only line-break hyphens followed by lowercase letters are joined."""
    result, count = dehyphenate("inter-\nnational\nwell-known\nPart-\nTime")
    assert result == "international\nwell-known\nPart-\nTime"
    assert count == 1


def test_headers_and_page_footers() -> None:
    """Detect repeated running matter even when page digits differ."""
    document = _doc(
        *(
            f"Company | Document\n# Section {i}\nUnique paragraph {i}.\nPage {i} of 4"
            for i in range(1, 5)
        )
    )
    cleaned = clean_document(document)
    combined = "\n".join(unit.markdown for unit in cleaned.units)
    assert "Company | Document" not in combined
    assert "Page 1 of 4" not in combined
    assert "# Section 1" in combined
    assert cleaned.report.header_lines_removed == 4
    assert cleaned.report.footer_lines_removed == 4


def test_header_threshold() -> None:
    """Require at least three pages before deleting repeated header text."""
    document = _doc("Header\nFirst", "Header\nSecond")
    cleaned = clean_document(document)
    assert all("Header" in unit.markdown for unit in cleaned.units)
    assert cleaned.report.header_lines_removed == 0
    custom = CleaningConfig(boundary_lines=2, repetition_ratio=1, minimum_pages=2)
    cleaned = clean_document(document, custom)
    assert all("Header" not in unit.markdown for unit in cleaned.units)


def test_rejoin_hard_wrapped_without_flattening_structure() -> None:
    """Keep headings, lists, tables, sentences and paragraph breaks intact."""
    source = (
        "This is one\nparagraph\n\n# Heading\n- Bullet\n"
        "| Col | Value |\n| --- | --- |\n| A | B |\nEnd.\nNext line"
    )
    result = rejoin_hard_wrapped(source)
    assert "This is one paragraph" in result
    assert "\n\n# Heading\n- Bullet\n" in result
    assert "| A | B |" in result
    assert "End.\nNext line" in result


def test_whitespace_preserves_markdown_structures() -> None:
    """Do not collapse table and heading spacing or add excessive blank lines."""
    text = "#  Heading\n\n\n  Body   text\t  here  \n| A  | B  |\n"
    result = normalize_whitespace(text)
    assert result == "#  Heading\n\nBody text here\n| A  | B  |"


def test_table_column_mismatch_warning() -> None:
    """Find inconsistent rows within individual contiguous tables."""
    assert table_warnings("| a | b |\n| x | y |") == []
    warnings = table_warnings("| a | b |\n| x | y | z |")
    assert len(warnings) == 1
    assert "3 columns, expected 2" in warnings[0]
    assert table_warnings("| a\\|b | c |\n| x | y |") == []


def test_empty_page_keeps_number() -> None:
    """Deleting only page-number content must not shift later page locators."""
    cleaned = clean_document(_doc("Page 1 of 3", "Useful text.", "Page 3 of 3"))
    assert [u.page_number for u in cleaned.units] == [1, 2, 3]
    assert cleaned.units[0].markdown == ""
    assert cleaned.units[2].markdown == ""
    assert cleaned.report.empty_units_dropped == 2


def test_report_character_counts() -> None:
    """Ensure the cleaning report exposes before/after character totals."""
    cleaned = clean_document(_doc("A  B\n\n\nC."))
    assert cleaned.report.characters_before > cleaned.report.characters_after
    assert cleaned.report.dehyphenations == 0
