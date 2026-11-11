"""Reranker protocol and base abstractions (SRD Section 8.2; A-12)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    """Score query-document text pairs returning raw similarity logits."""

    @property
    def model_name(self) -> str: ...

    @property
    def model_revision(self) -> str | None: ...

    @property
    def max_length(self) -> int: ...

    def predict(
        self,
        pairs: list[tuple[str, str]],
        batch_size: int = 32,
    ) -> list[float]:
        """Score query-document pairs and return float scores (raw logits).

        Args:
            pairs: List of (query, passage) string pairs.
            batch_size: Batch size for cross-encoder scoring.

        Returns:
            List of float scores corresponding to the input pairs in order.
        """
        ...
