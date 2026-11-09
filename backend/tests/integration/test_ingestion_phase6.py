"""Integration tests for Phase 6 end-to-end ingestion against live operational database.

SRD Sections 7, 8.1, 9; DM-3, DM-5, DM-7, IN-1 to IN-6, MT-1, MT-3, MT-5.
All tests require live database and are marked with pytest.mark.db.
Future production modules are imported at the beginning of each test body to ensure
clean ModuleNotFoundError failure during Stage 1 RED before any database state is modified.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Chunk
from app.repositories.base import admin_session, tenant_session
from app.repositories.company import CompanyRepository
from app.repositories.document import DocumentRepository

pytestmark = pytest.mark.db


class _LocalFakeEmbedder:
    """Deterministic local fake embedder for integration tests without torch or network."""

    dimension: int = 1024

    def embed_documents(
        self, texts: list[str], batch_size: int = 16
    ) -> list[list[float]]:
        del batch_size
        return [[1.0] + [0.0] * (self.dimension - 1) for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0] + [0.0] * (self.dimension - 1)


class _LocalRaisingEmbedder(_LocalFakeEmbedder):
    """Embedder that raises an error to test transaction rollback."""

    def embed_documents(
        self, texts: list[str], batch_size: int = 16
    ) -> list[list[float]]:
        del texts, batch_size
        raise RuntimeError("Controlled embedder failure for integration test")


class _LocalDeterministicTokenCounter:
    """Deterministic token counter without model or tokenizer dependencies."""

    def count(self, text: str) -> int:
        return len(text.split())


class _LocalFakeExtractorFactory:
    """Deterministic local extractor factory for integration tests."""

    def __call__(self, path: Path, **kwargs: object):
        from app.adapters.extractors.factory import extract_document

        return extract_document(path, **kwargs)

    def extract_document(self, path: Path, **kwargs: object):
        return self(path, **kwargs)


def _load_ingestion_modules() -> dict[str, ModuleType]:
    """Import future Phase 6 production modules at the start of each test body."""
    return {
        "service": importlib.import_module("app.services.ingestion"),
        "storage": importlib.import_module("app.adapters.storage.local"),
        "chunk_repo": importlib.import_module("app.repositories.chunk"),
        "cli": importlib.import_module("app.cli"),
    }


def _make_admin_engine():
    """Create administrative database engine without exposing credentials."""
    return create_engine(
        get_settings().require_database_url(),
        connect_args={"options": "-c search_path=public,extensions"},
    )


def _make_runtime_engine():
    """Create restricted runtime database engine (rag_app) without exposing credentials."""
    return create_engine(
        get_settings().require_runtime_database_url(),
        connect_args={"options": "-c search_path=public,extensions"},
    )


def test_db_upload_to_indexed_with_correct_counts(tmp_path: Path) -> None:
    """End-to-end ingestion of a valid file must transition to indexed with correct counts."""
    mods = _load_ingestion_modules()
    admin_engine = _make_admin_engine()
    runtime_engine = _make_runtime_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    company_id = uuid4()
    company_name = f"p6_test_{company_id.hex[:12]}"
    try:
        with admin_session(admin_factory) as session:
            CompanyRepository(session, company_id).create(name=company_name)

        doc_file = tmp_path / "test_doc.txt"
        doc_file.write_text(
            "Hello world.\nThis is section one.\n\nMore text.", encoding="utf-8"
        )

        storage = mods["storage"].LocalStorage(tmp_path / "storage")
        embedder = _LocalFakeEmbedder()
        token_counter = _LocalDeterministicTokenCounter()
        extractor_factory = _LocalFakeExtractorFactory()
        service = mods["service"].IngestionService(
            session_factory=runtime_factory,
            admin_session_factory=admin_factory,
            storage=storage,
            embedder=embedder,
            extractor_factory=extractor_factory,
            token_counter=token_counter,
        )

        result = service.ingest_file(company_id=company_id, file_path=doc_file)
        assert result.status == "indexed"
        assert result.chunk_count > 0

        with tenant_session(runtime_factory, company_id) as session:
            doc = DocumentRepository(session, company_id).get(result.document_id)
            assert doc is not None
            assert doc.status == "indexed"
            assert doc.chunk_count == result.chunk_count
    finally:
        with admin_session(admin_factory) as session:
            comp = CompanyRepository(session, company_id).get()
            if comp is not None:
                session.delete(comp)
        admin_engine.dispose()
        runtime_engine.dispose()


def test_db_reprocess_is_idempotent_without_duplicate_chunks(
    tmp_path: Path,
) -> None:
    """Reprocessing an existing document replaces chunks idempotently."""
    mods = _load_ingestion_modules()
    admin_engine = _make_admin_engine()
    runtime_engine = _make_runtime_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    company_id = uuid4()
    company_name = f"p6_test_{company_id.hex[:12]}"
    try:
        with admin_session(admin_factory) as session:
            CompanyRepository(session, company_id).create(name=company_name)

        doc_file = tmp_path / "test_reprocess.txt"
        doc_file.write_text("First version content.", encoding="utf-8")

        storage = mods["storage"].LocalStorage(tmp_path / "storage")
        embedder = _LocalFakeEmbedder()
        token_counter = _LocalDeterministicTokenCounter()
        extractor_factory = _LocalFakeExtractorFactory()
        service = mods["service"].IngestionService(
            session_factory=runtime_factory,
            admin_session_factory=admin_factory,
            storage=storage,
            embedder=embedder,
            extractor_factory=extractor_factory,
            token_counter=token_counter,
        )

        result1 = service.ingest_file(company_id=company_id, file_path=doc_file)
        result2 = service.reprocess(
            company_id=company_id, document_id=result1.document_id
        )

        assert result2.status == "indexed"
        assert result2.chunk_count == result1.chunk_count

        with tenant_session(runtime_factory, company_id) as session:
            chunks = list(
                session.scalars(
                    select(Chunk).where(
                        Chunk.company_id == company_id,
                        Chunk.document_id == result1.document_id,
                    )
                )
            )
            assert len(chunks) == result1.chunk_count
            indices = [c.chunk_index for c in chunks]
            assert len(indices) == len(set(indices))
    finally:
        with admin_session(admin_factory) as session:
            comp = CompanyRepository(session, company_id).get()
            if comp is not None:
                session.delete(comp)
        admin_engine.dispose()
        runtime_engine.dispose()


def test_db_delete_cascades_chunks(tmp_path: Path) -> None:
    """Deleting a document from DB must cascade-delete all of its chunks."""
    mods = _load_ingestion_modules()
    admin_engine = _make_admin_engine()
    runtime_engine = _make_runtime_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    company_id = uuid4()
    company_name = f"p6_test_{company_id.hex[:12]}"
    try:
        with admin_session(admin_factory) as session:
            CompanyRepository(session, company_id).create(name=company_name)

        doc_file = tmp_path / "to_delete.txt"
        doc_file.write_text("Text to be chunked and deleted.", encoding="utf-8")

        storage = mods["storage"].LocalStorage(tmp_path / "storage")
        embedder = _LocalFakeEmbedder()
        token_counter = _LocalDeterministicTokenCounter()
        extractor_factory = _LocalFakeExtractorFactory()
        service = mods["service"].IngestionService(
            session_factory=runtime_factory,
            admin_session_factory=admin_factory,
            storage=storage,
            embedder=embedder,
            extractor_factory=extractor_factory,
            token_counter=token_counter,
        )

        result = service.ingest_file(company_id=company_id, file_path=doc_file)
        service.delete_document(company_id=company_id, document_id=result.document_id)

        with tenant_session(runtime_factory, company_id) as session:
            doc = DocumentRepository(session, company_id).get(result.document_id)
            assert doc is None
            chunk_count = session.scalar(
                select(Chunk).where(Chunk.document_id == result.document_id)
            )
            assert chunk_count is None or chunk_count == 0
    finally:
        with admin_session(admin_factory) as session:
            comp = CompanyRepository(session, company_id).get()
            if comp is not None:
                session.delete(comp)
        admin_engine.dispose()
        runtime_engine.dispose()


def test_db_same_file_can_be_ingested_for_two_companies(
    tmp_path: Path,
) -> None:
    """Same content (SHA-256) may be ingested independently by two distinct tenants."""
    mods = _load_ingestion_modules()
    admin_engine = _make_admin_engine()
    runtime_engine = _make_runtime_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    company_a = uuid4()
    company_b = uuid4()
    name_a = f"p6_test_{company_a.hex[:12]}"
    name_b = f"p6_test_{company_b.hex[:12]}"

    try:
        with admin_session(admin_factory) as session:
            CompanyRepository(session, company_a).create(name=name_a)
            CompanyRepository(session, company_b).create(name=name_b)

        shared_file = tmp_path / "shared.txt"
        shared_file.write_text(
            "Shared content between distinct tenants.", encoding="utf-8"
        )

        storage = mods["storage"].LocalStorage(tmp_path / "storage")
        embedder = _LocalFakeEmbedder()
        token_counter = _LocalDeterministicTokenCounter()
        extractor_factory = _LocalFakeExtractorFactory()
        service = mods["service"].IngestionService(
            session_factory=runtime_factory,
            admin_session_factory=admin_factory,
            storage=storage,
            embedder=embedder,
            extractor_factory=extractor_factory,
            token_counter=token_counter,
        )

        res_a = service.ingest_file(company_id=company_a, file_path=shared_file)
        res_b = service.ingest_file(company_id=company_b, file_path=shared_file)

        assert res_a.document_id != res_b.document_id
        assert res_a.status == "indexed"
        assert res_b.status == "indexed"
    finally:
        with admin_session(admin_factory) as session:
            for c_id in (company_a, company_b):
                comp = CompanyRepository(session, c_id).get()
                if comp is not None:
                    session.delete(comp)
        admin_engine.dispose()
        runtime_engine.dispose()


def test_db_tenant_a_cannot_read_tenant_b_documents_or_chunks(
    tmp_path: Path,
) -> None:
    """Tenant A session cannot read or query Tenant B's documents or chunks."""
    mods = _load_ingestion_modules()
    admin_engine = _make_admin_engine()
    runtime_engine = _make_runtime_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    company_a = uuid4()
    company_b = uuid4()
    name_a = f"p6_test_{company_a.hex[:12]}"
    name_b = f"p6_test_{company_b.hex[:12]}"

    try:
        with admin_session(admin_factory) as session:
            CompanyRepository(session, company_a).create(name=name_a)
            CompanyRepository(session, company_b).create(name=name_b)

        b_file = tmp_path / "b_doc.txt"
        b_file.write_text("Private document belonging to tenant B.", encoding="utf-8")

        storage = mods["storage"].LocalStorage(tmp_path / "storage")
        embedder = _LocalFakeEmbedder()
        token_counter = _LocalDeterministicTokenCounter()
        extractor_factory = _LocalFakeExtractorFactory()
        service = mods["service"].IngestionService(
            session_factory=runtime_factory,
            admin_session_factory=admin_factory,
            storage=storage,
            embedder=embedder,
            extractor_factory=extractor_factory,
            token_counter=token_counter,
        )
        res_b = service.ingest_file(company_id=company_b, file_path=b_file)

        with tenant_session(runtime_factory, company_a) as session_a:
            repo_a = DocumentRepository(session_a, company_a)
            assert repo_a.get(res_b.document_id) is None
            cross_chunks = list(
                session_a.scalars(
                    select(Chunk).where(Chunk.document_id == res_b.document_id)
                )
            )
            assert len(cross_chunks) == 0
    finally:
        with admin_session(admin_factory) as session:
            for c_id in (company_a, company_b):
                comp = CompanyRepository(session, c_id).get()
                if comp is not None:
                    session.delete(comp)
        admin_engine.dispose()
        runtime_engine.dispose()


