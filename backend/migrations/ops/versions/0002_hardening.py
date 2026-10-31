"""Harden runtime RLS and document/chunk integrity without modifying 0001.

Revision ID: 0002_ops_hardening
Revises: 0001_ops_initial
SRD Sections 7 and 9; MT-1, MT-3, MT-5, DM-3, IN-5.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_ops_hardening"
down_revision: str | None = "0001_ops_initial"
branch_labels: str | None = None
depends_on: str | None = None

_COMPANY_SCOPE = "id = NULLIF(current_setting('app.company_id', true), '')::uuid"


def _original_chunk_fk() -> str:
    """Resolve the existing 0001 document FK without assuming its name."""
    bind = op.get_bind()
    for foreign_key in sa.inspect(bind).get_foreign_keys("chunks", schema="public"):
        if foreign_key["referred_table"] == "documents" and foreign_key[
            "constrained_columns"
        ] == ["document_id"]:
            name = foreign_key.get("name")
            if not isinstance(name, str):
                raise RuntimeError("Original chunks-document FK has no name")
            return name
    raise RuntimeError("Original chunks-document foreign key is missing")


def upgrade() -> None:
    """Apply strict RLS, composite FK, warnings and query indexes."""
    op.execute("SET LOCAL search_path TO public, extensions")
    op.execute("ALTER TABLE companies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE companies FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON companies FOR ALL "
        f"USING ({_COMPANY_SCOPE}) WITH CHECK ({_COMPANY_SCOPE})"
    )
    op.execute("REVOKE INSERT, UPDATE, DELETE ON companies FROM rag_app")
    op.execute("GRANT SELECT ON companies TO rag_app")

    op.create_unique_constraint(
        "uq_documents_id_company", "documents", ["id", "company_id"]
    )
    original_fk_name = _original_chunk_fk()
    op.drop_constraint(original_fk_name, "chunks", type_="foreignkey")
    op.create_foreign_key(
        "fk_chunks_document_company_documents",
        "chunks",
        "documents",
        ["document_id", "company_id"],
        ["id", "company_id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_chunks_document_chunk_index", "chunks", ["document_id", "chunk_index"]
    )
    op.add_column(
        "documents",
        sa.Column(
            "warnings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_index(
        "ix_documents_company_status", "documents", ["company_id", "status"]
    )
    op.create_index(
        "ix_messages_session_created", "messages", ["session_id", "created_at"]
    )


def downgrade() -> None:
    """Reverse 0002 and restore the original 0001 schema."""
    op.execute("SET LOCAL search_path TO public, extensions")
    op.drop_index("ix_messages_session_created", table_name="messages")
    op.drop_index("ix_documents_company_status", table_name="documents")
    op.drop_column("documents", "warnings")
    op.drop_constraint("uq_chunks_document_chunk_index", "chunks", type_="unique")
    op.drop_constraint(
        "fk_chunks_document_company_documents", "chunks", type_="foreignkey"
    )
    op.create_foreign_key(
        "chunks_document_id_fkey",
        "chunks",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_documents_id_company", "documents", type_="unique")

    op.execute("GRANT INSERT, UPDATE, DELETE ON companies TO rag_app")
    op.execute("DROP POLICY tenant_isolation ON companies")
    op.execute("ALTER TABLE companies NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE companies DISABLE ROW LEVEL SECURITY")
