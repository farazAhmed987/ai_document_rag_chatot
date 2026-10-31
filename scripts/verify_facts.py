"""Verify generated sample PDFs against the canonical 170 key facts.

SRD Sections 8.1, 15 and Appendix A.
Requirements: DM-2, IN-1, IN-2 and IN-5.

Usage:
    python scripts/verify_facts.py --extractor raw

    python scripts/verify_facts.py \
        --docs-dir data/sample_docs \
        --facts data/facts/key_facts.json \
        --extractor raw \
        --report-pages data/facts/page_map.json

Raw verification updates data/facts/page_map.json by default. Pipeline mode
only writes a report when --report-pages is explicitly supplied (B7).
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DOCS = ROOT / "data" / "sample_docs"
DEFAULT_FACTS = ROOT / "data" / "facts" / "key_facts.json"
DEFAULT_PAGE_MAP = ROOT / "data" / "facts" / "page_map.json"

REQUIRED_TABLE_DOCUMENTS = {
    "Property_Listings.pdf",
    "Services_and_Fees.pdf",
}

DASH_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u00a0": " ",
    }
)


def normalize(value: str) -> str:
    """Normalize source snippets and extracted document text.

    Args:
        value: Input text.

    Returns:
        Unicode NFKC and whitespace-normalized text.

    Markdown emphasis, backticks, typographic quotes and dash
    variations are normalized before comparison.
    """
    text = unicodedata.normalize("NFKC", value)
    text = text.translate(DASH_TRANSLATION)

    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("*", "")
    text = text.replace("`", "")

    text = re.sub(r"\s+", " ", text)

    return text.strip().casefold()


def raw_pdf_pages(path: Path) -> list[str]:
    """Extract ordinary selectable page text using PyMuPDF.

    Args:
        path: PDF path.

    Returns:
        One text string per original PDF page.
    """
    with pymupdf.open(path) as document:
        return [page.get_text() for page in document]


def pipeline_pdf_pages(path: Path) -> list[str]:
    """Use the real Phase 3 extraction and cleaning pipeline.

    Args:
        path: PDF path.

    Returns:
        Cleaned Markdown text grouped by original page number.

    Raises:
        RuntimeError: If Phase 3 has not been implemented.
    """
    try:
        from app.adapters.extractors.factory import extract_document
        from app.services.cleaning import clean_document
    except ImportError as exc:
        raise RuntimeError(
            "Pipeline extraction requires Phase 3. Use --extractor raw during Phase 1."
        ) from exc

    extracted = extract_document(path)
    cleaned = clean_document(extracted)

    page_count = extracted.page_count or 0
    pages = ["" for _ in range(page_count)]

    for unit in cleaned.units:
        if unit.page_number is None:
            continue

        index = unit.page_number - 1

        if 0 <= index < page_count:
            pages[index] += unit.markdown + "\n"

    return pages


def extract_pages(path: Path, mode: str) -> list[str]:
    """Select the configured extraction implementation.

    Args:
        path: Source PDF.
        mode: Either raw or pipeline.

    Returns:
        Text from each original PDF page.
    """
    if mode == "raw":
        return raw_pdf_pages(path)

    if mode == "pipeline":
        return pipeline_pdf_pages(path)

    raise ValueError(f"Unknown extraction mode: {mode}")


def ordered_table_match(normalized_page: str, snippet: str) -> bool:
    """Find ordered table-cell values on one PDF page.

    Args:
        normalized_page: Previously normalized page text.
        snippet: Original Markdown row containing pipe separators.

    Returns:
        True only when every non-empty cell appears in order
        on the same page.
    """
    cells = [normalize(cell) for cell in snippet.split("|")]
    cells = [cell for cell in cells if cell]

    if not cells:
        return False

    offset = 0

    for cell in cells:
        position = normalized_page.find(cell, offset)

        if position < 0:
            return False

        offset = position + len(cell)

    return True


def full_text_match_pages(
    normalized_pages: list[str],
    target: str,
) -> list[int]:
    """Locate exact matches that may cross a PDF page boundary.

    Args:
        normalized_pages: Normalized individual pages.
        target: Normalized snippet.

    Returns:
        Original one-based page numbers containing a match.
    """
    combined = " ".join(normalized_pages)

    if not target:
        return []

    intervals: list[tuple[int, int, int]] = []
    cursor = 0

    for page_number, text in enumerate(normalized_pages, start=1):
        intervals.append((cursor, cursor + len(text), page_number))
        cursor += len(text) + 1

    matches: set[int] = set()
    position = combined.find(target)

    while position >= 0:
        end_position = position + len(target)

        for start, end, page_number in intervals:
            if position < end and end_position > start:
                matches.add(page_number)

        position = combined.find(target, position + 1)

    return sorted(matches)


def ordered_table_row_match(page: str, snippet: str) -> bool:
    """Require ALL normalized source cells in ONE Markdown table line.

    Unlike raw PDF matching, this does not allow cells to be assembled
    from unrelated lines or different rows.
    """
    cells = [normalize(cell) for cell in snippet.split("|")]
    cells = [cell for cell in cells if cell and cell != "---"]
    if not cells:
        return False
    for line in page.splitlines():
        if not line.strip().startswith("|"):
            continue
        normalized = normalize(re.sub(r"<br\s*/?>", " ", line, flags=re.IGNORECASE))
        position = 0
        for cell in cells:
            next_position = normalized.find(cell, position)
            if next_position < 0:
                break
            position = next_position + len(cell)
        else:
            return True
    return False


def match_snippet(
    pages: list[str], snippet: str, *, mode: str = "raw"
) -> tuple[list[int], str]:
    """Match strict single Markdown table rows in pipeline mode."""
    if mode == "pipeline" and "|" in snippet:
        hits = [
            index
            for index, page in enumerate(pages, start=1)
            if ordered_table_row_match(page, snippet)
        ]
        return hits, "table_row" if hits else "not_found"

    target = normalize(snippet)
    if not target:
        return [], "empty_snippet"
    normalized_pages = [normalize(page) for page in pages]
    hits = [
        index for index, page in enumerate(normalized_pages, start=1) if target in page
    ]
    if hits:
        return hits, "exact_page"
    full_pages = full_text_match_pages(normalized_pages, target)
    if full_pages:
        return full_pages, "exact_full_text"
    if mode == "raw" and "|" in snippet:
        table_pages = [
            index
            for index, page in enumerate(normalized_pages, start=1)
            if ordered_table_match(page, snippet)
        ]
        if table_pages:
            return table_pages, "table_cells"
    return [], "not_found"


def inspect_pdf(path: Path) -> dict[str, int]:
    """Check PDF pages, text layers and detectable tables.

    Args:
        path: PDF document.

    Returns:
        Page count, populated text pages and table count.
    """
    with pymupdf.open(path) as document:
        text_pages = 0
        table_count = 0

        for page in document:
            if page.get_text().strip():
                text_pages += 1

            tables = page.find_tables()
            table_count += len(tables.tables)

        return {
            "pages": len(document),
            "text_pages": text_pages,
            "tables": table_count,
        }


def load_facts(path: Path) -> list[dict[str, Any]]:
    """Read and minimally validate the canonical facts array.

    Args:
        path: JSON facts file.

    Returns:
        Fact dictionaries.

    Raises:
        ValueError: When the file is not a usable facts array.
    """
    content = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(content, list) or not content:
        raise ValueError("The facts file must be a non-empty array")

    for index, fact in enumerate(content):
        if not isinstance(fact, dict):
            raise TypeError(f"Fact at index {index} must be an object")

        required = ("id", "document", "snippets")

        if any(key not in fact for key in required):
            raise ValueError(f"Fact at index {index} is missing required fields")

        if (
            not isinstance(fact["snippets"], list)
            or not fact["snippets"]
            or not all(isinstance(value, str) for value in fact["snippets"])
        ):
            raise ValueError(f"Fact {fact['id']} has invalid snippets")

    return content


def verify(
    *,
    docs_dir: Path,
    facts_path: Path,
    extractor: str,
    report_path: Path | None = None,
) -> bool:
    """Verify all facts and write the source-to-page map.

    Args:
        docs_dir: Directory of generated PDFs.
        facts_path: Canonical key_facts.json path.
        extractor: raw or pipeline.
        report_path: Destination for page_map.json.

    Returns:
        True when every snippet and PDF integrity check passes.
    """
    facts = load_facts(facts_path)
    pdf_paths = sorted(docs_dir.glob("*.pdf"))

    if not pdf_paths:
        raise FileNotFoundError(f"No PDFs found in {docs_dir}")

    page_text: dict[str, list[str]] = {}
    pdf_stats: dict[str, dict[str, int]] = {}
    failures: list[str] = []
    outliers: list[str] = []

    print()
    print(f"{'PDF':<31} {'Pages':>6} {'Text':>6} {'Tables':>7}")
    print("-" * 54)

    for path in pdf_paths:
        stats = inspect_pdf(path)
        pdf_stats[path.name] = stats

        print(
            f"{path.name:<31} "
            f"{stats['pages']:>6} "
            f"{stats['text_pages']:>6} "
            f"{stats['tables']:>7}"
        )

        if not 5 <= stats["pages"] <= 12:
            outliers.append(
                f"{path.name}: {stats['pages']} pages (recommended range: 5 to 12)"
            )

        if stats["text_pages"] != stats["pages"]:
            failures.append(f"{path.name}: at least one page has no text layer")

        if path.name in REQUIRED_TABLE_DOCUMENTS and stats["tables"] < 1:
            failures.append(f"{path.name}: no detectable table found")

        page_text[path.name] = extract_pages(path, extractor)

    for required_pdf in sorted(REQUIRED_TABLE_DOCUMENTS):
        if required_pdf not in pdf_stats:
            failures.append(f"Required PDF is missing: {required_pdf}")

    page_map: dict[str, dict[str, object]] = {}
    match_modes: Counter[str] = Counter()
    matching_facts = 0

    print()
    print("Fact verification:")
    print("-" * 54)

    for fact in facts:
        fact_id = str(fact["id"])
        source_name = str(fact["document"])
        pdf_name = Path(source_name).with_suffix(".pdf").name

        snippets = fact["snippets"]
        pages = page_text.get(pdf_name)

        snippet_results: list[dict[str, object]] = []
        matched_pages: set[int] = set()
        modes: set[str] = set()

        fully_matched = pages is not None

        if pages is None:
            failures.append(f"{fact_id}: source PDF missing: {pdf_name}")

        for snippet in snippets:
            found_pages, mode = (
                match_snippet(pages, snippet, mode=extractor)
                if pages is not None
                else ([], "missing_pdf")
            )

            match_modes[mode] += 1

            if not found_pages:
                fully_matched = False
                failures.append(
                    f"{fact_id} ({pdf_name}): "
                    "snippet not found using exact/text/table "
                    f"matching: {snippet!r}"
                )

            matched_pages.update(found_pages)
            modes.add(mode)

            snippet_results.append(
                {
                    "snippet": snippet,
                    "pages": found_pages,
                    "match_mode": mode,
                }
            )

        page_map[fact_id] = {
            "document": pdf_name,
            "pages": sorted(matched_pages),
            "match_mode": sorted(modes),
            "matched": fully_matched,
            "snippets": snippet_results,
        }

        if fully_matched:
            matching_facts += 1

    destination = report_path
    if destination is None and extractor == "raw":
        destination = DEFAULT_PAGE_MAP
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(page_map, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(f"Key facts matched: {matching_facts}/{len(facts)}")
    coverage = 100 * matching_facts / len(facts)
    print(f"Fact coverage: {coverage:.2f}%")
    print(f"Page map: {destination if destination else 'not written'}")

    print()
    print("Snippet match modes:")

    for mode, count in sorted(match_modes.items()):
        print(f"  {mode}: {count}")

    if outliers:
        print()
        print("Page count warnings:")

        for message in outliers:
            print(f"  WARNING: {message}")

    if failures:
        print()
        print("Verification failures:")

        for message in failures:
            print(f"  FAIL: {message}")

        return False

    print()
    print("PASS: all key facts and PDF integrity checks passed")
    return True


def main() -> None:
    """Parse command-line arguments and run verification."""
    parser = argparse.ArgumentParser(
        description="Verify generated PDFs against key facts."
    )

    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=DEFAULT_DOCS,
    )
    parser.add_argument(
        "--facts",
        type=Path,
        default=DEFAULT_FACTS,
    )
    parser.add_argument(
        "--extractor",
        choices=("raw", "pipeline"),
        default="raw",
    )
    parser.add_argument(
        "--report-pages",
        type=Path,
        default=None,
    )

    args = parser.parse_args()

    passed = verify(
        docs_dir=args.docs_dir,
        facts_path=args.facts,
        extractor=args.extractor,
        report_path=args.report_pages,
    )

    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
