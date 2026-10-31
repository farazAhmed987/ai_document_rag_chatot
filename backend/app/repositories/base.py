"""Tenant and privileged administration transaction boundaries.

SRD Section 7 (isolation layer 1); MT-1, MT-3, MT-5.
Runtime repositories MUST obtain sessions from tenant_session, which
assumes a non-BYPASSRLS role before setting the tenant ID. No privileged
fallback is permitted. admin_session is restricted to company creation,
migrations, and maintenance; never use it for user requests.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.errors import TenantRoleError

_ROLE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
_ADMIN_MARKER = "rag_admin_session"
_TENANT_MARKER = "rag_tenant_id"


def _safe_role_name(role: str) -> str:
    """Validate and safely quote a SQL role identifier.

    Args:
        role: Configured PostgreSQL role name.

    Returns:
        Quoted SQL identifier.

    Raises:
        TenantRoleError: If the role name is unsafe.
    """
    if not _ROLE_PATTERN.fullmatch(role):
        raise TenantRoleError("Invalid configured DB_RUNTIME_ROLE")
    return f'"{role}"'


@contextmanager
def tenant_session(
    factory: sessionmaker[Session], company_id: UUID
) -> Iterator[Session]:
    """Open a tenant transaction using a mandatory restricted DB role.

    Args:
        factory: Session factory bound to the operational database.
        company_id: Tenant UUID validated before opening a transaction.

    Yields:
        Session under the restricted role with app.company_id set.

    Raises:
        TypeError: If the tenant ID is not a UUID.
        TenantRoleError: If the runtime role cannot be assumed.
    """
    if not isinstance(company_id, UUID):
        raise TypeError("company_id must be a uuid.UUID")

    role = _safe_role_name(get_settings().DB_RUNTIME_ROLE)
    with factory.begin() as session:
        role_assumed = False
        try:
            try:
                session.execute(text(f"SET LOCAL ROLE {role}"))
            except SQLAlchemyError as exc:
                raise TenantRoleError(
                    "Cannot assume the configured non-bypass database role"
                ) from exc
            role_assumed = True
            session.execute(
                text("SELECT set_config('app.company_id', :company_id, true)"),
                {"company_id": str(company_id)},
            )
            session.info[_TENANT_MARKER] = company_id
            yield session
        finally:
            session.info.pop(_TENANT_MARKER, None)
            if role_assumed:
                failed_body = sys.exc_info()[0] is not None
                try:
                    # A failed SQL statement can abort the transaction; in that
                    # case factory.begin() rolls back both local settings.
                    session.execute(text("RESET ROLE"))
                    session.execute(
                        text("SELECT set_config('app.company_id', '', true)")
                    )
                except SQLAlchemyError:
                    if not failed_body:
                        raise


@contextmanager
def admin_session(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Open a privileged maintenance transaction without tenant scoping.

    Only trusted administrative workflows may call this helper, and only
    for company creation, migrations and maintenance. The PostgreSQL login
    must independently be authorized to perform administrative operations.

    Args:
        factory: Session factory using a privileged admin connection.

    Yields:
        Session marked as administrative for CompanyRepository.create.
    """
    with factory.begin() as session:
        session.info[_ADMIN_MARKER] = True
        try:
            yield session
        finally:
            session.info.pop(_ADMIN_MARKER, None)


def is_admin_session(session: Session) -> bool:
    """Return whether a session entered through admin_session."""
    return session.info.get(_ADMIN_MARKER) is True
