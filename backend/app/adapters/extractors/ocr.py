"""Optional offline OCR providers (IN-3; SRD §8.1 step 3).

Tesseract is never an installation requirement. The provider is usable only
when both the executable and optional Python image libraries are present.
"""

from __future__ import annotations

import importlib
import shutil
from io import BytesIO
from typing import cast

import pymupdf


class NullOcrProvider:
    """The safe default: OCR is unavailable, without side effects."""

    @property
    def available(self) -> bool:
        """Report that local OCR is disabled."""
        return False

    def recognize(self, page: pymupdf.Page) -> str:
        """Return no text when no OCR provider is configured."""
        del page
        return ""


class TesseractOcrProvider:
    """Opt-in pytesseract adapter; requires a local Tesseract executable."""

    @property
    def available(self) -> bool:
        """Check optional executable and Python modules without importing them."""
        if shutil.which("tesseract") is None:
            return False
        try:
            importlib.import_module("pytesseract")
            importlib.import_module("PIL.Image")
        except ImportError:
            return False
        return True

    def recognize(self, page: pymupdf.Page) -> str:
        """OCR one page locally and return its recognized text.

        Args:
            page: PDF page object.

        Raises:
            RuntimeError: When Tesseract is not installed.
        """
        if not self.available:
            raise RuntimeError("Tesseract OCR is unavailable")
        image_module = importlib.import_module("PIL.Image")
        ocr_module = importlib.import_module("pytesseract")
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(2, 2),  # type: ignore[no-untyped-call]
            alpha=False,
        )
        image = image_module.open(BytesIO(pixmap.tobytes("png")))  # type: ignore[no-untyped-call]
        return cast(str, ocr_module.image_to_string(image))
