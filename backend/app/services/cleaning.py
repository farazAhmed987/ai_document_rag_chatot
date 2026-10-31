"""Pure, structure-aware document cleaning (IN-2; SRD §8.1 step 4).

Rules run in the specified order. PDF page placeholders are never discarded;
empty text is retained with its original one-based page number (IN-5).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from app.schemas.extraction import (
    CleanedDocument,
    CleaningReport,
    ExtractedDocument,
    ExtractedUnit,
)


@dataclass(frozen=True, slots=True)
class CleaningConfig:
    """Thresholds controlling repeated running matter detection."""

    boundary_lines: int = 3
    repetition_ratio: float = 0.60
    minimum_pages: int = 3

    def __post_init__(self) -> None:
        """Reject invalid cleaning settings."""
        if self.boundary_lines < 1 or self.minimum_pages < 1:
            raise ValueError("Boundary lines and minimum pages must be positive")
        if not 0 < self.repetition_ratio <= 1:
            raise ValueError("repetition_ratio must be between 0 and 1")


def normalize_unicode(text: str) -> str:
    """Apply NFKC and remove invisible/control codepoints but retain newlines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    result: list[str] = []
    for character in unicodedata.normalize("NFKC", text):
        if character == "\u00ad":
            continue
        if character in ("\n", "\t"):
            result.append(character)
        elif unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
            continue
        else:
            result.append(" " if character == "\u00a0" else character)
    return "".join(result)


_DEHYPHEN = re.compile(r"(?<=[^\W\d_])-[ \t]*\n[ \t]*(?=[a-z])", re.UNICODE)


def dehyphenate(text: str) -> tuple[str, int]:
    """Join hyphen-newline-lowercase breaks, without touching real hyphens.

    Args:
        text: Normalized Markdown.

    Returns:
        Cleaned text and number of joins.
    """
    return _DEHYPHEN.subn("", text)


def _is_heading(line: str) -> bool:
    return bool(re.match(r"^\s*#{1,6}\s+\S", line))


def _is_table(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _is_list(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[-+*]\s+|\d+[.)]\s+)", line))


def _is_page_number(line: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:-\s*)?(?:Page\s+)?\d+"
            r"(?:\s+of\s+\d+)?(?:\s*-)?\s*",
            line,
            re.IGNORECASE,
        )
    )


def _boundary_candidates(
    lines: list[str], *, from_bottom: bool, count: int
) -> set[int]:
    """Get the first/last K eligible non-table and non-heading lines."""
    indices = range(len(lines) - 1, -1, -1) if from_bottom else range(len(lines))
    matches: set[int] = set()
    for index in indices:
        line = lines[index]
        if not line.strip() or _is_table(line) or _is_heading(line) or _is_list(line):
            continue
        matches.add(index)
        if len(matches) >= count:
            break
    return matches


def _signature(line: str) -> str:
    """Replace digit runs to normalize changing PDF page numbers."""
    return re.sub(r"\d+", "#", " ".join(line.casefold().split()))


def remove_running_matter(
    units: list[ExtractedUnit], config: CleaningConfig
) -> tuple[list[str], int, int]:
    """Remove headers/footers recurring across sufficiently many PDF pages.

    Args:
        units: Unicode-normalized PDF page units.
        config: Detection threshold configuration.

    Returns:
        Per-unit text, header removals, footer removals.
    """
    page_units = [unit for unit in units if unit.kind == "page"]
    if len(page_units) < config.minimum_pages:
        # Still remove isolated page-number lines from pages.
        return _remove_page_numbers(units)

    required = max(
        config.minimum_pages,
        math.ceil(len(page_units) * config.repetition_ratio),
    )
    header_counter: Counter[str] = Counter()
    footer_counter: Counter[str] = Counter()
    header_originals: dict[str, set[str]] = {}
    footer_originals: dict[str, set[str]] = {}
    selections: list[tuple[set[int], set[int], list[str]]] = []

    for unit in units:
        lines = unit.markdown.splitlines()
        if unit.kind != "page":
            selections.append((set(), set(), lines))
            continue
        top = _boundary_candidates(
            lines, from_bottom=False, count=config.boundary_lines
        )
        bottom = _boundary_candidates(
            lines, from_bottom=True, count=config.boundary_lines
        )
        selections.append((top, bottom, lines))
        header_counter.update({_signature(lines[i]) for i in top})
        footer_counter.update({_signature(lines[i]) for i in bottom})
        for i in top:
            header_originals.setdefault(_signature(lines[i]), set()).add(
                lines[i].strip()
            )
        for i in bottom:
            footer_originals.setdefault(_signature(lines[i]), set()).add(
                lines[i].strip()
            )

    # Numerically changing body prose (such as "Unique paragraph 1") must
    # not be mistaken for running matter merely because digits normalize.
    # Varying numeric signatures are removed only if explicitly page-related.
    recurring_headers = {
        signature
        for signature, frequency in header_counter.items()
        if frequency >= required
        and (len(header_originals[signature]) == 1 or "page" in signature)
    }
    recurring_footers = {
        signature
        for signature, frequency in footer_counter.items()
        if frequency >= required
        and (len(footer_originals[signature]) == 1 or "page" in signature)
    }

    result: list[str] = []
    headers_removed = 0
    footers_removed = 0
    for top, bottom, lines in selections:
        kept: list[str] = []
        for index, line in enumerate(lines):
            signature = _signature(line)
            if _is_page_number(line):
                footers_removed += 1
            elif index in top and signature in recurring_headers:
                headers_removed += 1
            elif index in bottom and signature in recurring_footers:
                footers_removed += 1
            else:
                kept.append(line)
        result.append("\n".join(kept))
    return result, headers_removed, footers_removed


