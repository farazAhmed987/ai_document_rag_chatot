"""Storage protocol interface for multi-tenant document persistence.

SRD Sections 8.1, 9; DM-3, MT-1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class Storage(Protocol):
    """Protocol defining document storage capabilities."""

    def store(
        self,
        company_id: UUID,
        document_id: UUID,
        filename: str,
        content: bytes | Path,
    ) -> Path:
        """Store document content at <root>/<company_id>/<document_id>/<filename>."""
        ...

    def get_path(
        self,
        company_id: UUID,
        document_id: UUID,
        filename: str,
    ) -> Path:
        """Return the resolved filesystem path for a stored document."""
        ...

    def delete(
        self,
        company_id: UUID,
        document_id: UUID,
        filename: str | None = None,
    ) -> None:
        """Delete a stored document file or entire document directory."""
        ...
