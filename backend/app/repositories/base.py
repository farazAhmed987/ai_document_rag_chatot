"""Tenant-scoped transactional SQLAlchemy session helper.

SRD Section 7 (isolation layer 1); requirements MT-1 and MT-3.

Callers must use a PostgreSQL login without BYPASSRLS or table ownership
privileges. Migration/admin credentials are not suitable runtime credentials.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


@contextmanager
def tenant_session(
    factory: sessionmaker[Session], company_id: UUID
) -> Iterator[Session]:
    """Start a transaction scoped to one company using SET LOCAL.

    Args:
        factory: A SQLAlchemy sessionmaker bound to the operational database.
        company_id: Required tenant UUID; never derived from the document itself.

    Yields:
        Open SQLAlchemy session with PostgreSQL app.company_id set.

    Raises:
        TypeError: If company_id is not a UUID.
    """
    if not isinstance(company_id, UUID):
        raise TypeError("company_id must be a uuid.UUID")

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": str(company_id)},
        )
        yield session
