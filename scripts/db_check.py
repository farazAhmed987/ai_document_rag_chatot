"""Inspect operational and analytics PostgreSQL schema health.

SRD Sections 7, 9 and 15; requirements MT-3 and MT-5.

Usage (from the repository root):
    python scripts/db_check.py

Never prints database connection URLs or credentials.
"""

from __future__ import annotations

import argparse

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.core.config import REPO_ROOT, get_settings
from sqlalchemy import create_engine, inspect, text

OPS_TABLES = {"companies", "documents", "chunks", "chat_sessions", "messages"}
ANALYTICS_TABLES = {"qa_events", "qa_feedback", "qa_evaluations"}
RLS_TABLES = {"companies", "documents", "chunks", "chat_sessions", "messages"}


def check_database(*, analytics: bool) -> bool:
    """Check connectivity, schema, RLS, pgvector, and migration head.

    Args:
        analytics: Select ANALYTICS_DATABASE_URL if True.

    Returns:
        True if every required check passes.
    """
    settings = get_settings()
    label = "analytics" if analytics else "operational"
    ini = "alembic_analytics.ini" if analytics else "alembic_ops.ini"
    version_table = "alembic_version_analytics" if analytics else "alembic_version_ops"
    required = ANALYTICS_TABLES if analytics else OPS_TABLES

    alembic_config = Config(str(REPO_ROOT / "backend" / ini))
    migration_heads = set(ScriptDirectory.from_config(alembic_config).get_heads())

    engine = create_engine(
        settings.require_database_url(analytics=analytics),
        connect_args={
            "options": "-c search_path=public,extensions",
            "connect_timeout": 10,
        },
    )
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SHOW server_version")).scalar_one()
            tables = set(inspect(connection).get_table_names(schema="public"))
            missing = required - tables

            migration_context = MigrationContext.configure(
                connection,
                opts={"version_table": version_table},
            )
            current_heads = set(migration_context.get_current_heads())

            print(f"[{label}] PostgreSQL: {version}")
            print(f"[{label}] Tables: {', '.join(sorted(tables))}")
            print(f"[{label}] Alembic: {sorted(current_heads)}")
            print(f"[{label}] Expected: {sorted(migration_heads)}")

            passed = not missing and current_heads == migration_heads
            if missing:
                print(f"[{label}] Missing tables: {sorted(missing)}")

            if not analytics:
                extension_version = connection.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                ).scalar_one_or_none()
                print(f"[{label}] pgvector: {extension_version or 'MISSING'}")
                passed = passed and extension_version is not None

                rows = connection.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relname IN "
                        "('companies', 'documents', 'chunks', 'chat_sessions', 'messages')"
                    )
                ).all()
                policies = {
                    str(row[0])
                    for row in connection.execute(
                        text(
                            "SELECT tablename FROM pg_policies "
                            "WHERE schemaname='public' AND policyname='tenant_isolation'"
                        )
                    )
                }
                enabled = {
                    str(name)
                    for name, row_rls, force_rls in rows
                    if row_rls and force_rls
                }
                print(f"[{label}] RLS enabled + forced: {sorted(enabled)}")
                print(f"[{label}] tenant_isolation policies: {sorted(policies)}")
                passed = passed and enabled == RLS_TABLES and RLS_TABLES <= policies

                role_exists = connection.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='rag_app')"
                    )
                )
                print(f"[{label}] rag_app role exists: {bool(role_exists)}")
                passed = passed and bool(role_exists)
                if role_exists:
                    privileges = connection.execute(
                        text(
                            "SELECT "
                            "has_table_privilege('rag_app', 'public.companies', 'SELECT'), "
                            "has_table_privilege('rag_app', 'public.companies', 'INSERT'), "
                            "has_table_privilege('rag_app', 'public.companies', 'UPDATE'), "
                            "has_table_privilege('rag_app', 'public.companies', 'DELETE')"
                        )
                    ).one()
                    print(
                        f"[{label}] rag_app company privileges (SELECT, I, U, D): {tuple(privileges)}"
                    )
                    passed = passed and tuple(privileges) == (True, False, False, False)
                else:
                    print(
                        f"[{label}] FAIL: required non-bypass runtime role is missing"
                    )

            print(f"[{label}] {'PASS' if passed else 'FAIL'}")
            return passed
    finally:
        engine.dispose()


def main() -> None:
    """Run both database health checks and return a useful exit code."""
    parser = argparse.ArgumentParser(description="Check both RAG PostgreSQL databases")
    parser.parse_args()
    try:
        operational = check_database(analytics=False)
        analytics = check_database(analytics=True)
    except Exception as exc:  # noqa: BLE001
        # Do not leak PostgreSQL connection URLs or passwords in CLI output.
        print(f"Database check failed: {type(exc).__name__} (inspect local logs)")
        raise SystemExit(1) from None

    raise SystemExit(0 if operational and analytics else 1)


if __name__ == "__main__":
    main()
