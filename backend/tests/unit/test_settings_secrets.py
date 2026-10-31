"""Fail-first runtime secret and dimensionality regression tests.

SRD Sections 9, 13 and 14; MT-3, IN-6.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.chunk import Chunk


def test_settings_hide_database_hosts_and_passwords() -> None:
    """Prevent credentials and connection hosts leaking through diagnostics."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgres://alice:db_secret_418@ops-sensitive.example:5432/postgres",
        ANALYTICS_DATABASE_URL=(
            "postgresql://bob:analytics_secret_612@analytics-sensitive.example:5432/postgres"
        ),
        API_KEY="api_secret_729",  # gitleaks:allow
    )
    display = str(settings) + repr(settings)
    for forbidden in (
        "db_secret_418",
        "ops-sensitive.example",
        "analytics_secret_612",
        "analytics-sensitive.example",
        "api_secret_729",
    ):
        assert forbidden not in display
    assert settings.require_database_url().startswith("postgresql+psycopg://")
    assert settings.require_database_url(analytics=True).startswith(
        "postgresql+psycopg://"
    )


def test_invalid_chunk_overlap_is_rejected() -> None:
    """Chunk overlap equal to or greater than window is forbidden."""
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP"):
        Settings(_env_file=None, CHUNK_TOKENS=100, CHUNK_OVERLAP=100)
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP"):
        Settings(_env_file=None, CHUNK_TOKENS=100, CHUNK_OVERLAP=101)
    assert Settings(_env_file=None, CHUNK_TOKENS=100, CHUNK_OVERLAP=99)


def test_embedding_dimension_matches_vector_model() -> None:
    """Configured embedding dimension must match the column definition."""
    settings = Settings(_env_file=None)
    assert settings.EMBEDDING_DIMENSION == Chunk.__table__.c.embedding.type.dim == 1024


def test_security_limits_defaults() -> None:
    """Define runtime role and resource/retention limits explicitly."""
    settings = Settings(_env_file=None)
    assert settings.DB_RUNTIME_ROLE == "rag_app"
    assert settings.MAX_PAGES == 300
    assert settings.MAX_EXTRACTED_CHARS == 5_000_000
    assert settings.MAX_ZIP_UNCOMPRESSED_MB == 200
    assert settings.QA_LOGGING_ENABLED is True
    assert settings.QA_RETENTION_DAYS == 365
    assert settings.QA_SPILL_PATH.name == "qa_spill"
