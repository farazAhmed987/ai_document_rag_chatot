"""Configuration and exception smoke tests.

SRD Sections 8.4, 10 and 13.
Requirements: DM-2, IN-1 and RG-11.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import ENV_FILE, REPO_ROOT, Settings
from app.core.errors import (
    AppError,
    EmptyDocumentError,
    FileTooLargeError,
)


def test_repo_root_is_resolved() -> None:
    """Confirm the dotenv path is anchored at the repository."""
    assert ENV_FILE == REPO_ROOT / ".env"
    assert (REPO_ROOT / "backend").is_dir()


def test_settings_without_external_credentials() -> None:
    """Ensure the application starts without database credentials."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL=None,
        ANALYTICS_DATABASE_URL=None,
        API_KEY=None,
    )

    assert settings.DATABASE_URL is None
    assert settings.ANALYTICS_DATABASE_URL is None
    assert settings.API_KEY is None
    assert settings.MAX_UPLOAD_MB == 20
    assert settings.CHUNK_TOKENS == 500
    assert settings.CHUNK_OVERLAP == 60


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            "postgres://user:pass@localhost:5432/rag",
            "postgresql+psycopg://user:pass@localhost:5432/rag",
        ),
        (
            "postgresql://user:pass@localhost:5432/rag",
            "postgresql+psycopg://user:pass@localhost:5432/rag",
        ),
        (
            "postgresql+psycopg://user:pass@localhost:5432/rag",
            "postgresql+psycopg://user:pass@localhost:5432/rag",
        ),
    ],
)
def test_database_url_normalization(
    original: str,
    expected: str,
) -> None:
    """Verify PostgreSQL URLs select the psycopg 3 driver."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL=original,
        ANALYTICS_DATABASE_URL=original,
    )

    assert settings.require_database_url() == expected
    assert settings.require_database_url(analytics=True) == expected


def test_database_url_is_required_only_on_use() -> None:
    """Missing credentials must not break settings initialization."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL=None,
        ANALYTICS_DATABASE_URL=None,
    )

    with pytest.raises(ValueError, match="DATABASE_URL"):
        settings.require_database_url()

    with pytest.raises(
        ValueError,
        match="ANALYTICS_DATABASE_URL",
    ):
        settings.require_database_url(analytics=True)


def test_upload_limit_validation() -> None:
    """Reject invalid upload configuration values."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            MAX_UPLOAD_MB=0,
        )


def test_error_envelope() -> None:
    """Ensure application errors produce the SRD error envelope."""
    error = AppError(
        "Document validation failed",
        details={"filename": "sample.pdf"},
    )

    assert error.as_dict() == {
        "error": {
            "code": "app_error",
            "message": "Document validation failed",
            "details": {"filename": "sample.pdf"},
        }
    }


def test_specialized_error_codes() -> None:
    """Check error codes and customizable scanned-PDF failures."""
    oversized = FileTooLargeError("File exceeds upload limit")

    assert oversized.code == "file_too_large"
    assert oversized.status_code == 413

    scanned = EmptyDocumentError(
        "Scanned PDF requires OCR",
        code="scanned_pdf_no_ocr",
    )

    assert scanned.code == "scanned_pdf_no_ocr"
