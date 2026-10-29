"""Application exception hierarchy.

SRD Sections 8.4 and 14.
Requirements: DM-2, IN-1, IN-2 and IN-3.

Errors expose machine-readable codes suitable for the
error envelope defined in SRD Section 10.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar


class AppError(Exception):
    """Base class for application-level errors.

    Attributes:
        code: Machine-readable error code.
        message: Safe error description.
        details: Additional structured error information.
    """

    default_code: ClassVar[str] = "app_error"
    status_code: ClassVar[int] = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Initialize a structured application error.

        Args:
            message: Human-readable message.
            code: Optional code override for specialized cases.
            details: Optional structured context.
        """
        super().__init__(message)

        self.message = message
        self.code = code or self.default_code
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, object]:
        """Build the standard API error envelope.

        Returns:
            A dictionary containing code, message and details.
        """
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class UnsupportedFileTypeError(AppError):
    """Raised for unsupported or mismatched document types."""

    default_code = "unsupported_file_type"
    status_code = 400


class CorruptFileError(AppError):
    """Raised when a document cannot be parsed safely."""

    default_code = "corrupt_file"
    status_code = 400


class EmptyDocumentError(AppError):
    """Raised for empty documents or missing extractable content."""

    default_code = "empty_document"
    status_code = 400


class PasswordProtectedError(AppError):
    """Raised when an encrypted document requires a password."""

    default_code = "password_protected"
    status_code = 400


class ExtractionError(AppError):
    """Raised when document extraction fails."""

    default_code = "extraction_failed"
    status_code = 422


class FileTooLargeError(AppError):
    """Raised when a file exceeds the configured upload limit."""

    default_code = "file_too_large"
    status_code = 413
