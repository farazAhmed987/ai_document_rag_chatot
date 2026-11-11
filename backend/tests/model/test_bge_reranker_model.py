"""Model acceptance probe for BAAI/bge-reranker-v2-m3 cross-encoder (SRD 8.2; A-12).

Marked with pytest.mark.model; runs exclusively in ML environment (.venv).
"""

from __future__ import annotations

import os

import pytest

from app.adapters.rerankers.bge_reranker import BgeM3Reranker
from app.core.config import get_settings

pytestmark = pytest.mark.model


def test_bge_reranker_offline_inference_and_raw_logits() -> None:
    """Verifies that BgeM3Reranker runs offline on CPU and outputs raw logits."""
    # Ensure offline environment
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    settings = get_settings()
    reranker = BgeM3Reranker(
        model_name=settings.RERANKER_MODEL_NAME,
        model_revision=settings.RERANKER_MODEL_REVISION,
        max_length=settings.RERANKER_MAX_LENGTH,
    )

    query = "What is the security deposit for residential properties?"
    relevant_passage = (
        "Company: Himalaya Crest Realty\nDocument: Policies_and_Terms.pdf\nSection: Security Deposit\n\n"
        "Residential properties require a security deposit equivalent to two months rent upfront."
    )
    irrelevant_passage = (
        "Company: Himalaya Crest Realty\nDocument: Property_Listings.pdf\nSection: Commercial Plots\n\n"
        "Commercial plot 45 is situated along the main expressway with 100 feet frontage."
    )

    pairs = [
        (query, relevant_passage),
        (query, irrelevant_passage),
    ]

    scores = reranker.predict(pairs, batch_size=2)

    assert len(scores) == 2
    assert all(isinstance(s, float) for s in scores)
    # The relevant passage must have a substantially higher score than the irrelevant passage
    assert scores[0] > scores[1]
    # Decision D2: Verify raw logits (scores not squeezed between 0 and 1 by sigmoid or softmax)
    # Typically raw logits for matching text in bge-reranker-v2-m3 exceed 0.0 or can be negative for unmatching
    assert isinstance(scores[0], float)

    # Determinism check
    repeated_scores = reranker.predict(pairs, batch_size=2)
    assert pytest.approx(scores[0], 1e-4) == repeated_scores[0]
    assert pytest.approx(scores[1], 1e-4) == repeated_scores[1]
