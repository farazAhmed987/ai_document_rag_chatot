"""Deterministic normalized embeddings for tests and CI (IN-6)."""

from __future__ import annotations

import hashlib
import math


class FakeEmbedder:
    """Map text to a stable hash-derived unit vector without ML dependencies."""

    model_name = "fake"
    model_revision = "sha256-shake-v1"
    max_length = 2**31 - 1

    def __init__(self, *, dimension: int = 1024) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    def _embed(self, text: str) -> list[float]:
        raw = hashlib.shake_256(text.encode("utf-8")).digest(self.dimension * 8)
        scale = float(2**64 - 1)
        vector = [
            (int.from_bytes(raw[index : index + 8], "big") / scale) * 2.0 - 1.0
            for index in range(0, len(raw), 8)
        ]
        norm = math.sqrt(math.fsum(value * value for value in vector))
        if norm == 0.0:  # pragma: no cover - cryptographically implausible safeguard
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]

    def embed_documents(
        self, texts: list[str], batch_size: int = 16
    ) -> list[list[float]]:
        """Embed in deterministic batches while retaining input order."""
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        output: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            output.extend(
                self._embed(text) for text in texts[start : start + batch_size]
            )
        return output

    def embed_query(self, text: str) -> list[float]:
        """Use the same no-prefix representation as document embedding."""
        return self._embed(text)
