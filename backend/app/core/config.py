"""Centralized secure application settings.

SRD Sections 7, 9, 13 and 14; MT-1, MT-3, RG-11.
No database connection or credential logging occurs during settings loading.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Application environment with masked credentials and bounded limits."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    APP_ENV: str = "development"
    DATABASE_URL: SecretStr | None = None
    ANALYTICS_DATABASE_URL: SecretStr | None = None
    RUNTIME_DATABASE_URL: SecretStr | None = None
    API_KEY: SecretStr | None = None
    MODEL_NAME: str | None = None
    REWRITE_MODEL_NAME: str | None = None
    LLM_PROVIDER: str = "gemini"
    DB_RUNTIME_ROLE: str = "rag_app"

    EMBEDDING_MODEL_NAME: str = "BAAI/bge-m3"
    EMBEDDING_MODEL_REVISION: str = "5617a9f61b028005a4858fdac845db406aefb181"
    EMBEDDING_DIMENSION: int = Field(default=1024, gt=0)
    EMBEDDER_BACKEND: Literal["bge-m3", "fake"] = "bge-m3"
    EMBEDDING_BATCH_SIZE: int = Field(default=16, gt=0)
    EMBEDDING_MAX_LENGTH: int = Field(default=2048, ge=2048, le=8192)
    RERANKER_MODEL_NAME: str = "BAAI/bge-reranker-v2-m3"

    STORAGE_BACKEND: str = "local"
    STORAGE_PATH: Path = REPO_ROOT / "data" / "storage"
    SUPABASE_URL: str | None = None
    MAX_UPLOAD_MB: int = Field(default=20, gt=0)
    MAX_PAGES: int = Field(default=300, gt=0)
    MAX_EXTRACTED_CHARS: int = Field(default=5_000_000, gt=0)
    MAX_ZIP_UNCOMPRESSED_MB: int = Field(default=200, gt=0)

    CHUNK_TOKENS: int = Field(default=500, gt=0)
    CHUNK_OVERLAP: int = Field(default=60, ge=0)
    MIN_CHUNK_TOKENS: int = Field(default=80, gt=0)
    MAX_TABLE_TOKENS: int = Field(default=1500, gt=0)
    DENSE_K: int = Field(default=20, gt=0)
    KEYWORD_K: int = Field(default=20, gt=0)
    RERANK_TOP_N: int = Field(default=5, gt=0)
    MIN_RELEVANCE: float | None = None
    HISTORY_TURNS: int = Field(default=6, ge=0)
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    QA_LOGGING_ENABLED: bool = True
    QA_RETENTION_DAYS: int = Field(default=365, gt=0)
    QA_SPILL_PATH: Path = REPO_ROOT / "data" / "qa_spill"

    @field_validator(
        "DATABASE_URL",
        "ANALYTICS_DATABASE_URL",
        "RUNTIME_DATABASE_URL",
        mode="before",
    )
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        """Normalize supported PostgreSQL schemes to psycopg 3.

        Args:
            value: Plain URL, SecretStr or unset value.

        Returns:
            SecretStr containing the normalized URL, or the unset value.
        """
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            value = "postgresql+psycopg://" + value[len("postgres://") :]
        elif value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value[len("postgresql://") :]
        return SecretStr(value)

    @field_validator("EMBEDDING_MODEL_REVISION")
    @classmethod
    def validate_embedding_revision(cls, value: str) -> str:
        """Require an immutable lowercase Hugging Face commit revision."""
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("EMBEDDING_MODEL_REVISION must be a 40-character SHA")
        return value

    @field_validator("STORAGE_BACKEND")
    @classmethod
    def validate_storage_backend(cls, value: str) -> str:
        """Allow only local storage backend."""
        if value != "local":
            raise ValueError("STORAGE_BACKEND currently allows only 'local'")
        return value

    @field_validator("STORAGE_PATH", mode="before")
    @classmethod
    def validate_storage_path(cls, value: object) -> Path:
        """Ensure STORAGE_PATH is non-empty."""
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("STORAGE_PATH cannot be empty")
            p = Path(value)
            return p if p.is_absolute() else REPO_ROOT / p
        if isinstance(value, Path):
            if not str(value).strip():
                raise ValueError("STORAGE_PATH cannot be empty")
            return value
        raise ValueError("STORAGE_PATH must be a valid path")

    @model_validator(mode="after")
    def validate_chunk_window(self) -> Settings:
        """Require a strictly smaller overlap than the chunk size."""
        if self.CHUNK_OVERLAP >= self.CHUNK_TOKENS:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_TOKENS")
        if self.MIN_CHUNK_TOKENS >= self.CHUNK_TOKENS:
            raise ValueError("MIN_CHUNK_TOKENS must be smaller than CHUNK_TOKENS")
        if self.MAX_TABLE_TOKENS < self.CHUNK_TOKENS:
            raise ValueError("MAX_TABLE_TOKENS must be at least CHUNK_TOKENS")
        return self

    def require_database_url(self, *, analytics: bool = False) -> str:
        """Expose a connection URL only to an explicit database caller.

        Args:
            analytics: Select the separate analytics database.

        Returns:
            SQLAlchemy-compatible PostgreSQL URL.

        Raises:
            ValueError: If the requested URL is not configured.
        """
        key = "ANALYTICS_DATABASE_URL" if analytics else "DATABASE_URL"
        secret = self.ANALYTICS_DATABASE_URL if analytics else self.DATABASE_URL
        if secret is None or not secret.get_secret_value():
            raise ValueError(f"{key} is not configured; set it in the root .env")
        return secret.get_secret_value()

    def require_runtime_database_url(self) -> str:
        """Expose runtime connection URL for rag_app without logging it.

        Returns:
            SQLAlchemy-compatible PostgreSQL URL for rag_app.

        Raises:
            ValueError: If RUNTIME_DATABASE_URL is not configured.
        """
        secret = self.RUNTIME_DATABASE_URL
        if secret is None or not secret.get_secret_value():
            raise ValueError(
                "RUNTIME_DATABASE_URL is not configured; set it in the root .env"
            )
        return secret.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached, validated runtime settings."""
    return Settings()
