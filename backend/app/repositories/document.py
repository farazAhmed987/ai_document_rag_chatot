"""Company-scoped document persistence and status updates.

SRD Sections 7, 8.1 and 9; requirements DM-3, DM-5, DM-7 and MT-1.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import DOCUMENT_STATUSES, Document


class DocumentRepository:
    """Document operations always constrained to one tenant ID."""

    def __init__(self, session: Session, company_id: UUID) -> None:
        """Bind document operations to the required company.

        Args:
            session: Tenant-scoped transaction session.
            company_id: Authorized company UUID.
        """
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
        """Persist a newly uploaded document.

        Args:
            filename: Original sanitized filename.
            file_type: Validated file type.
            file_path: Storage reference.
            sha256: Full SHA-256 hex digest.
            size_bytes: Optional file size.

        Returns:
            New persisted document with status uploaded.
        """
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
        """Return a document only when it belongs to this company.

        Args:
            document_id: Requested document UUID.

        Returns:
            Document or None; cross-tenant lookups never return rows.
        """
        return self.session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.company_id == self.company_id,
            )
        )

    def list(self) -> list[Document]:
        """List documents ordered by upload date.

        Returns:
            All documents belonging to the authorized company.
        """
        statement = (
            select(Document)
            .where(Document.company_id == self.company_id)
            .order_by(Document.created_at.desc(), Document.id)
        )
        return list(self.session.scalars(statement))

    def find_by_sha256(self, sha256: str) -> Document | None:
        """Look for a duplicate within this company only.

        Args:
            sha256: Document SHA-256.

        Returns:
            Matching document or None.
        """
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
        error_message: str | None = None,
        page_count: int | None = None,
        chunk_count: int | None = None,
        processed_at: datetime | None = None,
    ) -> Document | None:
        """Update processing fields on a tenant-owned document.

        Args:
            document_id: Target document UUID.
            status: One of the four legal document statuses.
            error_message: Optional processing error or None to clear.
            page_count: Optional page count update.
            chunk_count: Optional chunk count update.
            processed_at: Optional completion time.

        Returns:
            Updated document or None when inaccessible.

        Raises:
            ValueError: For an invalid status.
        """
        if status not in DOCUMENT_STATUSES:
            raise ValueError(f"Invalid document status: {status}")
        document = self.get(document_id)
        if document is None:
            return None
        document.status = status
        document.error_message = error_message
        if page_count is not None:
            document.page_count = page_count
        if chunk_count is not None:
            document.chunk_count = chunk_count
        if processed_at is not None:
            document.processed_at = processed_at
        self.session.flush()
        return document

    def delete(self, document_id: UUID) -> bool:
        """Delete a tenant-owned document and DB-cascaded chunks.

        Args:
            document_id: Document UUID.

        Returns:
            True only when the document was found and deleted.
        """
        document = self.get(document_id)
        if document is None:
            return False
        self.session.delete(document)
        self.session.flush()
        return True
