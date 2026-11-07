"""Configured embedder selection without eager ML imports (IN-6)."""

from __future__ import annotations

from app.adapters.embedders.base import Embedder, validate_embedder_dimension
from app.core.config import Settings, get_settings
from app.core.errors import EmbeddingConfigurationError
from app.models.chunk import CHUNK_EMBEDDING_DIMENSION

_ALLOWED_BACKENDS = ("fake", "bge-m3")


def create_embedder(*, settings: Settings | None = None) -> Embedder:
    """Build and dimension-check the selected backend without loading weights."""
    config = settings or get_settings()

    if config.EMBEDDER_BACKEND not in _ALLOWED_BACKENDS:
        raise EmbeddingConfigurationError(
            f"Unsupported embedder backend: {config.EMBEDDER_BACKEND}",
            details={"configured_backend": config.EMBEDDER_BACKEND},
        )

    validate_embedder_dimension(
        config.EMBEDDING_DIMENSION,
        CHUNK_EMBEDDING_DIMENSION,
        config.EMBEDDING_MODEL_NAME,
    )

    if config.EMBEDDER_BACKEND == "fake":
        from app.adapters.embedders.fake import FakeEmbedder

        return FakeEmbedder(dimension=config.EMBEDDING_DIMENSION)

    from app.adapters.embedders.bge_m3 import BgeM3Embedder

    return BgeM3Embedder(
        model_name=config.EMBEDDING_MODEL_NAME,
        model_revision=config.EMBEDDING_MODEL_REVISION,
        dimension=config.EMBEDDING_DIMENSION,
        max_length=config.EMBEDDING_MAX_LENGTH,
    )
