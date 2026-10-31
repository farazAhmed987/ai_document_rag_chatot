"""Create operational PostgreSQL/pgvector tables and tenant RLS.

Revision ID: 0001_ops_initial
Revises:
SRD Sections 7 and 9; requirements DM-5, DM-7, MT-1, MT-3, MT-5.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_ops_initial"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

TENANT = "company_id = NULLIF(current_setting('app.company_id', true), '')::uuid"
MESSAGE_TENANT = (
    "EXISTS (SELECT 1 FROM chat_sessions AS s "
    "WHERE s.id = messages.session_id "
    "AND s.company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
)


def upgrade() -> None:
    """Create the complete operational schema and isolation policies."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Supabase commonly installs extensions in the extensions schema.
    op.execute("SET LOCAL search_path TO public, extensions")

    op.create_table(
        "companies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "embedding_model",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'BAAI/bge-m3'"),
        ),
        sa.Column(
            "embedding_dim",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1024"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("file_type", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'uploaded'")
        ),
        sa.Column("error_message", sa.Text()),
        sa.Column("page_count", sa.Integer()),
        sa.Column(
            "chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("company_id", "sha256", name="uq_documents_company_sha256"),
        sa.CheckConstraint(
            "status IN ('uploaded','processing','indexed','failed')",
            name="ck_documents_status_valid",
        ),
    )
    op.create_table(
        "chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("section", sa.Text()),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer()),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, content)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_chunks_company_document", "chunks", ["company_id", "document_id"]
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "chat_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("title", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", postgresql.JSONB()),
        sa.Column("answered", sa.Boolean()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('user','assistant')", name="ck_messages_role_valid"
        ),
    )

    for table in ("documents", "chunks", "chat_sessions", "messages"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    for table in ("documents", "chunks", "chat_sessions"):
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            f"USING ({TENANT}) WITH CHECK ({TENANT})"
        )
    op.execute(
        "CREATE POLICY tenant_isolation ON messages FOR ALL "
        f"USING ({MESSAGE_TENANT}) WITH CHECK ({MESSAGE_TENANT})"
    )

    # Optional non-BYPASSRLS role. Hosts without CREATE ROLE privilege may
    # still migrate; isolation testing will explicitly skip and report why.
    op.execute(
        """
        DO $do$
        BEGIN
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_app') THEN
                    CREATE ROLE rag_app NOLOGIN NOBYPASSRLS;
                END IF;
                GRANT USAGE ON SCHEMA public TO rag_app;
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    companies, documents, chunks, chat_sessions, messages TO rag_app;
                GRANT rag_app TO CURRENT_USER;
            EXCEPTION WHEN insufficient_privilege THEN
                RAISE NOTICE 'rag_app role not available; isolation test will skip';
            END;
        END
        $do$;
        """
    )


def downgrade() -> None:
    """Drop only operational objects created by this revision."""
    op.drop_table("messages")
    op.drop_table("chat_sessions")
    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")
    op.drop_index("ix_chunks_tsv", table_name="chunks")
    op.drop_index("ix_chunks_company_document", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("companies")
    # The vector extension and shared rag_app role are intentionally retained.
