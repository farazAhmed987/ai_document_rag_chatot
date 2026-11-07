"""Configured embedder selection without eager ML imports (IN-6)."""

from __future__ import annotations

from app.adapters.embedders.base import Embedder, validate_embedder_dimension
from app.adapters.embedders.bge_m3 import BgeM3Embedder
from app.adapters.embedders.fake import FakeEmbedder
from app.core.config import Settings, get_settings


def create_embedder(*, settings: Settings | None = None) -> Embedder:
    """Build and dimension-check the selected backend without loading weights."""
    config = settings or get_settings()
    embedder: Embedder
    if config.EMBEDDER_BACKEND == "fake":
        embedder = FakeEmbedder(dimension=config.EMBEDDING_DIMENSION)
    else:
        embedder = BgeM3Embedder(
            model_name=config.EMBEDDING_MODEL_NAME,
            model_revision=config.EMBEDDING_MODEL_REVISION,
            dimension=config.EMBEDDING_DIMENSION,
            max_length=config.EMBEDDING_MAX_LENGTH,
        )
    return validate_embedder_dimension(embedder, 1024)
