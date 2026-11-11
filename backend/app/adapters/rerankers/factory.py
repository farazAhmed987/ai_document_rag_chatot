"""Configured reranker selection without eager ML imports (SRD 8.2; A-12)."""

from __future__ import annotations

from app.adapters.rerankers.base import Reranker
from app.core.config import Settings, get_settings
from app.core.errors import RerankerConfigurationError

_ALLOWED_BACKENDS = ("none", "fake", "bge-m3")


def create_reranker(*, settings: Settings | None = None) -> Reranker | None:
    """Build the configured reranker backend without loading weights.

    Args:
        settings: Optional Settings instance; defaults to get_settings().

    Returns:
        Reranker instance, or None if RERANKER_BACKEND is 'none'.

    Raises:
        RerankerConfigurationError: If the backend is not recognized.
    """
    config = settings or get_settings()

    if config.RERANKER_BACKEND not in _ALLOWED_BACKENDS:
        raise RerankerConfigurationError(
            f"Unsupported reranker backend: {config.RERANKER_BACKEND}",
            details={"configured_backend": config.RERANKER_BACKEND},
        )

    if config.RERANKER_BACKEND == "none":
        return None

    if config.RERANKER_BACKEND == "fake":
        from app.adapters.rerankers.fake import FakeReranker

        return FakeReranker(max_length=config.RERANKER_MAX_LENGTH)

    from app.adapters.rerankers.bge_reranker import BgeM3Reranker

    return BgeM3Reranker(
        model_name=config.RERANKER_MODEL_NAME,
        model_revision=config.RERANKER_MODEL_REVISION,
        max_length=config.RERANKER_MAX_LENGTH,
    )
