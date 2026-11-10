"""Retrieval repository for tenant-scoped dense and keyword candidate searches.

SRD Sections 8.2, 9; DM-3, IN-5, MT-1, MT-3; A-12.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.chunk import Chunk
from app.models.document import Document
from app.schemas.retrieval import RetrievedChunk
from app.services.chunking import strip_context_header

_TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)


def extract_search_tokens(query: str) -> list[str]:
    """Extract alphanumeric word tokens for keyword matching."""
    return [t.lower() for t in _TOKEN_PATTERN.findall(query) if t.strip()]


def build_or_tsquery_expression(tokens: list[str]) -> str | None:
    """Safely build a PostgreSQL tsquery OR string for the simple dictionary."""
    if not tokens:
        return None
    # Each token is wrapped in single quotes; internal quotes are escaped
    quoted = ["'" + t.replace("'", "''") + "'" for t in tokens]
    return " | ".join(quoted)


class RetrievalRepository:
    """Repository executing candidate retrieval bounded to an authorized tenant."""

    def __init__(self, session: Session, company_id: UUID) -> None:
        """Bind repository to tenant company UUID.

        Args:
            session: Active database session (runtime rag_app connection).
            company_id: Authorized tenant identifier.

        Raises:
            TypeError: If company_id is not a UUID.
        """
        if not isinstance(company_id, UUID):
            raise TypeError("company_id must be a uuid.UUID")
        self.session = session
        self.company_id = company_id

    def dense_search(
        self,
        query_vector: list[float],
        limit: int = 20,
    ) -> list[RetrievedChunk]:
        """Perform dense cosine similarity search against indexed company chunks.

        Args:
            query_vector: Normalized 1024-dimensional query vector.
            limit: Maximum candidate chunks to retrieve.

        Returns:
            List of RetrievedChunk candidates ranked by dense similarity.
        """
        if limit <= 0:
            return []

        distance_expr = Chunk.embedding.cosine_distance(query_vector).label("distance")

        stmt = (
            select(
                Chunk.id,
                Chunk.document_id,
                Document.filename.label("document_name"),
                Chunk.chunk_index,
                Chunk.page,
                Chunk.section,
                Chunk.content,
                Chunk.token_count,
                distance_expr,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.company_id == self.company_id,
                Document.company_id == self.company_id,
                Document.status == "indexed",
            )
            .order_by(
                Chunk.embedding.cosine_distance(query_vector).asc(),
                Chunk.id.asc(),
            )
            .limit(limit)
        )

        rows = self.session.execute(stmt).all()
        candidates: list[RetrievedChunk] = []

        for idx, row in enumerate(rows):
            distance = float(row.distance) if row.distance is not None else 1.0
            similarity = max(0.0, 1.0 - distance)
            rank = idx + 1
            candidates.append(
                RetrievedChunk(
                    rank=rank,
                    chunk_id=row.id,
                    document_id=row.document_id,
                    document_name=row.document_name,
                    chunk_index=row.chunk_index,
                    page=row.page,
                    section=row.section,
                    content=row.content,
                    body=strip_context_header(row.content),
                    token_count=row.token_count or 0,
                    score=similarity,
                    dense_rank=rank,
                    dense_similarity=similarity,
                    keyword_rank=None,
                    keyword_score=None,
                    rrf_score=None,
                    rerank_score=None,
                )
            )

        return candidates

    def keyword_search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[RetrievedChunk]:
        """Perform keyword search with ts_rank_cd against indexed company chunks.

        Args:
            query: Normalized query string.
            limit: Maximum candidate chunks to retrieve.

        Returns:
            List of RetrievedChunk candidates ranked by keyword score.
        """
        if limit <= 0:
            return []

        tokens = extract_search_tokens(query)
        tsquery_str = build_or_tsquery_expression(tokens)
        if tsquery_str is None:
            return []

        ts_query = func.to_tsquery("simple", tsquery_str)
        rank_score_expr = func.ts_rank_cd(Chunk.tsv, ts_query).label("rank_score")

        stmt = (
            select(
                Chunk.id,
                Chunk.document_id,
                Document.filename.label("document_name"),
                Chunk.chunk_index,
                Chunk.page,
                Chunk.section,
                Chunk.content,
                Chunk.token_count,
                rank_score_expr,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.company_id == self.company_id,
                Document.company_id == self.company_id,
                Document.status == "indexed",
                Chunk.tsv.op("@@")(ts_query),
            )
            .order_by(
                text("rank_score DESC"),
                Chunk.id.asc(),
            )
            .limit(limit)
        )

        rows = self.session.execute(stmt).all()
        candidates: list[RetrievedChunk] = []

        for idx, row in enumerate(rows):
            score = float(row.rank_score) if row.rank_score is not None else 0.0
            rank = idx + 1
            candidates.append(
                RetrievedChunk(
                    rank=rank,
                    chunk_id=row.id,
                    document_id=row.document_id,
                    document_name=row.document_name,
                    chunk_index=row.chunk_index,
                    page=row.page,
                    section=row.section,
                    content=row.content,
                    body=strip_context_header(row.content),
                    token_count=row.token_count or 0,
                    score=score,
                    dense_rank=None,
                    dense_similarity=None,
                    keyword_rank=rank,
                    keyword_score=score,
                    rrf_score=None,
                    rerank_score=None,
                )
            )

        return candidates
