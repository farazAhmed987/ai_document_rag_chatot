"""Create independent analytics QA event, feedback and evaluation tables.

Revision ID: 0001_analytics_initial
Revises:
SRD Sections 9 and 15, analytics schema deviation.

No foreign keys reference the operational database. The supplied SRD does
not include the referenced Section 9.1 table definitions; this minimal
schema makes the assumption explicit.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_analytics_initial"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create append-only QA analytics and feedback tables."""
    op.create_table(
        "qa_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True)),
        sa.Column("message_id", postgresql.UUID(as_uuid=True)),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("answered", sa.Boolean()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_qa_events_company_created_at", "qa_events", ["company_id", "created_at"]
    )
    op.create_table(
        "qa_feedback",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "rating BETWEEN 1 AND 5", name="ck_qa_feedback_rating_valid"
        ),
    )
    op.create_index("ix_qa_feedback_event_id", "qa_feedback", ["event_id"])
    op.create_table(
        "qa_evaluations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True)),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("evaluator", sa.Text()),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_qa_evaluations_run_id", "qa_evaluations", ["run_id"])


def downgrade() -> None:
    """Remove analytics tables without touching operational data."""
    op.drop_index("ix_qa_evaluations_run_id", table_name="qa_evaluations")
    op.drop_table("qa_evaluations")
    op.drop_index("ix_qa_feedback_event_id", table_name="qa_feedback")
    op.drop_table("qa_feedback")
    op.drop_index("ix_qa_events_company_created_at", table_name="qa_events")
    op.drop_table("qa_events")
