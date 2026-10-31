"""Encoding-aware plaintext and Markdown extraction (IN-1, SRD §8.1)."""

from __future__ import annotations

import re
from pathlib import Path

from charset_normalizer import from_bytes

from app.core.errors import CorruptFileError, EmptyDocumentError
from app.schemas.extraction import ExtractedDocument, ExtractedUnit


def _utf16_without_bom(data: bytes) -> str | None:
    """Detect alternating NUL bytes, rejecting malformed UTF-16."""
    if len(data) < 4:
        return None
    even = data[0::2].count(0)
    odd = data[1::2].count(0)
    pairs = len(data) // 2
    if even >= pairs * 0.6 and odd == 0:
        return data.decode("utf-16-be", errors="strict")
    if odd >= pairs * 0.6 and even == 0:
        return data.decode("utf-16-le", errors="strict")
    return None


def decode_text(data: bytes) -> tuple[str, list[str]]:
    """Decode BOM, UTF-16 without BOM and legacy encodings safely.

    Raises:
        CorruptFileError: For malformed encodings or unexpected binary NULs.
    """
    warnings: list[str] = []
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16", errors="strict")
        elif data.startswith(b"\xef\xbb\xbf"):
            text = data.decode("utf-8-sig", errors="strict")
        else:
            guessed_utf16 = _utf16_without_bom(data)
            if guessed_utf16 is not None:
                text = guessed_utf16
                warnings.append("Detected UTF-16 without BOM")
            else:
                try:
                    text = data.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    best = from_bytes(data).best()
                    if best is None:
                        text = data.decode("utf-8", errors="replace")
                        warnings.append(
                            "Encoding detection failed; UTF-8 replacement used"
                        )
                    else:
                        text = str(best)
                        warnings.append(
                            f"Detected non-UTF-8 encoding: {best.encoding or 'unknown'}"
                        )
    except (UnicodeError, LookupError, ValueError) as exc:
        raise CorruptFileError("Invalid text encoding") from exc
    if "\x00" in text:
        raise CorruptFileError("Text document contains NUL bytes")
    return text.replace("\r\n", "\n").replace("\r", "\n"), warnings


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
