"""Company repository constrained to the requested tenant.

SRD Sections 7 and 9; requirements MT-1 and MT-4.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company


class CompanyRepository:
    """Tenant-scoped company record access."""

    def __init__(self, session: Session, company_id: UUID) -> None:
        """Bind this repository to one tenant.

        Args:
            session: Session already created using tenant_session.
            company_id: Authorized company ID.
        """
        if not isinstance(company_id, UUID):
            raise TypeError("company_id must be a uuid.UUID")
        self.session = session
        self.company_id = company_id

    def create(self, *, name: str) -> Company:
        """Create the already-authorized tenant company.

        Args:
            name: Unique display name.

        Returns:
            Newly inserted company record.
        """
        company = Company(id=self.company_id, name=name)
        self.session.add(company)
        self.session.flush()
        return company

    def get(self) -> Company | None:
        """Fetch only the bound company, if present.

        Returns:
            Company or None.
        """
        return self.session.get(Company, self.company_id)

    def list(self) -> list[Company]:
        """List only the authorized company.

        Returns:
            Zero or one company record.
        """
        return list(
            self.session.scalars(select(Company).where(Company.id == self.company_id))
        )
