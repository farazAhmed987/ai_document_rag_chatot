"""Application settings and database URL normalization.

SRD Sections 7, 9, 12 and 13.
Requirements: MT-1, MT-3 and RG-11.

All runtime configuration is managed by pydantic-settings.
Database connections are not opened by this module.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Validated application configuration.

    The repository-root .env is used regardless of the caller's
    working directory. Environment variables override .env values.

    Empty environment values are ignored so a copied .env.example
    remains usable before external services are configured.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    APP_ENV: str = "development"

    DATABASE_URL: str | None = None
    ANALYTICS_DATABASE_URL: str | None = None

    API_KEY: str | None = None
    MODEL_NAME: str | None = None
    REWRITE_MODEL_NAME: str | None = None
    LLM_PROVIDER: str = "gemini"

    EMBEDDING_MODEL_NAME: str = "BAAI/bge-m3"
    RERANKER_MODEL_NAME: str = "BAAI/bge-reranker-v2-m3"

    STORAGE_BACKEND: str = "local"
    STORAGE_PATH: Path = REPO_ROOT / "data"
    SUPABASE_URL: str | None = None

    MAX_UPLOAD_MB: int = Field(default=20, gt=0)

    CHUNK_TOKENS: int = Field(default=500, gt=0)
    CHUNK_OVERLAP: int = Field(default=60, ge=0)

    DENSE_K: int = Field(default=20, gt=0)
    KEYWORD_K: int = Field(default=20, gt=0)
    RERANK_TOP_N: int = Field(default=5, gt=0)

    MIN_RELEVANCE: float | None = None

    HISTORY_TURNS: int = Field(default=6, ge=0)

    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    LOG_LEVEL: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = "INFO"

    @field_validator(
        "DATABASE_URL",
        "ANALYTICS_DATABASE_URL",
        mode="before",
    )
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        """Normalize PostgreSQL URLs to the psycopg 3 driver.

        Args:
            value: Original URL or optional empty value.

        Returns:
            URL with the SQLAlchemy psycopg driver identifier.
        """
        if not isinstance(value, str):
            return value

        if value.startswith("postgresql+psycopg://"):
            return value

        if value.startswith("postgresql://"):
            return value.replace(
                "postgresql://",
                "postgresql+psycopg://",
                1,
            )

        if value.startswith("postgres://"):
            return value.replace(
                "postgres://",
                "postgresql+psycopg://",
                1,
            )

        return value

    def require_database_url(
        self,
        *,
        analytics: bool = False,
    ) -> str:
        """Return a configured PostgreSQL URL or fail explicitly.

        Args:
            analytics: Select the analytics database instead of
                the operational database.

        Returns:
            SQLAlchemy-compatible PostgreSQL connection URL.

        Raises:
            ValueError: When the requested URL is not configured.
        """
        name = "ANALYTICS_DATABASE_URL" if analytics else "DATABASE_URL"

        url = self.ANALYTICS_DATABASE_URL if analytics else self.DATABASE_URL

        if not url:
            raise ValueError(
                f"{name} is not configured. Set it in the "
                "repository-root .env before connecting to "
                "the database."
            )

        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached validated settings.

    Returns:
        The application settings instance.
    """
    return Settings()
