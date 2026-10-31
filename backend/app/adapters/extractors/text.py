"""Encoding-aware plaintext and Markdown extraction (IN-1, SRD §8.1)."""

from __future__ import annotations

import re
from pathlib import Path

from charset_normalizer import from_bytes

from app.core.errors import CorruptFileError, EmptyDocumentError
from app.schemas.extraction import ExtractedDocument, ExtractedUnit


def decode_text(data: bytes) -> tuple[str, list[str]]:
    """Decode text using BOM, UTF-8, then charset-normalizer.

    Args:
        data: File contents.

    Returns:
        Decoded text and any warnings about loss of fidelity.
    """
    warnings: list[str] = []
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="strict")
    elif data.startswith(b"\xef\xbb\xbf"):
        text = data.decode("utf-8-sig", errors="strict")
    else:
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            result = from_bytes(data).best()
            if result is not None:
                text = str(result)
                warnings.append(
                    f"Detected non-UTF-8 encoding: {result.encoding or 'unknown'}"
                )
            else:
                text = data.decode("utf-8", errors="replace")
                warnings.append("Encoding detection failed; UTF-8 replacement used")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise CorruptFileError("Text document contains NUL bytes")
    return text, warnings


class TextExtractor:
    """Extract TXT or MD text, retaining Markdown headings when present."""

    def __init__(
        self, *, markdown: bool = False, numbered_headings: bool = False
    ) -> None:
        """Choose Markdown mode and optional TXT numbered heading splitting."""
        self.markdown = markdown
        self.numbered_headings = numbered_headings

    def extract(
        self, path: Path, *, original_filename: str | None = None
    ) -> ExtractedDocument:
        """Decode and optionally split textual sections.

        Args:
            path: Validated text path.
            original_filename: Original upload filename.
        """
        text, warnings = decode_text(path.read_bytes())
        if not text.strip():
            raise EmptyDocumentError("Text document is empty")
        parts = [text]
        if self.numbered_headings and not self.markdown:
            parts = re.split(r"\n\s*\n(?=\d+(?:\.\d+)*[.)]?\s+\S)", text)
        units: list[ExtractedUnit] = []
        for part in parts:
            if not part.strip():
                continue
            headings = (
                [
                    line.lstrip("# ").strip()
                    for line in part.splitlines()
                    if re.match(r"^#{1,6}\s+", line)
                ]
                if self.markdown
                else []
            )
            units.append(
                ExtractedUnit(
                    kind="section",
                    index=len(units),
                    page_number=None,
                    heading_path=headings,
                    markdown=part,
                )
            )
        return ExtractedDocument(
            filename=original_filename or path.name,
            file_type="md" if self.markdown else "txt",
            units=units,
            page_count=None,
            warnings=warnings,
        )