def test_db_wrong_company_repository_access_returns_empty(
    tmp_path: Path,
) -> None:
    """Instantiating DocumentRepository with tenant A UUID in tenant B session returns empty."""
    mods = _load_ingestion_modules()
    admin_engine = _make_admin_engine()
    runtime_engine = _make_runtime_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    company_a = uuid4()
    company_b = uuid4()
    name_a = f"p6_test_{company_a.hex[:12]}"
    name_b = f"p6_test_{company_b.hex[:12]}"

    try:
        with admin_session(admin_factory) as session:
            CompanyRepository(session, company_a).create(name=name_a)
            CompanyRepository(session, company_b).create(name=name_b)

        storage = mods["storage"].LocalStorage(tmp_path / "storage")
        embedder = _LocalFakeEmbedder()
        token_counter = _LocalDeterministicTokenCounter()
        extractor_factory = _LocalFakeExtractorFactory()
        service = mods["service"].IngestionService(
            session_factory=runtime_factory,
            admin_session_factory=admin_factory,
            storage=storage,
            embedder=embedder,
            extractor_factory=extractor_factory,
            token_counter=token_counter,
        )

        a_file = tmp_path / "a_doc.txt"
        a_file.write_text("Tenant A payload.", encoding="utf-8")
        res_a = service.ingest_file(company_id=company_a, file_path=a_file)

        with tenant_session(runtime_factory, company_b) as session_b:
            mismatched_repo = DocumentRepository(session_b, company_a)
            assert mismatched_repo.get(res_a.document_id) is None
            assert len(mismatched_repo.list()) == 0
    finally:
        with admin_session(admin_factory) as session:
            for c_id in (company_a, company_b):
                comp = CompanyRepository(session, c_id).get()
                if comp is not None:
                    session.delete(comp)
        admin_engine.dispose()
        runtime_engine.dispose()


