"""Data schemas for retrieval results and candidates.

SRD Section 8.2; A-12.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

RetrievalMode = Literal["hybrid", "hybrid+rerank", "dense", "keyword"]


@dataclass(slots=True)
class RetrievedChunk:
    """A single retrieved chunk with multi-stage ranking metadata."""

    rank: int
    chunk_id: UUID
    document_id: UUID
    document_name: str
    chunk_index: int
    page: int | None
    section: str | None
    content: str
    body: str
    token_count: int
    score: float
    dense_rank: int | None = None
    dense_similarity: float | None = None
    keyword_rank: int | None = None
    keyword_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None


@dataclass(slots=True)
class RetrievalResult:
    """Aggregated retrieval output with per-stage latency timings."""

    chunks: list[RetrievedChunk]
    query: str
    mode: RetrievalMode
    embed_ms: float = 0.0
    dense_ms: float = 0.0
    keyword_ms: float = 0.0
    fuse_ms: float = 0.0
    rerank_ms: float = 0.0
    total_ms: float = 0.0
