"""Lazy offline CPU adapter for BAAI/bge-reranker-v2-m3 (SRD Section 8.2; A-12)."""

from __future__ import annotations

import os
import threading
from typing import Any

from app.core.errors import RerankerConfigurationError

_RERANKER_LOCK = threading.Lock()
_RERANKER_CACHE: dict[tuple[str, str | None], Any] = {}


def _load_cross_encoder(
    model_name_or_path: str,
    revision: str | None,
    max_length: int = 1024,
    *,
    allow_download: bool = False,
) -> Any:
    """Load and cache a CrossEncoder model instance with process-wide thread safety.

    Args:
        model_name_or_path: HuggingFace model repo or local filesystem path.
        revision: Pinned 40-character commit SHA.
        max_length: Maximum cross-encoder sequence length.
        allow_download: Whether network download is allowed.

    Returns:
        Loaded CrossEncoder model instance.

    Raises:
        RerankerConfigurationError: On missing dependency, missing model, or load failure.
    """
    key = (model_name_or_path, revision)
    with _RERANKER_LOCK:
        if key in _RERANKER_CACHE:
            return _RERANKER_CACHE[key]

        if not allow_download:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RerankerConfigurationError(
                "sentence-transformers is not installed; run in ML environment (.venv) or install with [ml] extra"
            ) from exc

        try:
            model = CrossEncoder(
                model_name_or_path=model_name_or_path,
                revision=revision,
                local_files_only=not allow_download,
                trust_remote_code=False,
                max_length=max_length,
                device="cpu",
            )
        except Exception as exc:
            raise RerankerConfigurationError(
                f"Failed to load CrossEncoder model '{model_name_or_path}' (revision: {revision}): {exc}"
            ) from exc

        _RERANKER_CACHE[key] = model
        return model


class BgeM3Reranker:
    """Reranker wrapping BAAI/bge-reranker-v2-m3 producing raw logits."""

    model_name: str
    model_revision: str | None
    max_length: int

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        model_revision: str | None = None,
        max_length: int = 1024,
        allow_download: bool = False,
    ) -> None:
        """Initialize BgeM3Reranker configuration without loading weights."""
        if max_length <= 0:
            raise ValueError("max_length must be positive")
        self.model_name = model_name
        self.model_revision = model_revision
        self.max_length = max_length
        self._allow_download = allow_download
        self._model: Any = None

    @property
    def model(self) -> Any:
        """Lazily load and return the underlying CrossEncoder instance."""
        if self._model is None:
            self._model = _load_cross_encoder(
                model_name_or_path=self.model_name,
                revision=self.model_revision,
                max_length=self.max_length,
                allow_download=self._allow_download,
            )
        return self._model

    def predict(
        self,
        pairs: list[tuple[str, str]],
        batch_size: int = 32,
    ) -> list[float]:
        """Score query-passage pairs and return raw similarity logits.

        Args:
            pairs: List of (query, passage) string pairs.
            batch_size: Batch size for cross-encoder inference.

        Returns:
            List of raw logit float scores.
        """
        if not pairs:
            return []
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")

        # Decision D2: activation_fn=None returns raw logits
        raw_scores = self.model.predict(
            pairs,
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            activation_fn=None,
        )

        if hasattr(raw_scores, "tolist"):
            return [float(x) for x in raw_scores.tolist()]
        return [float(x) for x in raw_scores]
