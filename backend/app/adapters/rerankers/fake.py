"""Deterministic fake reranker for tests and CI without ML dependencies."""

from __future__ import annotations

import hashlib
import re

_TOKEN_PATTERN = re.compile(r"[\w]+", flags=re.UNICODE)


class FakeReranker:
    """Deterministic scoring without torch or sentence_transformers."""

    model_name: str = "fake"
    model_revision: str | None = "sha256-fake-v1"

    def __init__(self, *, max_length: int = 1024) -> None:
        """Initialize FakeReranker with bounded max_length."""
        if max_length <= 0:
            raise ValueError("max_length must be positive")
        self.max_length = max_length

    def predict(
        self,
        pairs: list[tuple[str, str]],
        batch_size: int = 32,
    ) -> list[float]:
        """Compute deterministic float scores based on token overlap and digest.

        Args:
            pairs: List of (query, passage) string pairs.
            batch_size: Must be positive.

        Returns:
            List of deterministic float scores.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if not pairs:
            return []

        scores: list[float] = []
        for query, text in pairs:
            q_tokens = set(_TOKEN_PATTERN.findall(query.lower()))
            d_tokens = set(_TOKEN_PATTERN.findall(text.lower()))
            overlap = len(q_tokens & d_tokens)

            # Deterministic fractional hash component between 0.0 and 1.0
            digest = hashlib.md5(f"{query}::{text}".encode()).hexdigest()
            frac = int(digest[:4], 16) / 65535.0
            scores.append(float(overlap * 2.0 + frac))

        return scores
