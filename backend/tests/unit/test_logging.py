"""Unit tests for structured application logging."""

from __future__ import annotations

import pytest

from app.core.logging import configure_logging, get_logger


def test_configure_logging_and_emit_event() -> None:
    configure_logging(level="INFO")
    logger = get_logger("test_module")
    logger.info("application_event", status="ok")


def test_configure_logging_invalid_level() -> None:
    with pytest.raises(ValueError, match="Invalid logging level"):
        configure_logging(level="NOT_A_VALID_LEVEL")
