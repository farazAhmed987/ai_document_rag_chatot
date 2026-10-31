"""Typed extraction and cleaning results (SRD §8.1, IN-1–IN-3).

Units retain source-page numbering even when a page becomes empty after
cleaning, avoiding any shift in future source citations (IN-5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(slots=True)
class ExtractedUnit:
    """One PDF page or one document section, before or after cleaning."""

    kind: Literal["page", "section"]
    index: int
    page_number: int | None
    heading_path: list[str]
    markdown: str
    needs_ocr: bool = False


@dataclass(slots=True)
class ExtractedDocument:
    """Document text plus extraction metadata and nonfatal warnings."""

    filename: str
    file_type: Literal["pdf", "docx", "txt", "md"]
    units: list[ExtractedUnit]
    page_count: int | None
    metadata: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CleaningReport:
    """Observable changes made by the deterministic cleaning pipeline."""

    header_lines_removed: int = 0
    footer_lines_removed: int = 0
    dehyphenations: int = 0
    characters_before: int = 0
    characters_after: int = 0
    empty_units_dropped: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CleanedDocument:
    """Cleaned Markdown units and cleaning statistics."""

    filename: str
    file_type: Literal["pdf", "docx", "txt", "md"]
    units: list[ExtractedUnit]
    page_count: int | None
    metadata: dict[str, str]
    warnings: list[str]
    report: CleaningReport
