"""Lazy offline CPU adapter for normalized BGE-M3 dense vectors (IN-6)."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Protocol, cast

from app.core.errors import EmbeddingConfigurationError, EmbeddingInputError


class _Tokenizer(Protocol):
    def __call__(
        self,
        texts: str | list[str],
        *,
        add_special_tokens: bool,
        truncation: bool,
    ) -> dict[str, list[int] | list[list[int]]]: ...


class _Array(Protocol):
    def tolist(self) -> list[list[float]]: ...


class _SentenceModel(Protocol):
    max_seq_length: int
    tokenizer: _Tokenizer

    def get_sentence_embedding_dimension(self) -> int | None: ...

    def encode(self, texts: list[str], **kwargs: object) -> _Array: ...


class _SentenceTransformerFactory(Protocol):
    def __call__(self, model_name: str, **kwargs: object) -> _SentenceModel: ...


@lru_cache(maxsize=4)
def _load_model(
    model_name: str,
    model_revision: str,
    max_length: int,
    allow_download: bool,
) -> _SentenceModel:
    """Load one process-wide model instance for a pinned configuration."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "BGE-M3 requires the dependencies in backend/requirements-ml.txt"
        ) from exc
    try:
        model = cast(_SentenceTransformerFactory, SentenceTransformer)(
            model_name,
            revision=model_revision,
            device="cpu",
            local_files_only=not allow_download,
            trust_remote_code=False,
        )
    except OSError as exc:
        raise RuntimeError(
            "BGE-M3 is not cached; run embed_benchmark.py --prepare-model first"
        ) from exc
    model.max_seq_length = max_length
    return model


def _unit_vector(values: list[float]) -> list[float]:
    norm = math.sqrt(math.fsum(value * value for value in values))
    if norm == 0.0:
        raise EmbeddingConfigurationError("Embedding model returned a zero vector")
    return [value / norm for value in values]


class BgeM3Embedder:
    """Dense-only, unprefixed BAAI/bge-m3 embedding on CPU."""

    def __init__(
        self,
        *,
        model_name: str,
        model_revision: str,
        dimension: int,
        max_length: int,
    ) -> None:
        self.model_name = model_name
        self.model_revision = model_revision
        self.dimension = dimension
        self.max_length = max_length

    @classmethod
    def prepare_model(
        cls,
        *,
        model_name: str,
        model_revision: str,
        dimension: int,
        max_length: int,
    ) -> BgeM3Embedder:
        """Explicitly download and cache the pinned model, never implicitly."""
        embedder = cls(
            model_name=model_name,
            model_revision=model_revision,
            dimension=dimension,
            max_length=max_length,
        )
        model = _load_model(model_name, model_revision, max_length, True)
        embedder._validate_model(model)
        return embedder

    def _model(self) -> _SentenceModel:
        model = _load_model(
            self.model_name,
            self.model_revision,
            self.max_length,
            False,
        )
        model.max_seq_length = self.max_length
        self._validate_model(model)
        return model

    def _validate_model(self, model: _SentenceModel) -> None:
        observed = model.get_sentence_embedding_dimension()
        if observed != self.dimension:
            raise EmbeddingConfigurationError(
                (
                    f"Model dimension {observed} does not match configured "
                    f"dimension {self.dimension}"
                ),
                details={"configured": self.dimension, "observed": observed},
            )

    def _validate_lengths(self, model: _SentenceModel, texts: list[str]) -> None:
        encoded = model.tokenizer(
            texts,
            add_special_tokens=False,
            truncation=False,
        )["input_ids"]
        if texts and (not encoded or isinstance(encoded[0], int)):
            lengths = [len(cast(list[int], encoded))]
        else:
            lengths = [len(row) for row in cast(list[list[int]], encoded)]
        for index, observed in enumerate(lengths):
            if observed > self.max_length:
                raise EmbeddingInputError(
                    "Embedding input exceeds maximum token length",
                    details={
                        "index": index,
                        "observed": observed,
                        "limit": self.max_length,
                    },
                )

    def embed_documents(
        self, texts: list[str], batch_size: int = 16
    ) -> list[list[float]]:
        """Sort by length for inference and restore the caller's exact order."""
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if not texts:
            return []
        model = self._model()
        self._validate_lengths(model, texts)
        ordered = sorted(enumerate(texts), key=lambda item: len(item[1]), reverse=True)
        results: list[list[float] | None] = [None] * len(texts)
        for start in range(0, len(ordered), batch_size):
            batch = ordered[start : start + batch_size]
            encoded = model.encode(
                [text for _, text in batch],
                batch_size=len(batch),
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            ).tolist()
            if len(encoded) != len(batch):
                raise EmbeddingConfigurationError(
                    "Embedding model returned an unexpected batch length"
                )
            for (original_index, _), vector in zip(batch, encoded, strict=True):
                if len(vector) != self.dimension:
                    raise EmbeddingConfigurationError(
                        "Embedding model returned an unexpected vector dimension",
                        details={
                            "configured": self.dimension,
                            "observed": len(vector),
                        },
                    )
                results[original_index] = _unit_vector(
                    [float(value) for value in vector]
                )
        if any(vector is None for vector in results):  # pragma: no cover
            raise EmbeddingConfigurationError("Embedding result order was incomplete")
        return [cast(list[float], vector) for vector in results]

    def embed_query(self, text: str) -> list[float]:
        """Embed one query without adding an instruction prefix."""
        return self.embed_documents([text], batch_size=1)[0]
