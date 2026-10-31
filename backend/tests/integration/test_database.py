"""Rollback-safe operational database integration and RLS checks.

SRD Sections 7, 9 and 15; requirements DM-5, DM-7, MT-1, MT-3, MT-5.

Every test runs inside a rollback-only outer transaction. These tests
require Phase 2 migrations to have been applied beforehand.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import ChatSession, Chunk, Document, Message
from app.repositories.base import tenant_session
from app.repositories.company import CompanyRepository
from app.repositories.document import DocumentRepository

pytestmark = pytest.mark.db


@pytest.fixture
def db_connection() -> Iterator[Connection]:
    """Provide a database connection whose changes are always rolled back."""
    engine = create_engine(
        get_settings().require_database_url(),
        connect_args={"options": "-c search_path=public,extensions"},
    )
    try:
        with engine.connect() as connection:
            outer = connection.begin()
            try:
                yield connection
            finally:
                outer.rollback()
    finally:
        engine.dispose()


def factory_for(connection: Connection) -> sessionmaker[Session]:
    """Bind sessions to the rollback-only transaction using savepoints."""
    return sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


def create_company(factory: sessionmaker[Session], company_id: UUID) -> None:
    """Insert a unique tenant under a scoped savepoint."""
    with tenant_session(factory, company_id) as session:
        CompanyRepository(session, company_id).create(name=f"Test company {company_id}")


def create_document(
    factory: sessionmaker[Session], company_id: UUID, sha256: str
) -> UUID:
    """Persist a test document and return its UUID."""
    with tenant_session(factory, company_id) as session:
        document = DocumentRepository(session, company_id).create(
            filename="sample.pdf",
            file_type="pdf",
            file_path=f"{company_id}/sample.pdf",
            sha256=sha256,
            size_bytes=128,
        )
        return document.id


def test_unique_sha256_is_company_scoped(db_connection: Connection) -> None:
    """Same tenant cannot upload identical bytes twice; another tenant can."""
    factory = factory_for(db_connection)
    company_a, company_b = uuid4(), uuid4()
    create_company(factory, company_a)
    create_company(factory, company_b)
    digest = "a" * 64
    create_document(factory, company_a, digest)
    create_document(factory, company_b, digest)

    with pytest.raises(IntegrityError):
        create_document(factory, company_a, digest)


def test_status_check_constraint(db_connection: Connection) -> None:
    """Reject invalid document statuses at the database layer."""
    factory = factory_for(db_connection)
    company_id = uuid4()
    create_company(factory, company_id)

    with pytest.raises(IntegrityError):
        with tenant_session(factory, company_id) as session:
            session.add(
                Document(
                    company_id=company_id,
                    filename="invalid.pdf",
                    file_type="pdf",
                    file_path="invalid.pdf",
                    sha256="b" * 64,
                    status="not_a_status",
                )
            )
            session.flush()


def test_delete_document_cascades_to_chunks(db_connection: Connection) -> None:
    """Deleting a source removes its vectors and stored chunk rows."""
    factory = factory_for(db_connection)
    company_id = uuid4()
    create_company(factory, company_id)
    document_id = create_document(factory, company_id, "c" * 64)
    chunk_id = uuid4()

    with tenant_session(factory, company_id) as session:
        session.add(
            Chunk(
                id=chunk_id,
                company_id=company_id,
                document_id=document_id,
                chunk_index=0,
                page=1,
                section="Overview",
                content="Sample searchable content",
                embedding=[1.0] + [0.0] * 1023,
            )
        )
        session.flush()
        assert DocumentRepository(session, company_id).delete(document_id)
        assert (
            session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.id == chunk_id)
            )
            == 0
        )


def test_vector_cosine_query(db_connection: Connection) -> None:
    """Persist a 1024-dimensional vector and query by cosine distance."""
    factory = factory_for(db_connection)
    company_id = uuid4()
    create_company(factory, company_id)
    document_id = create_document(factory, company_id, "d" * 64)
    vector = [1.0] + [0.0] * 1023

    with tenant_session(factory, company_id) as session:
        chunk = Chunk(
            company_id=company_id,
            document_id=document_id,
            chunk_index=0,
            content="Semantic matching example",
            embedding=vector,
        )
        session.add(chunk)
        session.flush()
        statement = (
            select(Chunk.id, Chunk.embedding.cosine_distance(vector).label("distance"))
            .where(Chunk.company_id == company_id)
            .order_by(Chunk.embedding.cosine_distance(vector))
            .limit(1)
        )
        result = session.execute(statement).one()
        assert result.id == chunk.id
        assert abs(float(result.distance)) < 1e-6
        assert session.scalar(select(Chunk.tsv).where(Chunk.id == chunk.id)) is not None


def test_repository_denies_other_company(db_connection: Connection) -> None:
    """Application predicates hide documents of a different company."""
    factory = factory_for(db_connection)
    company_a, company_b = uuid4(), uuid4()
    create_company(factory, company_a)
    create_company(factory, company_b)
    document_b = create_document(factory, company_b, "e" * 64)

    with tenant_session(factory, company_a) as session:
        repository = DocumentRepository(session, company_a)
        assert repository.get(document_b) is None
        assert not repository.delete(document_b)
        assert repository.list() == []
        assert repository.find_by_sha256("e" * 64) is None


def test_rls_hides_other_company_rows(db_connection: Connection) -> None:
    """Verify documents, chunks, sessions and messages under rag_app."""
    factory = factory_for(db_connection)
    company_a, company_b = uuid4(), uuid4()
    create_company(factory, company_a)
    create_company(factory, company_b)
    document_a = create_document(factory, company_a, "f" * 64)
    document_b = create_document(factory, company_b, "0" * 64)

    with tenant_session(factory, company_a) as session:
        session.add(
            Chunk(
                company_id=company_a,
                document_id=document_a,
                chunk_index=0,
                content="A",
                embedding=[1.0] + [0.0] * 1023,
            )
        )
        session_a = ChatSession(company_id=company_a)
        session.add(session_a)
        session.flush()
        session.add(Message(session_id=session_a.id, role="user", content="A"))

    with tenant_session(factory, company_b) as session:
        session.add(
            Chunk(
                company_id=company_b,
                document_id=document_b,
                chunk_index=0,
                content="B",
                embedding=[1.0] + [0.0] * 1023,
            )
        )
        session_b = ChatSession(company_id=company_b)
        session.add(session_b)
        session.flush()
        session.add(Message(session_id=session_b.id, role="user", content="B"))

    role_exists = db_connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_app')")
    )
    if not role_exists:
        pytest.skip("rag_app role unavailable on this host; RLS test not executed")

    # Use a savepoint so an insufficient-privilege error does not poison
    # the outer transaction. The report must state the reason for skipping.
    nested = db_connection.begin_nested()
    try:
        db_connection.exec_driver_sql("SET LOCAL ROLE rag_app")
    except DBAPIError as exc:
        nested.rollback()
        pytest.skip(f"cannot assume rag_app role: {type(exc.orig).__name__}")
    else:
        nested.commit()

    db_connection.execute(
        text("SELECT set_config('app.company_id', :company_id, true)"),
        {"company_id": str(company_a)},
    )

    for table in ("documents", "chunks", "chat_sessions", "messages"):
        count = db_connection.scalar(text(f"SELECT count(*) FROM {table}"))
        assert count == 1, f"RLS did not filter {table}: saw {count} rows"

    visible = db_connection.scalars(text("SELECT company_id FROM documents")).all()
    assert visible == [company_a]
