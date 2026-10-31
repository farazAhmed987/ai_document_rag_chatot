"""Fail-first security and relational hardening regression tests.

SRD Sections 7, 9 and 15; MT-1, MT-3, MT-5, DM-3, IN-5.
Each database test uses savepoints inside a rollback-only outer transaction.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, create_engine, delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.models import ChatSession, Chunk, Company, Document, Message
from app.repositories.base import tenant_session
from app.repositories.document import DocumentRepository

pytestmark = pytest.mark.db


@pytest.fixture
def connection() -> Iterator[Connection]:
    """Provide a real operational connection with rollback-only writes."""
    engine = create_engine(
        get_settings().require_database_url(),
        connect_args={"options": "-c search_path=public,extensions"},
    )
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                yield conn
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def factory_for(connection: Connection) -> sessionmaker[Session]:
    """Bind each Session to its own savepoint within the test transaction."""
    return sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


def make_company(factory: sessionmaker[Session], tenant: UUID) -> None:
    """Seed a company using the migration/admin login in a savepoint."""
    with factory.begin() as session:
        session.add(Company(id=tenant, name=f"Hardening {tenant}"))
        session.flush()


def make_document(factory: sessionmaker[Session], tenant: UUID, marker: str) -> UUID:
    """Seed one company-owned document inside a tenant transaction."""
    with tenant_session(factory, tenant) as session:
        document = DocumentRepository(session, tenant).create(
            filename=f"{marker}.pdf",
            file_type="pdf",
            file_path=f"{tenant}/{marker}.pdf",
            sha256=marker * 64,
        )
        return document.id


def make_chat(factory: sessionmaker[Session], tenant: UUID) -> None:
    """Create one chat and one message owned by tenant."""
    with tenant_session(factory, tenant) as session:
        chat = ChatSession(company_id=tenant)
        session.add(chat)
        session.flush()
        session.add(Message(session_id=chat.id, role="user", content="Hello"))


def make_chunk(factory: sessionmaker[Session], tenant: UUID, document_id: UUID) -> None:
    """Create one ordinary 1024-dimensional chunk."""
    with tenant_session(factory, tenant) as session:
        session.add(
            Chunk(
                company_id=tenant,
                document_id=document_id,
                chunk_index=0,
                content="tenant information",
                embedding=[1.0] + [0.0] * 1023,
            )
        )


def test_tenant_session_assumes_restricted_current_user(connection: Connection) -> None:
    """Verify role switching, not only app.company_id setting."""
    factory = factory_for(connection)
    tenant = uuid4()
    make_company(factory, tenant)
    with tenant_session(factory, tenant) as session:
        assert session.scalar(text("SELECT current_user")) == "rag_app"
    assert connection.scalar(text("SELECT current_user")) != "rag_app"


def test_tenant_only_sees_its_own_five_tables(connection: Connection) -> None:
    """Companies, documents, chunks, chats and messages are isolated."""
    factory = factory_for(connection)
    a, b = uuid4(), uuid4()
    make_company(factory, a)
    make_company(factory, b)
    document_a = make_document(factory, a, "a")
    document_b = make_document(factory, b, "b")
    make_chunk(factory, a, document_a)
    make_chunk(factory, b, document_b)
    make_chat(factory, a)
    make_chat(factory, b)

    with tenant_session(factory, a) as session:
        for table in (Company, Document, Chunk, ChatSession, Message):
            assert session.scalar(select(func.count()).select_from(table)) == 1, (
                table.__name__
            )
        assert session.scalar(select(Company.id)) == a
        assert session.scalar(select(Document.company_id)) == a


def test_tenant_cannot_insert_another_company_document(connection: Connection) -> None:
    """Disallow writes when company_id conflicts with app.company_id."""
    factory = factory_for(connection)
    a, b = uuid4(), uuid4()
    make_company(factory, a)
    make_company(factory, b)
    with pytest.raises(DBAPIError):
        with tenant_session(factory, a) as session:
            session.add(
                Document(
                    company_id=b,
                    filename="other.pdf",
                    file_type="pdf",
                    file_path="other.pdf",
                    sha256="1" * 64,
                    status="uploaded",
                )
            )
            session.flush()


def test_cross_company_update_delete_affect_no_rows(connection: Connection) -> None:
    """Direct SQL writes cannot modify or remove another tenant's rows."""
    factory = factory_for(connection)
    a, b = uuid4(), uuid4()
    make_company(factory, a)
    make_company(factory, b)
    target = make_document(factory, b, "c")
    with tenant_session(factory, a) as session:
        change = session.execute(
            update(Document).where(Document.id == target).values(status="failed")
        )
        assert change.rowcount == 0
        removed = session.execute(delete(Document).where(Document.id == target))
        assert removed.rowcount == 0


