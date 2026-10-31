"""Machine-readable errors for HTTP and offline workflows.

SRD Sections 8.4 and 10; DM-2, IN-1, MT-3.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar


class AppError(Exception):
    """Base typed application error with the standard SRD error envelope."""

    default_code: ClassVar[str] = "app_error"
    status_code: ClassVar[int] = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Initialize error metadata without leaking secrets."""
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, object]:
        """Return the standard machine-readable error envelope."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class UnsupportedFileTypeError(AppError):
    """Unsupported extension or content type."""

    default_code = "unsupported_file_type"


class CorruptFileError(AppError):
    """Unreadable or malformed document."""

    default_code = "corrupt_file"


class EmptyDocumentError(AppError):
    """No usable document text."""

    default_code = "empty_document"


class PasswordProtectedError(AppError):
    """Document cannot be opened without a password."""

    default_code = "password_protected"


class ExtractionError(AppError):
    """Document conversion failed."""

    default_code = "extraction_failed"
    status_code = 422


class FileTooLargeError(AppError):
    """Document is larger than configured limits."""

    default_code = "file_too_large"
    status_code = 413


class TenantRoleError(AppError):
    """Runtime role cannot be assumed; tenant access is denied."""

    default_code = "tenant_runtime_role_unavailable"
    status_code = 503