def _remove_page_numbers(
    units: list[ExtractedUnit],
) -> tuple[list[str], int, int]:
    """Strip page-number lines from PDFs only, never from DOCX/TXT/MD."""
    result: list[str] = []
    removed = 0
    for unit in units:
        lines = []
        for line in unit.markdown.splitlines():
            if unit.kind == "page" and _is_page_number(line):
                removed += 1
            else:
                lines.append(line)
        result.append("\n".join(lines))
    return result, 0, removed


def rejoin_hard_wrapped(text: str) -> str:
    """Rejoin prose lines, keeping paragraphs, tables, lists and headings."""
    result: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not result:
            result.append(line)
            continue
        previous = result[-1].rstrip()
        if (
            not stripped
            or not previous.strip()
            or _is_heading(stripped)
            or _is_list(stripped)
            or _is_table(stripped)
            or _is_heading(previous)
            or _is_list(previous)
            or _is_table(previous)
            or previous.endswith((".", "!", "?", ":", ";", "…"))
        ):
            result.append(line)
        else:
            result[-1] = previous + " " + stripped
    return "\n".join(result)


def _column_count(line: str) -> int:
    """Count Markdown table columns, ignoring escaped pipe characters."""
    stripped = line.strip()
    parts = re.split(r"(?<!\\)\|", stripped)
    if stripped.startswith("|"):
        parts = parts[1:]
    if stripped.endswith("|"):
        parts = parts[:-1]
    return len(parts)


def table_warnings(text: str) -> list[str]:
    """Report inconsistent column counts for each contiguous Markdown table."""
    warnings: list[str] = []
    expected: int | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not _is_table(line):
            expected = None
            continue
        count = _column_count(line)
        if expected is None:
            expected = count
        elif count != expected:
            warnings.append(
                f"table row {line_number}: {count} columns, expected {expected}"
            )
    return warnings


def normalize_whitespace(text: str) -> str:
    """Trim and collapse prose whitespace while preserving structural rows."""
    lines: list[str] = []
    previous_blank = False
    for line in text.splitlines():
        if _is_table(line) or _is_heading(line) or _is_list(line):
            cleaned = line.rstrip()
        else:
            cleaned = re.sub(r"[ \t]{2,}", " ", line).strip()
        if not cleaned:
            if not previous_blank and lines:
                lines.append("")
            previous_blank = True
        else:
            lines.append(cleaned)
            previous_blank = False
    return "\n".join(lines).strip()


def clean_document(
    extracted: ExtractedDocument, config: CleaningConfig | None = None
) -> CleanedDocument:
    """Apply Unicode, dehyphenation, running matter and layout cleaning.

    Args:
        extracted: Full extracted document.
        config: Optional settings for repeated headers and footers.

    Returns:
        Cleaned document with intact page map and cleaning report.
    """
    settings = config or CleaningConfig()
    report = CleaningReport(
        characters_before=sum(len(unit.markdown) for unit in extracted.units)
    )
    interim: list[ExtractedUnit] = []
    for unit in extracted.units:
        text = normalize_unicode(unit.markdown)
        text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
        text, count = dehyphenate(text)
        report.dehyphenations += count
        interim.append(
            ExtractedUnit(
                kind=unit.kind,
                index=unit.index,
                page_number=unit.page_number,
                heading_path=list(unit.heading_path),
                markdown=text,
                needs_ocr=unit.needs_ocr,
            )
        )

    texts, report.header_lines_removed, report.footer_lines_removed = (
        remove_running_matter(interim, settings)
    )
    cleaned_units: list[ExtractedUnit] = []
    for unit, text in zip(interim, texts, strict=True):
        text = normalize_whitespace(rejoin_hard_wrapped(text))
        for message in table_warnings(text):
            report.warnings.append(f"unit {unit.index}: {message}")
        if not text:
            report.empty_units_dropped += 1
            # Keep an empty placeholder and its source page number.
        cleaned_units.append(
            ExtractedUnit(
                kind=unit.kind,
                index=unit.index,
                page_number=unit.page_number,
                heading_path=list(unit.heading_path),
                markdown=text,
                needs_ocr=unit.needs_ocr,
            )
        )
    report.characters_after = sum(len(unit.markdown) for unit in cleaned_units)
    report.warnings.extend(extracted.warnings)
    return CleanedDocument(
        filename=extracted.filename,
        file_type=extracted.file_type,
        units=cleaned_units,
        page_count=extracted.page_count,
        metadata=dict(extracted.metadata),
        warnings=list(extracted.warnings),
        report=report,
    )
