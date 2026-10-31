"""Tenant-scoped company reads and privileged company creation.

SRD Sections 7 and 9; MT-1, MT-3 and MT-4.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.repositories.base import is_admin_session


class CompanyRepository:
    """Repository bound to exactly one company for reads."""

    def __init__(self, session: Session, company_id: UUID) -> None:
        """Bind repository to the requested company UUID."""
        if not isinstance(company_id, UUID):
            raise TypeError("company_id must be a uuid.UUID")
        self.session = session
        self.company_id = company_id

    def create(self, *, name: str) -> Company:
        """Create a company exclusively through an admin session.

        Args:
            name: Unique company display name.

        Returns:
            Inserted company.

        Raises:
            PermissionError: If session is not administrative.
        """
        if not is_admin_session(self.session):
            raise PermissionError("Company creation requires admin_session")
        company = Company(id=self.company_id, name=name)
        self.session.add(company)
        self.session.flush()
        return company

    def get(self) -> Company | None:
        """Return only the company matching this repository's UUID."""
        return self.session.scalar(select(Company).where(Company.id == self.company_id))

    def list(self) -> list[Company]:
        """List at most the company matching this repository's UUID."""
        return list(
            self.session.scalars(select(Company).where(Company.id == self.company_id))
        )