def test_db_rag_app_cannot_bypass_rls_or_read_cross_tenant_rows(
    tmp_path: Path,
) -> None:
    """Runtime role rag_app has no BYPASSRLS and direct SQL cannot cross tenant boundaries."""
    mods = _load_ingestion_modules()
    admin_engine = _make_admin_engine()
    runtime_engine = _make_runtime_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    # 1. Verify rolbypassrls is False
    with admin_engine.connect() as conn:
        bypass = conn.scalar(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = :role"),
            {"role": get_settings().DB_RUNTIME_ROLE},
        )
        assert bypass is False, (
            f"{get_settings().DB_RUNTIME_ROLE} must not have BYPASSRLS"
        )

    company_a = uuid4()
    company_b = uuid4()
    name_a = f"p6_test_{company_a.hex[:12]}"
    name_b = f"p6_test_{company_b.hex[:12]}"

    try:
        with admin_session(admin_factory) as session:
            CompanyRepository(session, company_a).create(name=name_a)
            CompanyRepository(session, company_b).create(name=name_b)

        storage = mods["storage"].LocalStorage(tmp_path / "storage")
        embedder = _LocalFakeEmbedder()
        token_counter = _LocalDeterministicTokenCounter()
        extractor_factory = _LocalFakeExtractorFactory()
        service = mods["service"].IngestionService(
            session_factory=runtime_factory,
            admin_session_factory=admin_factory,
            storage=storage,
            embedder=embedder,
            extractor_factory=extractor_factory,
            token_counter=token_counter,
        )

        doc_b_file = tmp_path / "doc_b.txt"
        doc_b_file.write_text("Tenant B confidential text.", encoding="utf-8")
        res_b = service.ingest_file(company_id=company_b, file_path=doc_b_file)

        with tenant_session(runtime_factory, company_a) as session_a:
            # Under tenant-A context, direct SQL by tenant-B doc ID returns no row
            doc_row = session_a.execute(
                text("SELECT id FROM documents WHERE id = :id"),
                {"id": res_b.document_id},
            ).first()
            assert doc_row is None

            # Under tenant-A context, direct SQL for tenant-B chunks returns no rows
            chunk_rows = session_a.execute(
                text("SELECT id FROM chunks WHERE document_id = :doc_id"),
                {"doc_id": res_b.document_id},
            ).all()
            assert len(chunk_rows) == 0

            # Attempt RESET ROLE inside restricted runtime transaction
            reset_denied = False
            try:
                session_a.execute(text("RESET ROLE"))
            except (DBAPIError, SQLAlchemyError):
                reset_denied = True

            # If allowed, following query must still return no data
            if not reset_denied:
                post_reset_docs = session_a.execute(
                    text("SELECT id FROM documents WHERE id = :id"),
                    {"id": res_b.document_id},
                ).all()
                assert len(post_reset_docs) == 0
                post_reset_chunks = session_a.execute(
                    text("SELECT id FROM chunks WHERE document_id = :doc_id"),
                    {"doc_id": res_b.document_id},
                ).all()
                assert len(post_reset_chunks) == 0

    finally:
        with admin_session(admin_factory) as session:
            for c_id in (company_a, company_b):
                comp = CompanyRepository(session, c_id).get()
                if comp is not None:
                    session.delete(comp)
        admin_engine.dispose()
        runtime_engine.dispose()


