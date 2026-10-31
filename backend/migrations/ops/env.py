"""Alembic operational database environment.

SRD Sections 7 and 9; requirements MT-1, MT-3 and MT-5.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate operational migration SQL without connecting."""
    context.configure(
        url=get_settings().require_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_ops",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply operational migrations to DATABASE_URL."""
    engine = create_engine(
        get_settings().require_database_url(),
        poolclass=pool.NullPool,
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                version_table="alembic_version_ops",
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
