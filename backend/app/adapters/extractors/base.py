"""Document-extraction and OCR interfaces (IN-1, IN-3; SRD §8.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pymupdf

from app.schemas.extraction import ExtractedDocument


class DocumentExtractor(Protocol):
    """Implement extraction without database or network access."""

    def extract(
        self, path: Path, *, original_filename: str | None = None
    ) -> ExtractedDocument:
        """Extract one validated document with its original structure."""
        ...


class OcrProvider(Protocol):
    """Optional local OCR hook; never contacts a remote service."""

    @property
    def available(self) -> bool:
        """Return whether this provider can perform OCR."""
        ...

    def recognize(self, page: pymupdf.Page) -> str:
        """Read textual content from one PDF page image."""
        ...
