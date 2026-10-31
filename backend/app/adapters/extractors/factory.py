"""Strict file validation and extractor selection (DM-2; SRD §8.1 step 1).

Content is checked independently from its extension. This prevents DOCX/PDF
renaming and binary files masquerading as TXT or Markdown uploads.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import filetype  # type: ignore[import-untyped]

from app.adapters.extractors.base import DocumentExtractor, OcrProvider
from app.adapters.extractors.docx import DocxExtractor
from app.adapters.extractors.pdf import PdfExtractor
from app.adapters.extractors.text import TextExtractor, decode_text
from app.core.config import get_settings
from app.core.errors import (
    CorruptFileError,
    EmptyDocumentError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from app.schemas.extraction import ExtractedDocument

SUPPORTED = frozenset({".pdf", ".docx", ".txt", ".md"})


def _validate_content(
    path: Path, suffix: str, data: bytes, *, max_zip_uncompressed_mb: int
) -> None:
    """Reject signatures inconsistent with the claimed extension."""
    is_pdf = b"%PDF-" in data[:1024]
    is_zip = zipfile.is_zipfile(path)
    guessed = filetype.guess(data[:261])

    if suffix == ".pdf":
        if not is_pdf:
            raise UnsupportedFileTypeError("File extension .pdf does not match content")
        return

    if suffix == ".docx":
        if not is_zip:
            raise UnsupportedFileTypeError(
                "File extension .docx does not match content"
            )
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                total = sum(info.file_size for info in entries)
                if total > max_zip_uncompressed_mb * 1024 * 1024:
                    raise FileTooLargeError(
                        "DOCX uncompressed content exceeds configured limit",
                        code="zip_uncompressed_too_large",
                    )
                for info in entries:
                    if info.file_size and (
                        not info.compress_size
                        or info.file_size / info.compress_size > 100
                    ):
                        raise FileTooLargeError(
                            "DOCX entry exceeds safe compression ratio",
                            code="zip_compression_ratio_exceeded",
                        )
                if "word/document.xml" not in archive.namelist():
                    raise CorruptFileError("DOCX archive has no word/document.xml")
        except zipfile.BadZipFile as exc:
            raise CorruptFileError("DOCX is not a valid ZIP archive") from exc
        return

    if is_pdf or is_zip or guessed is not None:
        raise UnsupportedFileTypeError("Binary content cannot be uploaded as text")
    decode_text(data)


def extract_document(
    path: Path,
    *,
    original_filename: str | None = None,
    max_mb: int | None = None,
    ocr_provider: OcrProvider | None = None,
    numbered_headings: bool = False,
    max_pages: int | None = None,
    max_extracted_chars: int | None = None,
    max_zip_uncompressed_mb: int | None = None,
) -> ExtractedDocument:
    """Validate type and size, then extract one document.

    Args:
        path: Local document path.
        original_filename: Filename supplied by the uploader, if present.
        max_mb: Upload size limit; defaults to Settings.MAX_UPLOAD_MB.
        ocr_provider: Optional offline OCR hook for PDFs.
        numbered_headings: Enable TXT numbered-section detection.

    Raises:
        UnsupportedFileTypeError: Unsupported extension or mismatched bytes.
        FileTooLargeError: Upload exceeds the configured size limit.
        EmptyDocumentError: Empty input file.
        CorruptFileError: Invalid archive or malformed document.
    """
    suffix = Path(original_filename or path.name).suffix.lower()
    if suffix not in SUPPORTED:
        raise UnsupportedFileTypeError(f"Unsupported file type: {suffix or '(none)'}")
    if not path.is_file():
        raise CorruptFileError("Document file does not exist")
    limit = max_mb if max_mb is not None else get_settings().MAX_UPLOAD_MB
    if limit <= 0:
        raise ValueError("max_mb must be positive")
    size = path.stat().st_size
    if size == 0:
        raise EmptyDocumentError("Uploaded file is empty")
    if size > limit * 1024 * 1024:
        raise FileTooLargeError(f"Upload exceeds {limit} MB limit")
    data = path.read_bytes()
    settings = get_settings()
    page_limit = max_pages if max_pages is not None else settings.MAX_PAGES
    char_limit = (
        max_extracted_chars
        if max_extracted_chars is not None
        else settings.MAX_EXTRACTED_CHARS
    )
    zip_limit = (
        max_zip_uncompressed_mb
        if max_zip_uncompressed_mb is not None
        else settings.MAX_ZIP_UNCOMPRESSED_MB
    )
    if page_limit <= 0 or char_limit <= 0 or zip_limit < 0:
        raise ValueError("Extraction limits must be positive")
    _validate_content(path, suffix, data, max_zip_uncompressed_mb=zip_limit)

    extractor: DocumentExtractor
    if suffix == ".pdf":
        extractor = PdfExtractor(
            ocr_provider=ocr_provider,
            max_pages=page_limit,
            max_extracted_chars=char_limit,
        )
    elif suffix == ".docx":
        extractor = DocxExtractor()
    else:
        extractor = TextExtractor(
            markdown=(suffix == ".md"), numbered_headings=numbered_headings
        )
    document = extractor.extract(path, original_filename=original_filename)
    if sum(len(unit.markdown) for unit in document.units) > char_limit:
        raise FileTooLargeError(
            "Extracted text exceeds configured character limit",
            code="extracted_text_too_large",
        )
    from app.services.structure import annotate_heading_paths

    return annotate_heading_paths(document)
