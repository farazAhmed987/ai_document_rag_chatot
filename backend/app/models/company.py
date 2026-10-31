"""Tenant record for operational data.

SRD Section 9; requirements MT-1 and MT-4.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Company(Base):
    """Organization owning all documents, chunks and chat sessions."""

    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    embedding_model: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'BAAI/bge-m3'")
    )
    embedding_dim: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1024")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