def test_db_embedding_failure_rolls_back_chunks_and_preserves_other_document(
    tmp_path: Path,
) -> None:
    """Embedding failure on document B leaves document A intact and B with zero chunks."""
    mods = _load_ingestion_modules()
    admin_engine = _make_admin_engine()
    runtime_engine = _make_runtime_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    company_id = uuid4()
    company_name = f"p6_test_{company_id.hex[:12]}"
    try:
        with admin_session(admin_factory) as session:
            CompanyRepository(session, company_id).create(name=company_name)

        storage = mods["storage"].LocalStorage(tmp_path / "storage")
        embedder = _LocalFakeEmbedder()
        token_counter = _LocalDeterministicTokenCounter()
        extractor_factory = _LocalFakeExtractorFactory()
        service = mods["service"].IngestionService(
            session_factory=runtime_factory,
            admin_session_factory=admin_factory,
            storage=storage,
            embedder=embedder,
            extractor_factory=extractor_factory,
            token_counter=token_counter,
        )

        file_a = tmp_path / "doc_a.txt"
        file_a.write_text("Document A text.", encoding="utf-8")
        res_a = service.ingest_file(company_id=company_id, file_path=file_a)

        file_b = tmp_path / "doc_b.txt"
        file_b.write_text("Document B text.", encoding="utf-8")

        # Inject raising embedder directly into service
        raising_embedder = _LocalRaisingEmbedder()
        service_failing = mods["service"].IngestionService(
            session_factory=runtime_factory,
            admin_session_factory=admin_factory,
            storage=storage,
            embedder=raising_embedder,
            extractor_factory=extractor_factory,
            token_counter=token_counter,
        )
        res_b = service_failing.ingest_file(company_id=company_id, file_path=file_b)

        assert res_b.status == "failed"
        with tenant_session(runtime_factory, company_id) as session:
            doc_a = DocumentRepository(session, company_id).get(res_a.document_id)
            assert doc_a is not None and doc_a.status == "indexed"
            chunks_b = list(
                session.scalars(
                    select(Chunk).where(Chunk.document_id == res_b.document_id)
                )
            )
            assert len(chunks_b) == 0
    finally:
        with admin_session(admin_factory) as session:
            comp = CompanyRepository(session, company_id).get()
            if comp is not None:
                session.delete(comp)
        admin_engine.dispose()
        runtime_engine.dispose()


