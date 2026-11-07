"""Embedding interface and vector-schema guard (IN-6; SRD 8.1 and 12)."""

from __future__ import annotations

from typing import Any, Protocol, overload, runtime_checkable

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


@overload
def validate_embedder_dimension(
    embedder: Embedder,
    required_dimension: int,
    model_name: str | None = ...,
) -> Embedder: ...


@overload
def validate_embedder_dimension(
    embedder: int,
    required_dimension: int,
    model_name: str | None = ...,
) -> int: ...


def validate_embedder_dimension(
    embedder: Embedder | int,
    required_dimension: int,
    model_name: str | None = None,
) -> Any:
    """Fail before use when the adapter/setting and pgvector dimensions disagree."""
    configured: int
    if isinstance(embedder, int):
        configured = embedder
    else:
        configured = embedder.dimension

    if configured != required_dimension:
        details: dict[str, Any] = {
            "configured": configured,
            "required": required_dimension,
        }
        if model_name is not None:
            details["model_name"] = model_name
        raise EmbeddingConfigurationError(
            (
                f"Embedder dimension {configured} does not match required "
                f"dimension {required_dimension}"
            ),
            details=details,
        )
    return embedder
