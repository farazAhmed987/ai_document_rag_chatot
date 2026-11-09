"""Unit tests for safe ingestion error formatting, masking and logging (DM-2, SRD 10)."""

from __future__ import annotations

import importlib
import logging
from types import ModuleType

import pytest

from app.core.errors import AppError

SENTINEL = "PHASE6_SECRET_SENTINEL_DO_NOT_LOG"


def _load_module() -> ModuleType:
    """Load future ingestion error formatting module inside test body."""
    return importlib.import_module("app.core.ingest_errors")


def test_ingest_error_formats_and_parses_code() -> None:
    """Format must produce [CODE] message and parse must extract both components."""
    err_mod = _load_module()
    formatted = err_mod.format_ingest_error(
        "EXTRACTION_FAILED", "Document text could not be parsed."
    )
    assert formatted == "[EXTRACTION_FAILED] Document text could not be parsed."

    code, msg = err_mod.parse_ingest_error(formatted)
    assert code == "EXTRACTION_FAILED"
    assert msg == "Document text could not be parsed."

    # Parse unformatted string defaults to UNKNOWN_ERROR
    fallback_code, fallback_msg = err_mod.parse_ingest_error(
        "Plain message without brackets"
    )
    assert fallback_code == "UNKNOWN_ERROR"
    assert fallback_msg == "Plain message without brackets"

    # Format with invalid code falls back to INGESTION_ERROR
    fallback_formatted = err_mod.format_ingest_error("invalid lower code", "Some text")
    assert fallback_formatted.startswith("[INGESTION_ERROR]")


def test_ingest_error_rejects_invalid_codes() -> None:
    """Error codes must strictly match regex ^[A-Z][A-Z0-9_]{2,63}$."""
    err_mod = _load_module()
    assert err_mod.validate_ingest_error_code("EXTRACTION_FAILED") is True
    assert err_mod.validate_ingest_error_code("CHUNK_BUDGET_EXCEEDED") is True

    assert err_mod.validate_ingest_error_code(12345) is False  # type: ignore[arg-type]
    assert err_mod.validate_ingest_error_code("invalid_lower") is False
    assert err_mod.validate_ingest_error_code("NO") is False
    assert err_mod.validate_ingest_error_code("123_STARTS_DIGIT") is False
    assert err_mod.validate_ingest_error_code("INVALID!CHAR") is False


def test_ingest_error_is_capped_at_500_characters() -> None:
    """Stored error messages must never exceed 500 characters."""
    err_mod = _load_module()
    long_msg = "A" * 1000
    formatted = err_mod.format_ingest_error("INGESTION_ERROR", long_msg)
    assert len(formatted) <= 500


def test_unknown_error_uses_safe_class_name_without_exception_text() -> None:
    """Unknown exceptions must format as [INGESTION_UNEXPECTED_ERROR] failed during <stage> (<ClassName>)."""
    err_mod = _load_module()
    leak_text = f"Connection failed with db_password=secret and {SENTINEL}"
    exc = RuntimeError(leak_text)

    sanitized = err_mod.sanitize_ingestion_exception(exc, stage="extraction")
    assert (
        sanitized
        == "[INGESTION_UNEXPECTED_ERROR] failed during extraction (RuntimeError)"
    )
    assert SENTINEL not in sanitized
    assert "db_password" not in sanitized

    # AppError passes through its code with stage info; raw message and sentinels are masked
    class CustomAppError(AppError):
        default_code = "CHUNK_BUDGET_EXCEEDED"

    app_exc = CustomAppError(
        f"Sensitive document token text: {SENTINEL}", code="CHUNK_BUDGET_EXCEEDED"
    )
    sanitized_app = err_mod.sanitize_ingestion_exception(app_exc, stage="chunking")
    assert sanitized_app == "[CHUNK_BUDGET_EXCEEDED] failed during chunking"
    assert SENTINEL not in sanitized_app
    assert "Sensitive document token text" not in sanitized_app

    # AppError with non-conforming code falls back to INGESTION_ERROR
    bad_code_exc = CustomAppError("msg", code="123_invalid_code")
    sanitized_bad = err_mod.sanitize_ingestion_exception(bad_code_exc, stage="chunking")
    assert sanitized_bad == "[INGESTION_ERROR] failed during chunking"


def test_error_and_logs_never_include_document_content_or_filename(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Neither error text nor structured logs may leak sensitive document content or filenames."""
    err_mod = _load_module()
    sensitive_filename = f"salary_confidential_{SENTINEL}.pdf"
    sensitive_content = f"Top secret company acquisitions {SENTINEL}"

    exc = ValueError(f"Failed processing {sensitive_filename}: {sensitive_content}")
    sanitized = err_mod.sanitize_ingestion_exception(exc, stage="chunking")

    assert SENTINEL not in sanitized
    assert sensitive_filename not in sanitized
    assert sensitive_content not in sanitized

    with caplog.at_level(logging.INFO):
        err_mod.log_ingestion_error(
            exc,
            stage="chunking",
            document_id="00000000-0000-0000-0000-000000000000",
        )

    assert SENTINEL not in caplog.text
