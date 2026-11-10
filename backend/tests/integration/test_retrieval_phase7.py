"""Integration tests for tenant-safe retrieval and RLS isolation.

SRD Sections 8.2, 9; DM-3, IN-5, MT-1, MT-3, MT-5; A-12.
All tests connect to the operational database and run under pytest.mark.db.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.company import Company
from app.models.document import Document
from app.repositories.base import admin_session, tenant_session
from app.repositories.company import CompanyRepository
from app.repositories.retrieval import RetrievalRepository

pytestmark = pytest.mark.db


def _make_admin_engine():
    """Create admin engine connecting as superuser/admin."""
    return create_engine(
        get_settings().require_database_url(),
        connect_args={"options": "-c search_path=public,extensions"},
    )


def _make_runtime_engine():
    """Create restricted runtime engine connecting as rag_app with active RLS."""
    return create_engine(
        get_settings().require_runtime_database_url(),
        connect_args={"options": "-c search_path=public,extensions"},
    )


def test_db_retrieval_tenant_isolation_and_status_filtering() -> None:
    """Multi-tenant isolation and document status filtering under restricted rag_app role."""
    admin_engine = _make_admin_engine()
    runtime_engine = _make_runtime_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    prefix = f"test_p7_{uuid4().hex[:8]}"
    comp_a_name = f"{prefix}_CompanyA"
    comp_b_name = f"{prefix}_CompanyB"

    comp_a_id = uuid4()
    comp_b_id = uuid4()

    try:
        # 1. Setup two distinct tenants via admin session
        with admin_session(admin_factory) as admin_s:
            comp_repo = CompanyRepository(admin_s)
            comp_repo.create(comp_a_name, company_id=comp_a_id)
            comp_repo.create(comp_b_name, company_id=comp_b_id)

            # Document 1 for Company A (status: indexed)
            doc_a1_id = uuid4()
            doc_a1 = Document(
                id=doc_a1_id,
                company_id=comp_a_id,
                filename="A_indexed.pdf",
                file_type="pdf",
                file_path=f"storage/{doc_a1_id}.pdf",
                sha256=f"hash_a1_{uuid4().hex}",
                status="indexed",
                chunk_count=1,
            )
            admin_s.add(doc_a1)

            # Document 2 for Company A (status: failed - should be excluded from search)
            doc_a2_id = uuid4()
            doc_a2 = Document(
                id=doc_a2_id,
                company_id=comp_a_id,
                filename="A_failed.pdf",
                file_type="pdf",
                file_path=f"storage/{doc_a2_id}.pdf",
                sha256=f"hash_a2_{uuid4().hex}",
                status="failed",
                chunk_count=1,
            )
            admin_s.add(doc_a2)

            # Document 1 for Company B (status: indexed - should be excluded from Company A search)
            doc_b1_id = uuid4()
            doc_b1 = Document(
                id=doc_b1_id,
                company_id=comp_b_id,
                filename="B_indexed.pdf",
                file_type="pdf",
                file_path=f"storage/{doc_b1_id}.pdf",
                sha256=f"hash_b1_{uuid4().hex}",
                status="indexed",
                chunk_count=1,
            )
            admin_s.add(doc_b1)
            admin_s.flush()

            # Orthogonal 1024-d unit vectors for deterministic cosine distance
            vec_target = [1.0] + [0.0] * 1023

            # Chunk for Company A (indexed, target vector, unique keyword 'emerald')
            chunk_a1 = Chunk(
                id=uuid4(),
                company_id=comp_a_id,
                document_id=doc_a1_id,
                chunk_index=0,
                page=1,
                section="Overview",
                content="Company: A\nDocument: A_indexed.pdf\nSection: Overview\n\nEmerald property lease details.",
                token_count=20,
                embedding=vec_target,
            )
            # Chunk for Company A (failed document, target vector, keyword 'emerald')
            chunk_a2 = Chunk(
                id=uuid4(),
                company_id=comp_a_id,
                document_id=doc_a2_id,
                chunk_index=0,
                page=1,
                section="Overview",
                content="Company: A\nDocument: A_failed.pdf\nSection: Overview\n\nEmerald failed document text.",
                token_count=20,
                embedding=vec_target,
            )
            # Chunk for Company B (indexed, target vector, keyword 'emerald')
            chunk_b1 = Chunk(
                id=uuid4(),
                company_id=comp_b_id,
                document_id=doc_b1_id,
                chunk_index=0,
                page=1,
                section="Overview",
                content="Company: B\nDocument: B_indexed.pdf\nSection: Overview\n\nEmerald Company B secret property.",
                token_count=20,
                embedding=vec_target,
            )
            admin_s.add_all([chunk_a1, chunk_a2, chunk_b1])
            admin_s.commit()

        # 2. Query as Company A under restricted runtime rag_app connection
        with tenant_session(runtime_factory, comp_a_id) as ts:
            repo_a = RetrievalRepository(session=ts, company_id=comp_a_id)

            # Test A: Dense search
            dense_results = repo_a.dense_search(query_vector=vec_target, limit=10)
            assert len(dense_results) == 1
            retrieved = dense_results[0]
            assert retrieved.document_name == "A_indexed.pdf"
            assert retrieved.document_id == doc_a1_id
            assert pytest.approx(retrieved.dense_similarity, 1e-4) == 1.0

            # Verify no chunks from failed doc_a2 or cross-tenant doc_b1 were returned
            retrieved_doc_ids = {c.document_id for c in dense_results}
            assert doc_a2_id not in retrieved_doc_ids
            assert doc_b1_id not in retrieved_doc_ids

            # Test B: Keyword search for 'emerald'
            kw_results = repo_a.keyword_search(query="emerald lease", limit=10)
            assert len(kw_results) == 1
            assert kw_results[0].document_name == "A_indexed.pdf"
            assert kw_results[0].document_id == doc_a1_id
            assert kw_results[0].keyword_score > 0.0

            # Test C: Noise query does not crash or return invalid rows
            noise_results = repo_a.keyword_search(query="??? !!! @#$%", limit=10)
            assert noise_results == []

        # 3. Query as Company B: must only see Company B's chunk
        with tenant_session(runtime_factory, comp_b_id) as ts:
            repo_b = RetrievalRepository(session=ts, company_id=comp_b_id)
            dense_b = repo_b.dense_search(query_vector=vec_target, limit=10)
            assert len(dense_b) == 1
            assert dense_b[0].document_name == "B_indexed.pdf"
            assert dense_b[0].document_id == doc_b1_id

    finally:
        # Cleanup test tenants
        with admin_session(admin_factory) as admin_s:
            admin_s.execute(
                delete(Company).where(Company.name.in_([comp_a_name, comp_b_name]))
            )
            admin_s.commit()

            # Confirm cleanup
            remaining = admin_s.scalar(
                select(Company.id).where(Company.name.in_([comp_a_name, comp_b_name]))
            )
            assert remaining is None, "Test companies must be cleanly deleted"
