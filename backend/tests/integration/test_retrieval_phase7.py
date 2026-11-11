"""Integration tests for tenant-safe retrieval and RLS isolation.

SRD Sections 8.2, 9; DM-3, IN-5, MT-1, MT-3, MT-5; A-12.
All tests connect to the operational database and run under pytest.mark.db.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select, text
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
            CompanyRepository(admin_s, company_id=comp_a_id).create(name=comp_a_name)
            CompanyRepository(admin_s, company_id=comp_b_id).create(name=comp_b_name)

            # Document 1 for Company A (status: indexed - should be returned)
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
            # Document 2 for Company A (status: failed - excluded)
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
            # Document 3 for Company A (status: uploaded - excluded)
            doc_a3_id = uuid4()
            doc_a3 = Document(
                id=doc_a3_id,
                company_id=comp_a_id,
                filename="A_uploaded.pdf",
                file_type="pdf",
                file_path=f"storage/{doc_a3_id}.pdf",
                sha256=f"hash_a3_{uuid4().hex}",
                status="uploaded",
                chunk_count=1,
            )
            # Document 4 for Company A (status: processing - excluded)
            doc_a4_id = uuid4()
            doc_a4 = Document(
                id=doc_a4_id,
                company_id=comp_a_id,
                filename="A_processing.pdf",
                file_type="pdf",
                file_path=f"storage/{doc_a4_id}.pdf",
                sha256=f"hash_a4_{uuid4().hex}",
                status="processing",
                chunk_count=1,
            )

            # Document 1 for Company B (status: indexed - cross-tenant, excluded from Company A)
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
            admin_s.add_all([doc_a1, doc_a2, doc_a3, doc_a4, doc_b1])
            admin_s.flush()

            # Orthogonal 1024-d unit vectors for deterministic cosine distance
            vec_target = [1.0] + [0.0] * 1023

            # Chunks for Company A
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
            chunk_a3 = Chunk(
                id=uuid4(),
                company_id=comp_a_id,
                document_id=doc_a3_id,
                chunk_index=0,
                page=1,
                section="Overview",
                content="Company: A\nDocument: A_uploaded.pdf\nSection: Overview\n\nEmerald uploaded document text.",
                token_count=20,
                embedding=vec_target,
            )
            chunk_a4 = Chunk(
                id=uuid4(),
                company_id=comp_a_id,
                document_id=doc_a4_id,
                chunk_index=0,
                page=1,
                section="Overview",
                content="Company: A\nDocument: A_processing.pdf\nSection: Overview\n\nEmerald processing document text.",
                token_count=20,
                embedding=vec_target,
            )
            # Chunk for Company B
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
            admin_s.add_all([chunk_a1, chunk_a2, chunk_a3, chunk_a4, chunk_b1])

        # 2. Assertion 4: With no tenant context, raw SQL returns zero document and chunk rows
        with runtime_factory() as raw_s:
            raw_s.execute(text("RESET app.company_id"))
            no_ctx_docs = raw_s.execute(text("SELECT count(*) FROM documents")).scalar()
            no_ctx_chunks = raw_s.execute(text("SELECT count(*) FROM chunks")).scalar()
            assert no_ctx_docs == 0, (
                "With no tenant context, raw SQL returns zero document rows"
            )
            assert no_ctx_chunks == 0, (
                "With no tenant context, raw SQL returns zero chunk rows"
            )

        # 3. Query as Company A under restricted runtime rag_app connection
        with tenant_session(runtime_factory, comp_a_id) as ts:
            # Assertion 1: Direct runtime login has current_user = rag_app
            current_user = ts.execute(text("SELECT current_user")).scalar()
            assert current_user == "rag_app"

            # Assertion 2: rolbypassrls = false
            bypass_rls = ts.execute(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).scalar()
            assert bypass_rls is False

            tenant_setting = ts.execute(
                text("SELECT current_setting('app.company_id', true)")
            ).scalar()
            assert tenant_setting == str(comp_a_id)

            # Assertion 11: Transaction is read-only
            ts.execute(text("SET TRANSACTION READ ONLY"))
            ro_setting = ts.execute(
                text("SELECT current_setting('transaction_read_only')")
            ).scalar()
            assert ro_setting == "on"

            # Assertion 6: A raw SQL query with Company B's company_id while under Company A's tenant context returns zero rows
            b_docs_via_raw = ts.execute(
                text("SELECT count(*) FROM documents WHERE company_id = :b_id"),
                {"b_id": comp_b_id},
            ).scalar()
            assert b_docs_via_raw == 0, (
                "Raw SQL with Company B id under Company A context returns zero rows"
            )
            b_chunks_via_raw = ts.execute(
                text("SELECT count(*) FROM chunks WHERE company_id = :b_id"),
                {"b_id": comp_b_id},
            ).scalar()
            assert b_chunks_via_raw == 0, (
                "Raw SQL with Company B id under Company A context returns zero rows"
            )

            repo_a = RetrievalRepository(session=ts, company_id=comp_a_id)

            # Assertion 3 & 8: Correct tenant context returns tenant's rows, and ONLY indexed documents are returned
            dense_results = repo_a.dense_search(query_vector=vec_target, limit=10)
            assert len(dense_results) == 1
            retrieved = dense_results[0]
            assert retrieved.document_name == "A_indexed.pdf"
            assert retrieved.document_id == doc_a1_id
            assert pytest.approx(retrieved.dense_similarity, 1e-4) == 1.0

            # Assertion 10: Returned page equals the stored chunk page
            assert retrieved.page == chunk_a1.page == 1

            # Assertion 5 & 7: Company A cannot retrieve Company B rows, and documents with status uploaded, processing or failed are never returned
            retrieved_doc_ids = {c.document_id for c in dense_results}
            assert doc_a2_id not in retrieved_doc_ids  # failed excluded
            assert doc_a3_id not in retrieved_doc_ids  # uploaded excluded
            assert doc_a4_id not in retrieved_doc_ids  # processing excluded
            assert doc_b1_id not in retrieved_doc_ids  # cross-tenant excluded

            # Assertion 9: Repeating the same retrieval produces the same ordered chunk IDs
            dense_results_repeat = repo_a.dense_search(
                query_vector=vec_target, limit=10
            )
            assert [c.chunk_id for c in dense_results] == [
                c.chunk_id for c in dense_results_repeat
            ]

            # Keyword search verification
            kw_results = repo_a.keyword_search(query="emerald lease", limit=10)
            assert len(kw_results) == 1
            assert kw_results[0].document_name == "A_indexed.pdf"
            assert kw_results[0].document_id == doc_a1_id
            assert kw_results[0].keyword_score > 0.0
            # Returned page equals stored chunk page
            assert kw_results[0].page == chunk_a1.page == 1

            # Repeating keyword retrieval produces same ordered chunk IDs
            kw_results_repeat = repo_a.keyword_search(query="emerald lease", limit=10)
            assert [c.chunk_id for c in kw_results] == [
                c.chunk_id for c in kw_results_repeat
            ]

            # Noise query returns empty list
            noise_results = repo_a.keyword_search(query="??? !!! @#$%", limit=10)
            assert noise_results == []

        # 4. Query as Company B: Assertion 3 & 5 (tenant B sees only B, cannot see A)
        with tenant_session(runtime_factory, comp_b_id) as ts:
            repo_b = RetrievalRepository(session=ts, company_id=comp_b_id)
            dense_b = repo_b.dense_search(query_vector=vec_target, limit=10)
            assert len(dense_b) == 1
            assert dense_b[0].document_name == "B_indexed.pdf"
            assert dense_b[0].document_id == doc_b1_id
            assert doc_a1_id not in {c.document_id for c in dense_b}

    finally:
        # Assertion 12: Test rows/companies are cleaned up
        with admin_session(admin_factory) as admin_s:
            admin_s.execute(
                delete(Company).where(Company.name.in_([comp_a_name, comp_b_name]))
            )

        with admin_session(admin_factory) as admin_s:
            remaining = admin_s.scalar(
                select(Company.id).where(Company.name.in_([comp_a_name, comp_b_name]))
            )
            assert remaining is None, "Test companies must be cleanly deleted"

        admin_engine.dispose()
        runtime_engine.dispose()
