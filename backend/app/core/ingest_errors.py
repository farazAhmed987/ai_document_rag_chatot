"""Safe ingestion error formatting, sanitization, and structured logging.

SRD Section 10; DM-2, IN-5, RG-11.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog

from app.core.errors import AppError

logger = structlog.get_logger(__name__)

_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_PARSE_PATTERN = re.compile(r"^\[([A-Z][A-Z0-9_]{2,63})\]\s*(.*)$", re.DOTALL)


def validate_ingest_error_code(code: str) -> bool:
    """Validate that error code strictly matches ^[A-Z][A-Z0-9_]{2,63}$."""
    if not isinstance(code, str):
        return False
    return bool(_CODE_PATTERN.match(code))


def format_ingest_error(code: str, safe_message: str) -> str:
    """Format safe error code and message capped at 500 characters.

    Preserves the complete '[CODE] ' prefix when truncating.
    Strips control characters.
    """
    clean_code = _CONTROL_CHAR_PATTERN.sub("", code).strip()
    if not validate_ingest_error_code(clean_code):
        clean_code = "INGESTION_ERROR"
    clean_msg = _CONTROL_CHAR_PATTERN.sub(" ", safe_message).strip()
    prefix = f"[{clean_code}] "
    max_msg_len = 500 - len(prefix)
    if len(clean_msg) > max_msg_len:
        clean_msg = clean_msg[:max_msg_len]
    return f"{prefix}{clean_msg}".rstrip()


def parse_ingest_error(value: str) -> tuple[str, str]:
    """Parse a formatted [CODE] message into code and message strings."""
    match = _PARSE_PATTERN.match(value.strip())
    if match:
        return match.group(1), match.group(2)
    return "UNKNOWN_ERROR", value.strip()


def sanitize_ingestion_exception(exc: Exception, *, stage: str) -> str:
    """Format a safe, leak-free error string for DB persistence.

    Never includes raw str(exc) or sensitive details.
    """
    clean_stage = _CONTROL_CHAR_PATTERN.sub("", stage).strip()
    if isinstance(exc, AppError):
        code = getattr(exc, "code", None) or getattr(exc, "default_code", "APP_ERROR")
        normalized_code = str(code).upper()
        if not validate_ingest_error_code(normalized_code):
            normalized_code = "INGESTION_ERROR"
        return f"[{normalized_code}] failed during {clean_stage}"

    class_name = type(exc).__name__
    return f"[INGESTION_UNEXPECTED_ERROR] failed during {clean_stage} ({class_name})"


def log_ingestion_error(
    exc: Exception,
    *,
    stage: str,
    document_id: str | UUID,
    **extra: Any,
) -> None:
    """Log ingestion failure using structlog with safe metadata only.

    Never logs str(exc), file contents, or exc_info.
    """
    clean_stage = _CONTROL_CHAR_PATTERN.sub("", stage).strip()
    doc_id_str = str(document_id)
    error_code = (
        getattr(exc, "code", type(exc).__name__)
        if isinstance(exc, AppError)
        else type(exc).__name__
    )
    logger.error(
        "ingestion_failed",
        stage=clean_stage,
        document_id=doc_id_str,
        error_type=type(exc).__name__,
        error_code=str(error_code).upper(),
        **{k: str(v) for k, v in extra.items() if not k.startswith("_")},
    )