def test_runtime_role_cannot_delete_company(connection: Connection) -> None:
    """The runtime role must not have company deletion privileges."""
    factory = factory_for(connection)
    a = uuid4()
    make_company(factory, a)
    with pytest.raises(DBAPIError):
        with tenant_session(factory, a) as session:
            session.execute(delete(Company).where(Company.id == a))


def test_chunk_company_must_match_parent_document(connection: Connection) -> None:
    """Composite FK rejects cross-tenant document/chunk associations."""
    factory = factory_for(connection)
    a, b = uuid4(), uuid4()
    make_company(factory, a)
    make_company(factory, b)
    document_b = make_document(factory, b, "d")
    with pytest.raises(IntegrityError):
        with tenant_session(factory, a) as session:
            session.add(
                Chunk(
                    company_id=a,
                    document_id=document_b,
                    chunk_index=0,
                    content="not allowed",
                    embedding=[1.0] + [0.0] * 1023,
                )
            )
            session.flush()


def test_duplicate_document_chunk_index_rejected(connection: Connection) -> None:
    """Index is unique within each document."""
    factory = factory_for(connection)
    a = uuid4()
    make_company(factory, a)
    document_id = make_document(factory, a, "e")
    with pytest.raises(IntegrityError):
        with tenant_session(factory, a) as session:
            for content in ("one", "two"):
                session.add(
                    Chunk(
                        company_id=a,
                        document_id=document_id,
                        chunk_index=0,
                        content=content,
                        embedding=[1.0] + [0.0] * 1023,
                    )
                )
            session.flush()


def test_admin_session_can_create_company(connection: Connection) -> None:
    """Creation succeeds only under the privileged session helper."""
    from app.repositories.base import admin_session
    from app.repositories.company import CompanyRepository

    factory = factory_for(connection)
    a = uuid4()
    with admin_session(factory) as session:
        created = CompanyRepository(session, a).create(name=f"Admin-created {a}")
        assert created.id == a
    with tenant_session(factory, a) as session:
        assert CompanyRepository(session, a).get() is not None
        with pytest.raises(PermissionError, match="admin_session"):
            CompanyRepository(session, a).create(name="Should not create")


def test_missing_runtime_role_fails_closed(
    connection: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Role errors must raise AppError rather than opening a privileged tenant session."""
    from app.core.errors import TenantRoleError

    factory = factory_for(connection)
    tenant = uuid4()
    make_company(factory, tenant)
    settings = Settings(
        _env_file=None, DB_RUNTIME_ROLE="role_that_does_not_exist_98765"
    )
    monkeypatch.setattr("app.repositories.base.get_settings", lambda: settings)
    with pytest.raises(TenantRoleError, match="Cannot assume"):
        with tenant_session(factory, tenant):
            pytest.fail("A failed role switch must never yield a session")


def test_invalid_role_identifier_fails_closed(
    connection: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsafe role identifiers must be rejected before SQL execution."""
    from app.core.errors import TenantRoleError

    settings = Settings(
        _env_file=None, DB_RUNTIME_ROLE='rag_app"; DROP TABLE companies;--'
    )
    monkeypatch.setattr("app.repositories.base.get_settings", lambda: settings)
    with pytest.raises(TenantRoleError, match="Invalid"):
        with tenant_session(factory_for(connection), uuid4()):
            pytest.fail("Unsafe role identifier must not be executed")


def test_update_status_preserves_error_unless_explicitly_cleared(
    connection: Connection,
) -> None:
    """Error and warning fields are only cleared or replaced explicitly."""
    factory = factory_for(connection)
    tenant = uuid4()
    make_company(factory, tenant)
    doc_id = make_document(factory, tenant, "f")
    with tenant_session(factory, tenant) as session:
        repo = DocumentRepository(session, tenant)
        assert repo.update_status(
            doc_id, "failed", error_message="original error", warnings=["first"]
        )
        document = repo.update_status(doc_id, "processing")
        assert document is not None
        assert document.error_message == "original error"
        assert document.warnings == ["first"]
        cleared = repo.update_status(doc_id, "indexed", error_message=None, warnings=[])
        assert cleared is not None
        assert cleared.error_message is None
        assert cleared.warnings == []
