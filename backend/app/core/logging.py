"""Structured application logging.

SRD Sections 6, 7 and 14.

Logging uses structlog and emits JSON records. Request identifiers
can later be attached using structlog.contextvars.bind_contextvars.
Secrets and document contents must never be logged.
"""

from __future__ import annotations

import logging
from typing import cast

import structlog
from structlog.stdlib import BoundLogger

from app.core.config import get_settings


def configure_logging(
    *,
    level: str | None = None,
) -> None:
    """Configure JSON logging for the application.

    Args:
        level: Optional logging level override. Defaults to
            the configured LOG_LEVEL.

    Raises:
        ValueError: If the specified level is invalid.
    """
    configured_level = (level or get_settings().LOG_LEVEL).upper()

    numeric_level = getattr(
        logging,
        configured_level,
        None,
    )

    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid logging level: {configured_level}")

    logging.basicConfig(
        level=numeric_level,
        format="%(message)s",
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(
                fmt="iso",
                utc=True,
            ),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> BoundLogger:
    """Return a named structured logger.

    Args:
        name: Python module or component name.

    Returns:
        A structlog logger bound to the name.
    """
    return cast(
        BoundLogger,
        structlog.get_logger(name),
    )
