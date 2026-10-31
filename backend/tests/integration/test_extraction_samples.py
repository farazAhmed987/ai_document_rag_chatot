"""Sample-document extraction checks (IN-1–IN-3; SRD §8.1, Appendix A).

Tests are skipped automatically when generated PDFs are not present.
All generated outputs go into temporary directories, never into data/facts.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pymupdf
import pytest

from app.adapters.extractors.factory import extract_document
from app.core.config import REPO_ROOT
from app.services.cleaning import clean_document

SAMPLES = REPO_ROOT / "data" / "sample_docs"
FACTS = REPO_ROOT / "data" / "facts" / "key_facts.json"
STEMS = (
    "Company_Overview",
    "Property_Listings",
    "Services_and_Fees",
    "FAQs",
    "Policies_and_Terms",
)


def _normalize(value: str) -> str:
    """Normalize Markdown emphasis and whitespace for fact verification."""
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\*\*|__|\*|`", "", value)
    return " ".join(value.split()).casefold()


@pytest.mark.sample
def test_all_sample_pdfs_and_variants() -> None:
    """Check PDF page fidelity, removed running matter and readable tables."""
    for stem in STEMS:
        path = SAMPLES / f"{stem}.pdf"
        with pymupdf.open(path) as pdf:
            source_page_count = len(pdf)
        extracted = extract_document(path)
        cleaned = clean_document(extracted)
        assert extracted.page_count == source_page_count
        assert cleaned.page_count == source_page_count
        assert [unit.page_number for unit in cleaned.units] == list(
            range(1, source_page_count + 1)
        )
        markdown = "\n".join(unit.markdown for unit in cleaned.units)
        assert "Himalaya Crest Realty (Pvt.) Ltd. |" not in markdown
        assert not re.search(r"(?m)^\s*Page\s+\d+\s+of\s+\d+\s*$", markdown)
        assert not any(
            unicodedata.category(character) in {"Cc", "Cf"} and character not in "\n\t"
            for character in markdown
        )
    for variant in (
        SAMPLES / "variants" / "Services_and_Fees.docx",
        SAMPLES / "variants" / "FAQs.txt",
        SAMPLES / "variants" / "FAQs.md",
    ):
        assert clean_document(extract_document(variant)).units


@pytest.mark.sample
def test_property_listing_table_rows_remain_whole() -> None:
    """Keep structured PDF tables as single Markdown rows per record."""
    extracted = extract_document(SAMPLES / "Property_Listings.pdf")
    cleaned = clean_document(extracted)
    markdown = "\n".join(unit.markdown for unit in cleaned.units)
    assert any(
        line.strip().startswith("|") and line.strip().endswith("|")
        for line in markdown.splitlines()
    ), "Expected Markdown pipe-table rows from Property_Listings.pdf"
    assert not any("table row" in warning for warning in cleaned.report.warnings)


@pytest.mark.sample
def test_pipeline_verifies_every_key_fact(tmp_path: Path) -> None:
    """Require 170/170 PDF fact coverage after the real extraction+cleaning."""
    report = tmp_path / "pipeline_page_map.json"
    run = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "verify_facts.py"),
            "--docs-dir",
            str(SAMPLES),
            "--facts",
            str(FACTS),
            "--extractor",
            "pipeline",
            "--report-pages",
            str(report),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + "\n" + run.stderr
    page_map = json.loads(report.read_text(encoding="utf-8"))
    assert len(page_map) == 170
    assert all(value["matched"] for value in page_map.values())


@pytest.mark.sample
def test_docx_variant_preserves_all_services_facts() -> None:
    """Verify each canonical Services_and_Fees snippet survives DOCX extraction."""
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    extracted = extract_document(SAMPLES / "variants" / "Services_and_Fees.docx")
    content = _normalize("\n".join(unit.markdown for unit in extracted.units))
    service_facts = [
        fact for fact in facts if fact["document"] == "Services_and_Fees.md"
    ]
    assert service_facts
    for fact in service_facts:
        for snippet in fact["snippets"]:
            target = _normalize(snippet)
            if target in content:
                continue
            if "|" in snippet:
                cells = [_normalize(cell) for cell in snippet.split("|")]
                cells = [cell for cell in cells if cell]
                position = 0
                found = True
                for cell in cells:
                    next_position = content.find(cell, position)
                    if next_position < 0:
                        found = False
                        break
                    position = next_position + len(cell)
                if found:
                    continue
            pytest.fail(f"Missing DOCX fact {fact['id']}: {snippet!r}")


@pytest.mark.sample
def test_every_source_table_row_is_one_cleaned_pdf_line() -> None:
    """Verify every real Markdown row remains a single ordered PDF row (B7)."""
    total = 0
    for stem in ("Property_Listings", "Services_and_Fees"):
        source_path = REPO_ROOT / "data" / "sample_docs_src" / f"{stem}.md"
        source = source_path.read_text(encoding="utf-8")
        cleaned = clean_document(extract_document(SAMPLES / f"{stem}.pdf"))
        lines = [
            line
            for unit in cleaned.units
            for line in unit.markdown.splitlines()
            if line.lstrip().startswith("|")
        ]
        rows = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith("|")
            and not re.match(r"^[|:\-\s]+$", line.strip())
        ]
        assert rows, f"No source table rows in {stem}"
        for row in rows:
            cells = [_normalize(cell) for cell in row.split("|") if _normalize(cell)]
            found = False
            for line in lines:
                position = 0
                text = _normalize(line)
                for cell in cells:
                    match = text.find(cell, position)
                    if match < 0:
                        break
                    position = match + len(cell)
                else:
                    found = True
                    break
            assert found, f"{stem}: source row lost or split: {row!r}"
            total += 1
    assert total > 0
    print(f"Verified source table rows: {total}")


@pytest.mark.sample
def test_faq_text_variants_contain_all_canonical_facts() -> None:
    """The Markdown and TXT FAQ copies retain all canonical FAQ snippets."""
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    faq_facts = [fact for fact in facts if fact["document"] == "FAQs.md"]
    assert faq_facts
    for suffix in ("md", "txt"):
        path = SAMPLES / "variants" / f"FAQs.{suffix}"
        cleaned = clean_document(extract_document(path))
        combined = _normalize("\n".join(unit.markdown for unit in cleaned.units))
        for fact in faq_facts:
            for snippet in fact["snippets"]:
                normalized = _normalize(snippet)
                assert normalized in combined or (
                    "|" in snippet
                    and all(
                        _normalize(cell) in combined
                        for cell in snippet.split("|")
                        if _normalize(cell)
                    )
                ), f"{suffix}: missing FAQ fact {fact['id']}: {snippet!r}"


@pytest.mark.sample
def test_optional_external_sample_corpus() -> None:
    """Optional external documents are readable without OCR or empty pages."""
    external = SAMPLES / "external"
    paths = (
        sorted(
            path
            for path in external.glob("**/*")
            if path.is_file()
            and path.suffix.lower() in {".pdf", ".docx", ".txt", ".md"}
        )
        if external.is_dir()
        else []
    )
    if not paths:
        pytest.skip("No optional external samples")
    for path in paths:
        extracted = extract_document(path)
        assert extracted.units, str(path)
        assert all(
            not unit.needs_ocr and bool(unit.markdown.strip())
            for unit in extracted.units
        ), str(path)
