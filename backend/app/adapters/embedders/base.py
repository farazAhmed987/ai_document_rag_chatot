"""Embedding interface and vector-schema guard (IN-6; SRD 8.1 and 12)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.errors import EmbeddingConfigurationError


@runtime_checkable
class Embedder(Protocol):
    """Produce normalized dense vectors without persistence side effects."""

    model_name: str
    model_revision: str
    dimension: int
    max_length: int

    def embed_documents(
        self, texts: list[str], batch_size: int = 16
    ) -> list[list[float]]:
        """Embed document texts in their original order."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed one query in the same unprefixed vector space."""
        ...


def validate_embedder_dimension(
    embedder: Embedder, required_dimension: int
) -> Embedder:
    """Fail before use when the adapter and pgvector dimensions disagree."""
    if embedder.dimension != required_dimension:
        raise EmbeddingConfigurationError(
            (
                f"Embedder dimension {embedder.dimension} does not match required "
                f"dimension {required_dimension}"
            ),
            details={
                "configured": embedder.dimension,
                "required": required_dimension,
            },
        )
    return embedder
