"""Chunk repository for tenant-scoped vector persistence.

SRD Sections 8.1, 9; DM-3, IN-5, MT-1.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.chunk import CHUNK_EMBEDDING_DIMENSION, Chunk
from app.schemas.chunking import ChunkDraft


class ChunkRepository:
    """Repository managing chunks strictly bounded to an authorized tenant."""

    def __init__(self, session: Session, company_id: UUID) -> None:
        """Bind repository to tenant company UUID."""
        if not isinstance(company_id, UUID):
            raise TypeError("company_id must be a uuid.UUID")
        self.session = session
        self.company_id = company_id

    def replace_for_document(
        self,
        document_id: UUID,
        chunks: Sequence[ChunkDraft],
        embeddings: Sequence[Sequence[float]],
    ) -> list[Chunk]:
        """Replace all chunks for a document within tenant scope."""
        if not isinstance(document_id, UUID):
            raise TypeError("document_id must be a uuid.UUID")
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Chunks count ({len(chunks)}) does not match embeddings count ({len(embeddings)})"
            )

        # Validate 1024-dimensional embeddings
        for idx, emb in enumerate(embeddings):
            if len(emb) != CHUNK_EMBEDDING_DIMENSION:
                raise ValueError(
                    f"Embedding at index {idx} has invalid dimension {len(emb)}; expected {CHUNK_EMBEDDING_DIMENSION}"
                )

        # Delete existing chunks for this document under tenant scope
        self.session.execute(
            delete(Chunk).where(
                Chunk.company_id == self.company_id,
                Chunk.document_id == document_id,
            )
        )

        chunk_rows: list[Chunk] = []
        for draft, emb in zip(chunks, embeddings, strict=True):
            row = Chunk(
                company_id=self.company_id,
                document_id=document_id,
                chunk_index=draft.chunk_index,
                page=draft.page,
                section=draft.section,
                content=draft.content,
                token_count=draft.token_count,
                embedding=list(emb),
            )
            chunk_rows.append(row)

        self.session.add_all(chunk_rows)
        self.session.flush()
        return chunk_rows

    def count_for_document(self, document_id: UUID) -> int:
        """Count persisted chunks for a document under tenant scope."""
        if not isinstance(document_id, UUID):
            raise TypeError("document_id must be a uuid.UUID")
        res = self.session.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(
                Chunk.company_id == self.company_id,
                Chunk.document_id == document_id,
            )
        )
        return int(res or 0)

    def list_for_document(
        self,
        document_id: UUID,
        limit: int = 5,
    ) -> list[Chunk]:
        """List chunks for a document ordered by chunk_index."""
        if not isinstance(document_id, UUID):
            raise TypeError("document_id must be a uuid.UUID")
        query = (
            select(Chunk)
            .where(
                Chunk.company_id == self.company_id,
                Chunk.document_id == document_id,
            )
            .order_by(Chunk.chunk_index)
            .limit(limit)
        )
        return list(self.session.scalars(query))