def test_db_cli_smoke_uses_fake_embedder(tmp_path: Path) -> None:
    """CLI ingest command executes successfully in subprocess with fake embedder."""
    _load_ingestion_modules()
    company_id = uuid4()
    company_name = f"p6_test_{company_id.hex[:12]}"

    admin_engine = _make_admin_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    try:
        with admin_session(admin_factory) as session:
            CompanyRepository(session, company_id).create(name=company_name)

        sample = tmp_path / "cli_sample.txt"
        sample.write_text("CLI test document body.", encoding="utf-8")

        env = {
            **os.environ,
            "EMBEDDER_BACKEND": "fake",
            "STORAGE_PATH": str(tmp_path / "storage"),
            "RUNTIME_DATABASE_URL": get_settings().require_runtime_database_url(),
        }

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.cli",
                "ingest",
                "--company",
                company_name,
                str(sample),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0
    finally:
        with admin_session(admin_factory) as session:
            comp = CompanyRepository(session, company_id).get()
            if comp is not None:
                session.delete(comp)
        admin_engine.dispose()


def test_db_direct_runtime_connection_isolation() -> None:
    """Prove direct runtime connection connects as rag_app without superuser or bypassrls privileges."""
    admin_engine = _make_admin_engine()
    runtime_engine = _make_runtime_engine()
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    company_a = uuid4()
    company_b = uuid4()
    name_a = f"p6_test_{company_a.hex[:12]}"
    name_b = f"p6_test_{company_b.hex[:12]}"

    try:
        # Admin setup
        with admin_session(admin_factory) as s:
            CompanyRepository(s, company_a).create(name=name_a)
            CompanyRepository(s, company_b).create(name=name_b)

        # 1. Direct runtime engine connects as rag_app without bypassrls or superuser
        with runtime_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        session_user,
                        current_user,
                        (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS rolbypassrls,
                        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS rolsuper
                    """
                )
            ).one()
            assert row.session_user == "rag_app"
            assert row.current_user == "rag_app"
            assert bool(row.rolbypassrls) is False
            assert bool(row.rolsuper) is False

            # Direct insert into companies by rag_app is denied
            with pytest.raises((DBAPIError, SQLAlchemyError)):
                conn.execute(
                    text("INSERT INTO companies (id, name) VALUES (:id, :name)"),
                    {"id": uuid4(), "name": "p6_test_forbidden"},
                )

        # 2. With no tenant context set, SELECT returns 0 rows
        with runtime_engine.connect() as conn:
            docs = conn.execute(text("SELECT id FROM documents")).all()
            chunks = conn.execute(text("SELECT id FROM chunks")).all()
            assert len(docs) == 0
            assert len(chunks) == 0

        # 3. Under tenant A context, inserting tenant B's document is rejected by RLS
        with tenant_session(runtime_factory, company_a) as session_a:
            with pytest.raises((DBAPIError, SQLAlchemyError)):
                session_a.execute(
                    text(
                        """
                        INSERT INTO documents (id, company_id, filename, file_type, file_path, sha256, status)
                        VALUES (:id, :comp_id, 'leak.txt', 'txt', 'path/leak.txt', 'sha_leak_123', 'uploaded')
                        """
                    ),
                    {"id": uuid4(), "comp_id": company_b},
                )
    finally:
        with admin_session(admin_factory) as s:
            for c_id in (company_a, company_b):
                comp = CompanyRepository(s, c_id).get()
                if comp is not None:
                    s.delete(comp)
        admin_engine.dispose()
        runtime_engine.dispose()
