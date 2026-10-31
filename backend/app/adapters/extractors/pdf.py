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
    FileTooLargeError,
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


def _image_coverage(page: pymupdf.Page) -> tuple[bool, float]:
    """Estimate visible page area covered by raster images, capped at 100%."""
    page_area = max(float(page.rect.width * page.rect.height), 1.0)
    area = 0.0
    items = page.get_image_info(xrefs=False)
    for item in items:
        bounds = item.get("bbox")
        if bounds is not None:
            intersection = pymupdf.Rect(bounds) & page.rect  # type: ignore[no-untyped-call]
            if not intersection.is_empty:
                area += float(intersection.width * intersection.height)
    has_images = bool(items) or bool(page.get_images(full=True))  # type: ignore[no-untyped-call]
    if not has_images:
        blocks = page.get_text("dict").get("blocks", [])  # type: ignore[no-untyped-call]
        has_images = any(block.get("type") == 1 for block in blocks)
    return has_images, min(1.0, area / page_area)


class PdfExtractor:
    """Extract Markdown as one unit per original PDF page."""

    def __init__(
        self,
        ocr_provider: OcrProvider | None = None,
        *,
        max_pages: int | None = None,
        max_extracted_chars: int | None = None,
    ) -> None:
        """Use configured safety limits and the optional offline OCR provider."""
        self.ocr_provider = ocr_provider or NullOcrProvider()
        self.max_pages = max_pages
        self.max_extracted_chars = max_extracted_chars

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

            if self.max_pages is not None and document.page_count > self.max_pages:
                raise FileTooLargeError(
                    f"PDF has {document.page_count} pages (maximum {self.max_pages})",
                    code="too_many_pages",
                )

            units: list[ExtractedUnit] = []
            warnings: list[str] = []
            any_content = False
            any_image = False
            extracted_chars = 0
            incomplete = False
            for index in range(document.page_count):
                page = document[index]
                original_text = page.get_text("text")  # type: ignore[no-untyped-call]
                has_images, coverage = _image_coverage(page)
                any_image = any_image or has_images
                text_chars = len(original_text.strip())
                needs_ocr = has_images and (
                    text_chars < 20 or (text_chars < 200 and coverage >= 0.50)
                )

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
                        incomplete = True
                        # Never quietly treat scanned images as readable prose.
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

                extracted_chars += len(markdown)
                if (
                    self.max_extracted_chars is not None
                    and extracted_chars > self.max_extracted_chars
                ):
                    raise FileTooLargeError(
                        "Extracted text exceeds configured character limit",
                        code="extracted_text_too_large",
                    )
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

            if incomplete:
                warnings.append("partial_extraction")
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
            FileTooLargeError,
        ):
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError("PDF extraction failed") from exc
        finally:
            document.close()  # type: ignore[no-untyped-call]
