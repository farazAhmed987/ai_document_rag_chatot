"""Tenant-safe hybrid retrieval service with RRF and optional reranking.

SRD Sections 8.2, 9; DM-3, IN-5, MT-1, MT-3; A-12.
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import replace
from typing import Protocol
from uuid import UUID

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.embedders.base import Embedder
from app.adapters.rerankers.base import Reranker
from app.core.config import Settings, get_settings
from app.core.errors import (
    RerankerConfigurationError,
    RetrievalInputError,
)
from app.repositories.base import tenant_session
from app.repositories.retrieval import RetrievalRepository
from app.schemas.retrieval import RetrievalMode, RetrievalResult, RetrievedChunk

logger = structlog.get_logger(__name__)


class TokenCounter(Protocol):
    """Protocol for counting query tokens."""

    def count(self, text: str) -> int:
        """Count tokens in text."""
        ...


class RetrievalService:
    """Orchestrates tenant-isolated hybrid retrieval and optional cross-encoder reranking."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        embedder: Embedder,
        reranker: Reranker | None = None,
        settings: Settings | None = None,
        token_counter: TokenCounter | None = None,
        max_query_tokens: int = 512,
    ) -> None:
        """Initialize retrieval service with dependencies.

        Args:
            session_factory: Session maker configured for runtime rag_app access.
            embedder: Query embedder adapter.
            reranker: Optional cross-encoder reranker adapter.
            settings: Runtime settings; defaults to get_settings().
            token_counter: Optional token counter for query budget enforcement.
            max_query_tokens: Maximum allowed query token count (default: 512).
        """
        self.session_factory = session_factory
        self.embedder = embedder
        self.reranker = reranker
        self.settings = settings or get_settings()
        self._token_counter = token_counter
        self.max_query_tokens = max_query_tokens

    def _get_token_counter(self) -> TokenCounter:
        """Lazily initialize default token counter if none was injected."""
        if self._token_counter is None:
            from app.adapters.tokenizers.bge_m3 import BgeM3TokenCounter

            self._token_counter = BgeM3TokenCounter()
        return self._token_counter

    def normalize_query(self, query: str) -> str:
        """Normalize query text, collapse whitespace, and enforce token budget.

        Args:
            query: Raw user query string.

        Returns:
            Normalized query string.

        Raises:
            RetrievalInputError: If query is empty or exceeds token budget.
        """
        if not isinstance(query, str):
            raise RetrievalInputError(
                "Query must be a string",
                code="RETRIEVAL_INPUT_ERROR",
            )

        # Unicode NFKC normalization
        normalized = unicodedata.normalize("NFKC", query)
        collapsed = " ".join(normalized.split())

        if not collapsed:
            raise RetrievalInputError(
                "Query cannot be empty",
                code="RETRIEVAL_INPUT_ERROR",
            )

        # Budget verification
        token_count = self._get_token_counter().count(collapsed)
        if token_count > self.max_query_tokens:
            raise RetrievalInputError(
                f"Query exceeds maximum token length ({token_count} > {self.max_query_tokens})",
                code="RETRIEVAL_INPUT_ERROR",
                details={
                    "token_count": token_count,
                    "max_tokens": self.max_query_tokens,
                },
            )

        return collapsed

    def fuse_rrf(
        self,
        dense_chunks: list[RetrievedChunk],
        keyword_chunks: list[RetrievedChunk],
        k: int = 60,
    ) -> list[RetrievedChunk]:
        """Combine dense and keyword candidate lists using Reciprocal Rank Fusion.

        Formula:
            score(c) = sum(1.0 / (k + rank_i)) for each candidate list where c appears.
            Equal weights (1.0 for dense, 1.0 for keyword).

        Tie-break ordering:
            score DESC, document_name ASC, chunk_index ASC, chunk_id ASC.

        Args:
            dense_chunks: Dense candidate chunks ordered by similarity DESC.
            keyword_chunks: Keyword candidate chunks ordered by keyword score DESC.
            k: RRF constant (default: 60).

        Returns:
            Deduplicated fused candidate chunks with rrf_score and deterministic ranking.
        """
        if k <= 0:
            raise ValueError("RRF constant k must be positive")

        fused_map: dict[UUID, RetrievedChunk] = {}

        # Process dense candidates
        for rank, chunk in enumerate(dense_chunks, start=1):
            rrf_contrib = 1.0 / (k + rank)
            fused_map[chunk.chunk_id] = replace(
                chunk,
                dense_rank=rank,
                dense_similarity=chunk.dense_similarity,
                rrf_score=rrf_contrib,
                score=rrf_contrib,
            )

        # Process keyword candidates and merge
        for rank, chunk in enumerate(keyword_chunks, start=1):
            rrf_contrib = 1.0 / (k + rank)
            cid = chunk.chunk_id
            if cid in fused_map:
                existing = fused_map[cid]
                total_rrf = (existing.rrf_score or 0.0) + rrf_contrib
                fused_map[cid] = replace(
                    existing,
                    keyword_rank=rank,
                    keyword_score=chunk.keyword_score,
                    rrf_score=total_rrf,
                    score=total_rrf,
                )
            else:
                fused_map[cid] = replace(
                    chunk,
                    dense_rank=None,
                    dense_similarity=None,
                    keyword_rank=rank,
                    keyword_score=chunk.keyword_score,
                    rrf_score=rrf_contrib,
                    score=rrf_contrib,
                )

        # Deterministic sort: score DESC, document_name ASC, chunk_index ASC, chunk_id ASC
        sorted_chunks = sorted(
            fused_map.values(),
            key=lambda c: (
                -c.score,
                c.document_name,
                c.chunk_index,
                str(c.chunk_id),
            ),
        )

        return [replace(c, rank=idx + 1) for idx, c in enumerate(sorted_chunks)]

    def diversify(
        self,
        candidates: list[RetrievedChunk],
        max_per_doc: int = 3,
        top_n: int = 5,
    ) -> list[RetrievedChunk]:
        """Apply per-document chunk limit with backfill to maintain top_n count.

        Args:
            candidates: Ranked candidate chunks.
            max_per_doc: Maximum chunks permitted from a single document.
            top_n: Target number of chunks to return.

        Returns:
            Diversified list of up to top_n chunks re-ranked 1..N.
        """
        if top_n <= 0 or max_per_doc <= 0:
            return []

        selected: list[RetrievedChunk] = []
        selected_ids: set[UUID] = set()
        doc_counts: dict[UUID, int] = {}

        # Pass 1: Select up to max_per_doc per document
        for c in candidates:
            count = doc_counts.get(c.document_id, 0)
            if count < max_per_doc:
                selected.append(c)
                selected_ids.add(c.chunk_id)
                doc_counts[c.document_id] = count + 1
                if len(selected) == top_n:
                    break

        # Pass 2: Backfill from unselected candidates if top_n not reached
        if len(selected) < top_n:
            for c in candidates:
                if c.chunk_id not in selected_ids:
                    selected.append(c)
                    selected_ids.add(c.chunk_id)
                    if len(selected) == top_n:
                        break

        return [replace(c, rank=idx + 1) for idx, c in enumerate(selected)]

    def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int = 20,
    ) -> list[RetrievedChunk]:
        """Score candidate chunks using the optional cross-encoder reranker.

        Args:
            query: Normalized user query.
            candidates: Fused candidate chunks.
            top_k: Number of candidates to rerank (RERANK_CANDIDATES).

        Returns:
            Candidates with rerank_score populated and re-ordered by rerank_score DESC.
        """
        if self.reranker is None or not candidates:
            return candidates

        to_rerank = candidates[:top_k]
        remaining = candidates[top_k:]

        pairs = [(query, c.content) for c in to_rerank]
        raw_logits = self.reranker.predict(pairs)

        reranked_chunks: list[RetrievedChunk] = []
        for chunk, logit in zip(to_rerank, raw_logits, strict=True):
            reranked_chunks.append(
                replace(
                    chunk,
                    rerank_score=logit,
                    score=logit,
                )
            )

        # Deterministic tie-break ordering for reranked candidates
        sorted_reranked = sorted(
            reranked_chunks,
            key=lambda c: (
                -c.score,
                c.document_name,
                c.chunk_index,
                str(c.chunk_id),
            ),
        )

        all_candidates = sorted_reranked + remaining
        return [replace(c, rank=idx + 1) for idx, c in enumerate(all_candidates)]

    def search(
        self,
        company_id: UUID,
        query: str,
        top_n: int | None = None,
        mode: RetrievalMode | None = None,
    ) -> RetrievalResult:
        """Execute tenant-isolated hybrid retrieval and return structured results.

        Args:
            company_id: Authorized tenant UUID.
            query: User query text.
            top_n: Number of final results to return (default: RETRIEVAL_TOP_N, max: RETRIEVAL_MAX_TOP_N).
            mode: Retrieval mode ('hybrid', 'hybrid+rerank', 'dense', 'keyword').

        Returns:
            RetrievalResult containing ranked RetrievedChunk items and timing metadata.

        Raises:
            TypeError: If company_id is not a UUID.
            RetrievalInputError: If query is invalid or top_n exceeds limits.
            RerankerConfigurationError: If hybrid+rerank requested without configured reranker.
        """
        if not isinstance(company_id, UUID):
            raise TypeError("company_id must be a uuid.UUID")

        target_top_n = top_n if top_n is not None else self.settings.RETRIEVAL_TOP_N
        if target_top_n <= 0:
            raise RetrievalInputError(
                "top_n must be positive",
                code="RETRIEVAL_INPUT_ERROR",
            )
        if target_top_n > self.settings.RETRIEVAL_MAX_TOP_N:
            raise RetrievalInputError(
                f"top_n ({target_top_n}) cannot exceed RETRIEVAL_MAX_TOP_N ({self.settings.RETRIEVAL_MAX_TOP_N})",
                code="RETRIEVAL_INPUT_ERROR",
                details={
                    "top_n": target_top_n,
                    "max_top_n": self.settings.RETRIEVAL_MAX_TOP_N,
                },
            )

        normalized_query = self.normalize_query(query)

        # Resolve mode
        resolved_mode: RetrievalMode
        if mode is None:
            resolved_mode = "hybrid+rerank" if self.reranker is not None else "hybrid"
        elif mode in ("hybrid", "hybrid+rerank", "dense", "keyword"):
            resolved_mode = mode
        else:
            raise RetrievalInputError(
                f"Unsupported retrieval mode: {mode}",
                code="RETRIEVAL_INPUT_ERROR",
            )

        if resolved_mode == "hybrid+rerank" and self.reranker is None:
            raise RerankerConfigurationError(
                "Reranker requested but no reranker backend is configured",
                code="RERANKER_CONFIG_ERROR",
            )

        start_total = time.perf_counter()
        embed_ms = 0.0
        dense_ms = 0.0
        keyword_ms = 0.0
        fuse_ms = 0.0
        rerank_ms = 0.0

        query_vector: list[float] = []
        if resolved_mode in ("hybrid", "hybrid+rerank", "dense"):
            t_embed_0 = time.perf_counter()
            query_vector = self.embedder.embed_query(normalized_query)
            embed_ms = (time.perf_counter() - t_embed_0) * 1000.0

        dense_candidates: list[RetrievedChunk] = []
        keyword_candidates: list[RetrievedChunk] = []

        with tenant_session(self.session_factory, company_id) as session:
            repo = RetrievalRepository(session=session, company_id=company_id)

            if resolved_mode in ("hybrid", "hybrid+rerank", "dense"):
                t_dense_0 = time.perf_counter()
                dense_candidates = repo.dense_search(
                    query_vector=query_vector,
                    limit=self.settings.DENSE_K,
                )
                dense_ms = (time.perf_counter() - t_dense_0) * 1000.0

            if resolved_mode in ("hybrid", "hybrid+rerank", "keyword"):
                t_kw_0 = time.perf_counter()
                keyword_candidates = repo.keyword_search(
                    query=normalized_query,
                    limit=self.settings.KEYWORD_K,
                )
                keyword_ms = (time.perf_counter() - t_kw_0) * 1000.0

        # Post-retrieval processing based on resolved mode
        final_chunks: list[RetrievedChunk]

        if resolved_mode == "dense":
            final_chunks = self.diversify(
                dense_candidates,
                max_per_doc=self.settings.RETRIEVAL_MAX_PER_DOC,
                top_n=target_top_n,
            )
        elif resolved_mode == "keyword":
            final_chunks = self.diversify(
                keyword_candidates,
                max_per_doc=self.settings.RETRIEVAL_MAX_PER_DOC,
                top_n=target_top_n,
            )
        else:
            # Hybrid or hybrid+rerank
            t_fuse_0 = time.perf_counter()
            fused = self.fuse_rrf(
                dense_chunks=dense_candidates,
                keyword_chunks=keyword_candidates,
                k=self.settings.RRF_K,
            )
            fuse_ms = (time.perf_counter() - t_fuse_0) * 1000.0

            if resolved_mode == "hybrid+rerank":
                t_rerank_0 = time.perf_counter()
                reranked = self.rerank(
                    query=normalized_query,
                    candidates=fused,
                    top_k=self.settings.RERANK_CANDIDATES,
                )
                rerank_ms = (time.perf_counter() - t_rerank_0) * 1000.0
                final_chunks = self.diversify(
                    reranked,
                    max_per_doc=self.settings.RETRIEVAL_MAX_PER_DOC,
                    top_n=target_top_n,
                )
            else:
                final_chunks = self.diversify(
                    fused,
                    max_per_doc=self.settings.RETRIEVAL_MAX_PER_DOC,
                    top_n=target_top_n,
                )

        total_ms = (time.perf_counter() - start_total) * 1000.0

        return RetrievalResult(
            chunks=final_chunks,
            query=normalized_query,
            mode=resolved_mode,
            embed_ms=embed_ms,
            dense_ms=dense_ms,
            keyword_ms=keyword_ms,
            fuse_ms=fuse_ms,
            rerank_ms=rerank_ms,
            total_ms=total_ms,
        )
