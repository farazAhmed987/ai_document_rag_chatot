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
    """Open a tenant transaction using a direct connection under the restricted runtime role.

    SRD Section 7, 8.1; MT-1, MT-3, MT-5; A-11.
    Direct runtime connections must already be authenticated as the configured runtime role
    (e.g. rag_app) without superuser or BYPASSRLS privileges. SET ROLE is not executed.

    Args:
        factory: Session factory bound directly to the runtime database role.
        company_id: Tenant UUID validated before opening a transaction.

    Yields:
        Session under the restricted role with app.company_id set via set_config.

    Raises:
        TypeError: If the tenant ID is not a UUID.
        TenantRoleError: If the role is unsafe, the session is not under the runtime role,
            or the active role possesses superuser or BYPASSRLS privileges.
    """
    if not isinstance(company_id, UUID):
        raise TypeError("company_id must be a uuid.UUID")

    _safe_role_name(get_settings().DB_RUNTIME_ROLE)
    expected_role = get_settings().DB_RUNTIME_ROLE

    with factory.begin() as session:
        # Verify active and session roles match the configured restricted runtime role
        try:
            row = session.execute(
                text(
                    """
                    SELECT
                        session_user,
                        current_user,
                        (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS rolbypassrls,
                        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS rolsuper
                    """
                )
            ).one()
        except SQLAlchemyError as exc:
            raise TenantRoleError("Failed to query database role privileges") from exc

        if row.session_user != expected_role or row.current_user != expected_role:
            raise TenantRoleError(
                f"tenant_session requires connection role '{expected_role}', "
                f"found session_user='{row.session_user}' current_user='{row.current_user}'"
            )

        if row.rolbypassrls or row.rolsuper:
            raise TenantRoleError(
                "tenant_session cannot run with superuser or BYPASSRLS privileges"
            )

        # Set transaction-local tenant context without SET ROLE
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": str(company_id)},
        )
        session.info[_TENANT_MARKER] = company_id
        try:
            yield session
        finally:
            session.info.pop(_TENANT_MARKER, None)


@contextmanager
def admin_assumed_tenant_session(
    factory: sessionmaker[Session], company_id: UUID
) -> Iterator[Session]:
    """Open an administrative maintenance transaction that assumes a tenant role via SET LOCAL ROLE.

    Note: This is reserved exclusively for Hardening Part A regression tests and trusted
    administrative maintenance. No production ingestion, CLI, or service path may call this helper.

    Args:
        factory: Session factory bound to an administrative database connection.
        company_id: Tenant UUID.

    Yields:
        Session under assumed restricted role with app.company_id set.
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
