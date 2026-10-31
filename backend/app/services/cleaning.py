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


_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)


def _arabic(character: str) -> bool:
    """Recognize Arabic-script neighbors used by joining format characters."""
    return any(lower <= ord(character) <= upper for lower, upper in _ARABIC_RANGES)


def normalize_unicode(text: str) -> str:
    """Normalize Unicode while retaining Arabic ZWNJ/ZWJ when script-adjacent."""
    source = unicodedata.normalize(
        "NFKC", text.replace("\r\n", "\n").replace("\r", "\n")
    )
    result: list[str] = []
    for index, character in enumerate(source):
        if character in ("\u200c", "\u200d"):
            previous = source[index - 1] if index else ""
            following = source[index + 1] if index + 1 < len(source) else ""
            if _arabic(previous) or _arabic(following):
                result.append(character)
        elif character == "\u00ad":
            continue
        elif character in ("\n", "\t"):
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
        # Even short PDFs only lose a page number at the page boundary.
        return _remove_page_numbers(units, config)

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
        header_counter.update(
            {_signature(lines[i]) for i in top if not _is_page_number(lines[i])}
        )
        footer_counter.update(
            {_signature(lines[i]) for i in bottom if not _is_page_number(lines[i])}
        )
        for i in top:
            if not _is_page_number(lines[i]):
                header_originals.setdefault(_signature(lines[i]), set()).add(
                    lines[i].strip()
                )
        for i in bottom:
            if not _is_page_number(lines[i]):
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
            if (index in top or index in bottom) and _page_marker(
                line, units[len(result)].page_number
            ):
                footers_removed += 1
            elif index in top and signature in recurring_headers:
                headers_removed += 1
            elif index in bottom and signature in recurring_footers:
                footers_removed += 1
            else:
                kept.append(line)
        result.append("\n".join(kept))
    return result, headers_removed, footers_removed


def _page_marker(line: str, page_number: int | None) -> bool:
    """Identify an explicit page label or its own page's bare index."""
    if not _is_page_number(line):
        return False
    if re.search(r"\bPage\s+\d+", line, flags=re.IGNORECASE):
        return True
    digits = re.search(r"\d+", line)
    return (
        digits is not None
        and page_number is not None
        and int(digits.group()) == page_number
    )


def _remove_page_numbers(
    units: list[ExtractedUnit], config: CleaningConfig
) -> tuple[list[str], int, int]:
    """Strip explicit page markers only near PDF page boundaries."""
    result: list[str] = []
    removed = 0
    for unit in units:
        lines = unit.markdown.splitlines()
        if unit.kind != "page":
            result.append(unit.markdown)
            continue
        top = _boundary_candidates(
            lines, from_bottom=False, count=config.boundary_lines
        )
        bottom = _boundary_candidates(
            lines, from_bottom=True, count=config.boundary_lines
        )
        kept: list[str] = []
        for i, line in enumerate(lines):
            if i in (top | bottom) and _page_marker(line, unit.page_number):
                removed += 1
            else:
                kept.append(line)
        result.append("\n".join(kept))
    return result, 0, removed


def _barrier(line: str) -> bool:
    """Prevent wrapping across Markdown structural and metadata boundaries."""
    stripped = line.strip()
    if not stripped:
        return True
    if _is_heading(line) or _is_list(line) or _is_table(line):
        return True
    if re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", stripped):
        return True
    if stripped.startswith((">", "```", "~~~", "<")):
        return True
    if line.startswith(("    ", "\t")):
        return True
    return bool(re.fullmatch(r"[\w . /-]{1,35}:\s+\S.*", stripped))


def rejoin_hard_wrapped(text: str) -> str:
    """Join only PDF/TXT prose; honor Markdown and metadata barriers."""
    result: list[str] = []
    inside_code = False
    for line in text.splitlines():
        stripped = line.strip()
        fence = stripped.startswith(("```", "~~~"))
        if not result:
            result.append(line)
            if fence:
                inside_code = not inside_code
            continue
        previous = result[-1].rstrip()
        if (
            inside_code
            or fence
            or _barrier(line)
            or _barrier(previous)
            or previous.endswith((".", "!", "?", ":", ";", "…"))
        ):
            result.append(line)
        else:
            result[-1] = previous + " " + stripped
        if fence:
            inside_code = not inside_code
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
        # Tables may contain HTML soft breaks inserted by pymupdf4llm.
        text = re.sub(r"(\d)<br\s*/?>(\d)", r"\1\2", text, flags=re.IGNORECASE)
        text = re.sub(
            r"([a-zA-Z]{3,})<br\s*/?>([a-z]{1,2}(?=\b|[|\s]))",
            r"\1\2",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
        if extracted.file_type in {"pdf", "txt"}:
            text = rejoin_hard_wrapped(text)
        text = normalize_whitespace(text)
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
