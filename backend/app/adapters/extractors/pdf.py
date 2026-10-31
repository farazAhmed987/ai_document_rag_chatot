"""Page-preserving PDF extraction using PyMuPDF and pymupdf4llm.

SRD §8.1 step 3, IN-1 and IN-3. Falls back to readable text blocks on a
per-page basis if Markdown conversion fails. OCR remains opt-in and offline.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pymupdf4llm  # type: ignore[import-untyped]

from app.adapters.extractors.base import OcrProvider
from app.adapters.extractors.ocr import NullOcrProvider
from app.core.errors import (
    CorruptFileError,
    EmptyDocumentError,
    ExtractionError,
    PasswordProtectedError,
)
from app.schemas.extraction import ExtractedDocument, ExtractedUnit


def _block_fallback(page: pymupdf.Page) -> str:
    """Retrieve text blocks in reading order when Markdown conversion fails."""
    parts: list[str] = []
    for block in page.get_text("blocks", sort=True):  # type: ignore[no-untyped-call]
        if len(block) > 4 and isinstance(block[4], str):
            part = block[4].strip()
            if part:
                parts.append(part)
    return "\n\n".join(parts)


def _has_images(page: pymupdf.Page) -> bool:
    """Check for embedded image objects or image blocks."""
    if page.get_images(full=True):  # type: ignore[no-untyped-call]
        return True
    return any(
        block.get("type") == 1
        for block in page.get_text("dict")["blocks"]  # type: ignore[no-untyped-call]
    )


class PdfExtractor:
    """Extract Markdown as one unit per original PDF page."""

    def __init__(self, ocr_provider: OcrProvider | None = None) -> None:
        """Use the specified provider or no OCR by default."""
        self.ocr_provider = ocr_provider or NullOcrProvider()

    def extract(
        self, path: Path, *, original_filename: str | None = None
    ) -> ExtractedDocument:
        """Extract pages without losing original one-based page numbers.

        Args:
            path: Validated PDF path.
            original_filename: Client-supplied display filename.

        Raises:
            CorruptFileError: If PDF bytes cannot be parsed.
            PasswordProtectedError: If the file needs a password.
            EmptyDocumentError: If content is empty or scanned without OCR.
            ExtractionError: For unexpected failures.
        """
        try:
            document = pymupdf.open(path)  # type: ignore[no-untyped-call]
        except (pymupdf.FileDataError, ValueError, RuntimeError) as exc:
            raise CorruptFileError("PDF is corrupt or unreadable") from exc

        try:
            if document.needs_pass:
                raise PasswordProtectedError("PDF requires a password")
            if document.page_count == 0:
                raise EmptyDocumentError("PDF has no pages")

            units: list[ExtractedUnit] = []
            warnings: list[str] = []
            any_content = False
            any_image = False
            for index in range(document.page_count):
                page = document[index]
                original_text = page.get_text("text")  # type: ignore[no-untyped-call]
                has_images = _has_images(page)
                any_image = any_image or has_images
                needs_ocr = len(original_text.strip()) < 20 and has_images

                try:
                    markdown = pymupdf4llm.to_markdown(
                        document,
                        pages=[index],
                        write_images=False,
                        page_chunks=False,
                    )
                    if not isinstance(markdown, str):
                        raise TypeError("Unexpected Markdown output")
                except Exception as exc:  # noqa: BLE001
                    markdown = _block_fallback(page)
                    warnings.append(
                        f"page {index + 1}: Markdown conversion failed "
                        f"({type(exc).__name__}); block fallback used"
                    )

                if not markdown.strip() and original_text.strip():
                    markdown = _block_fallback(page)
                    warnings.append(
                        f"page {index + 1}: empty Markdown; block fallback used"
                    )

                if needs_ocr:
                    warnings.append(f"page {index + 1}: needs_ocr")
                    if not self.ocr_provider.available:
                        # An optional converter OCR engine must not bypass
                        # the configured offline OCR policy.
                        markdown = original_text if original_text.strip() else ""
                    if self.ocr_provider.available:
                        try:
                            ocr_text = self.ocr_provider.recognize(page).strip()
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(
                                f"page {index + 1}: OCR failed ({type(exc).__name__})"
                            )
                        else:
                            if ocr_text:
                                markdown = ocr_text
                                needs_ocr = False

                if markdown.strip():
                    any_content = True

                units.append(
                    ExtractedUnit(
                        kind="page",
                        index=index,
                        page_number=index + 1,
                        heading_path=[],
                        markdown=markdown,
                        needs_ocr=needs_ocr,
                    )
                )

            if not any_content:
                if any_image and not self.ocr_provider.available:
                    raise EmptyDocumentError(
                        "Scanned PDF has no extractable text and OCR is unavailable",
                        code="scanned_pdf_no_ocr",
                    )
                raise EmptyDocumentError("PDF contains no extractable text")

            return ExtractedDocument(
                filename=original_filename or path.name,
                file_type="pdf",
                units=units,
                page_count=document.page_count,
                metadata={
                    str(key): str(value)
                    for key, value in (document.metadata or {}).items()
                    if value is not None
                },
                warnings=warnings,
            )
        except (
            CorruptFileError,
            PasswordProtectedError,
            EmptyDocumentError,
        ):
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError("PDF extraction failed") from exc
        finally:
            document.close()  # type: ignore[no-untyped-call]
