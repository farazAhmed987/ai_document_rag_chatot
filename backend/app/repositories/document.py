"""Tenant-scoped document persistence and explicit status field updates.

SRD Sections 7, 8.1 and 9; DM-3, DM-5, DM-7 and MT-1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import DOCUMENT_STATUSES, Document


class _UnsetType:
    """Distinguish omitted keyword arguments from explicit None."""


UNSET: Final = _UnsetType()


class DocumentRepository:
    """Document operations constrained to one company UUID."""

    def __init__(self, session: Session, company_id: UUID) -> None:
        """Bind document operations to the authorized company."""
        if not isinstance(company_id, UUID):
            raise TypeError("company_id must be a uuid.UUID")
        self.session = session
        self.company_id = company_id

    def create(
        self,
        *,
        filename: str,
        file_type: str,
        file_path: str,
        sha256: str,
        size_bytes: int | None = None,
    ) -> Document:
        """Insert a validated document with initial uploaded status."""
        document = Document(
            company_id=self.company_id,
            filename=filename,
            file_type=file_type,
            file_path=file_path,
            sha256=sha256,
            size_bytes=size_bytes,
            status="uploaded",
        )
        self.session.add(document)
        self.session.flush()
        return document

    def get(self, document_id: UUID) -> Document | None:
        """Return the requested document only for the authorized tenant."""
        return self.session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.company_id == self.company_id,
            )
        )

    def find_by_sha256(self, sha256: str) -> Document | None:
        """Check for an existing matching document in this company."""
        return self.session.scalar(
            select(Document).where(
                Document.company_id == self.company_id,
                Document.sha256 == sha256,
            )
        )

    def update_status(
        self,
        document_id: UUID,
        status: str,
        *,
        error_message: str | None | _UnsetType = UNSET,
        warnings: list[str] | _UnsetType = UNSET,
        page_count: int | None = None,
        chunk_count: int | None = None,
        processed_at: datetime | None = None,
    ) -> Document | None:
        """Update status without silently clearing previous error details.

        Args:
            document_id: Owned document UUID.
            status: One of uploaded, processing, indexed or failed.
            error_message: Omitted means preserve; explicit None clears.
            warnings: Omitted means preserve; provided list replaces.
            page_count: Optional replacement page count.
            chunk_count: Optional replacement chunk count.
            processed_at: Optional processing completion timestamp.

        Returns:
            Updated document, or None if not visible to tenant.
        """
        if status not in DOCUMENT_STATUSES:
            raise ValueError(f"Invalid document status: {status}")
        document = self.get(document_id)
        if document is None:
            return None
        document.status = status
        if not isinstance(error_message, _UnsetType):
            document.error_message = error_message
        if not isinstance(warnings, _UnsetType):
            document.warnings = list(warnings)
        if page_count is not None:
            document.page_count = page_count
        if chunk_count is not None:
            document.chunk_count = chunk_count
        if processed_at is not None:
            document.processed_at = processed_at
        self.session.flush()
        return document

    def delete(self, document_id: UUID) -> bool:
        """Delete one owned document and DB-cascaded chunks."""
        document = self.get(document_id)
        if document is None:
            return False
        self.session.delete(document)
        self.session.flush()
        return True

    def list(self) -> list[Document]:
        """Return owned documents sorted newest first."""
        query = (
            select(Document)
            .where(Document.company_id == self.company_id)
            .order_by(Document.created_at.desc(), Document.id)
        )
        return list(self.session.scalars(query))
