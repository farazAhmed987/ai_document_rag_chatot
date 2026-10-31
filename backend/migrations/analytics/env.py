"""Independent analytics Alembic migration environment.

SRD Sections 9 and 15; no foreign keys to the operational database.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings

config = context.config


def run_migrations_offline() -> None:
    """Generate analytics migration SQL without connecting."""
    context.configure(
        url=get_settings().require_database_url(analytics=True),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_analytics",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply analytics migrations only to ANALYTICS_DATABASE_URL."""
    engine = create_engine(
        get_settings().require_database_url(analytics=True),
        poolclass=pool.NullPool,
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                version_table="alembic_version_analytics",
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
