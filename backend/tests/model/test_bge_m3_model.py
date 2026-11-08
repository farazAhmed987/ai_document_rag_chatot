"""Local-only BGE-M3 acceptance and performance probes (IN-6; SRD 12)."""

from __future__ import annotations

import importlib
import math
import resource
import time
from collections.abc import Iterator
from typing import Protocol

import pytest

from app.core.config import Settings

pytestmark = pytest.mark.model


class EmbedderLike(Protocol):
    dimension: int

    def embed_documents(
        self, texts: list[str], batch_size: int = 16
    ) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def norm(vector: list[float]) -> float:
    return math.sqrt(math.fsum(value * value for value in vector))


def cosine(left: list[float], right: list[float]) -> float:
    return math.fsum(a * b for a, b in zip(left, right, strict=True))


@pytest.fixture(scope="session")
def real_embedder() -> Iterator[EmbedderLike]:
    factory = importlib.import_module("app.adapters.embedders.factory")
    config = Settings(_env_file=None, EMBEDDER_BACKEND="bge-m3")
    yield factory.create_embedder(settings=config)


def test_real_model_dimension_and_normalization(
    real_embedder: EmbedderLike,
) -> None:
    vectors = real_embedder.embed_documents(
        ["The property is available in Islamabad.", "کرایہ ماہانہ ادا کرنا ہے۔"]
    )
    assert real_embedder.dimension == 1024
    assert all(len(vector) == 1024 for vector in vectors)
    assert all(norm(vector) == pytest.approx(1.0, abs=1e-5) for vector in vectors)


@pytest.mark.parametrize(
    ("source", "paraphrase", "unrelated"),
    [
        (
            "The buyer pays a two percent commission after completion.",
            "A 2% buyer commission becomes due when the transaction completes.",
            "The Islamabad office opens at nine in the morning.",
        ),
        (
            "Tenants must give thirty days notice before leaving.",
            "A tenant needs to notify the landlord one month before departure.",
            "Commercial valuation fees depend on the property's price.",
        ),
        (
            "The reservation deposit is adjusted against the final payment.",
            "The booking deposit is deducted from the amount paid at completion.",
            "The company started operating in Rawalpindi in 2017.",
        ),
    ],
)
def test_english_paraphrase_scores_above_unrelated(
    real_embedder: EmbedderLike,
    source: str,
    paraphrase: str,
    unrelated: str,
) -> None:
    source_vector = real_embedder.embed_documents([source])[0]
    paraphrase_vector = real_embedder.embed_query(paraphrase)
    unrelated_vector = real_embedder.embed_query(unrelated)
    assert cosine(source_vector, paraphrase_vector) > cosine(
        source_vector, unrelated_vector
    )


def test_roman_urdu_similarity_is_reported_without_threshold(
    real_embedder: EmbedderLike,
) -> None:
    query = real_embedder.embed_query("booking fee kitni hai?")
    relevant, unrelated = real_embedder.embed_documents(
        [
            "The booking fee is PKR 50,000 and is adjusted in the final payment.",
            "The company has branch offices in Islamabad and Rawalpindi.",
        ]
    )
    relevant_score = cosine(query, relevant)
    unrelated_score = cosine(query, unrelated)
    print(
        "ROMAN_URDU_SIMILARITY",
        f"relevant={relevant_score:.6f}",
        f"unrelated={unrelated_score:.6f}",
    )
    assert math.isfinite(relevant_score)
    assert math.isfinite(unrelated_score)


def test_real_model_batch_latency_and_peak_rss_are_reported(
    real_embedder: EmbedderLike,
) -> None:
    texts = [f"Performance probe document number {index}." for index in range(16)]
    started = time.perf_counter()
    vectors = real_embedder.embed_documents(texts, batch_size=16)
    elapsed = time.perf_counter() - started
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(
        "BGE_M3_BATCH_METRICS",
        f"items={len(texts)}",
        f"seconds={elapsed:.6f}",
        f"items_per_second={len(texts) / elapsed:.6f}",
        f"peak_rss_kib={peak_rss}",
    )
    assert len(vectors) == len(texts)
    assert elapsed > 0
    assert peak_rss > 0
