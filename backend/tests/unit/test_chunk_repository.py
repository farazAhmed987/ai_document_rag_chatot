"""Unit tests for ChunkRepository tenant scoping and ORM column contracts (DM-3, IN-5, MT-1)."""

from __future__ import annotations

import importlib
from types import ModuleType
from uuid import uuid4

import pytest

from app.schemas.chunking import ChunkDraft


def _load_module() -> ModuleType:
    """Load future chunk repository module inside test body."""
    return importlib.import_module("app.repositories.chunk")


def test_chunk_repository_forces_tenant_company_id_and_omits_tsv() -> None:
    """Repository must force company_id from repository scope, omit tsv and transient fields."""
    repo_mod = _load_module()
    tenant_id = uuid4()
    foreign_company = uuid4()
    document_id = uuid4()

    captured_chunks: list[object] = []

    class MockSession:
        def execute(
            self, statement: object, params: dict[str, object] | None = None
        ) -> object:
            del statement, params
            return None

        def add_all(self, items: list[object]) -> None:
            captured_chunks.extend(items)

        def flush(self) -> None:
            pass

    draft = ChunkDraft(
        chunk_index=0,
        page=1,
        section="Heading",
        header="Header content",
        body="Body content",
        content="Header content\n\nBody content",
        token_count=15,
        is_table=False,
        table_part=None,
        segment_key=(1, "Heading", 0),
        sub_min_reason=None,
    )
    vector = [0.1] * 1024

    with pytest.raises(TypeError):
        repo_mod.ChunkRepository(session=MockSession(), company_id="not-a-uuid")  # type: ignore[arg-type]

    repo = repo_mod.ChunkRepository(session=MockSession(), company_id=tenant_id)

    # Calling with non-UUID document_id must raise TypeError
    with pytest.raises(TypeError):
        repo.replace_for_document(
            document_id="not-a-uuid",  # type: ignore[arg-type]
            chunks=[draft],
            embeddings=[vector],
        )

    # Calling with mismatched counts must raise ValueError
    with pytest.raises(ValueError):
        repo.replace_for_document(
            document_id=document_id,
            chunks=[draft],
            embeddings=[],
        )

    # Calling with invalid vector dimension must raise ValueError
    with pytest.raises(ValueError):
        repo.replace_for_document(
            document_id=document_id,
            chunks=[draft],
            embeddings=[[0.1] * 512],
        )

    # 1. Calling replace_for_document with company_id keyword must raise TypeError
    with pytest.raises(TypeError):
        repo.replace_for_document(  # type: ignore[call-arg]
            document_id=document_id,
            chunks=[draft],
            embeddings=[vector],
            company_id=foreign_company,
        )

    # 2. Standard call forces repo.company_id
    inserted = repo.replace_for_document(
        document_id=document_id,
        chunks=[draft],
        embeddings=[vector],
    )

    assert len(inserted) == 1
    chunk = inserted[0]
    assert chunk.company_id == tenant_id
    assert chunk.company_id != foreign_company
    assert chunk.document_id == document_id
    assert chunk.chunk_index == 0
    assert chunk.page == 1
    assert chunk.section == "Heading"
    assert chunk.content == "Header content\n\nBody content"
    assert chunk.token_count == 15
    assert chunk.embedding == vector

    # Must NOT set tsv (generated column) or transient draft fields
    assert "tsv" not in chunk.__dict__
    for transient_field in (
        "header",
        "body",
        "is_table",
        "table_part",
        "segment_key",
        "sub_min_reason",
    ):
        assert transient_field not in chunk.__dict__


def test_chunk_repository_replace_count_and_preview_are_tenant_scoped() -> None:
    """Queries and updates must be strictly bounded to the authorized tenant."""
    repo_mod = _load_module()
    tenant_a = uuid4()
    document_id = uuid4()

    queries_run: list[str] = []

    class MockScopingSession:
        def execute(
            self, statement: object, params: dict[str, object] | None = None
        ) -> object:
            queries_run.append(str(statement))
            return []

        def scalar(
            self, statement: object, params: dict[str, object] | None = None
        ) -> object:
            queries_run.append(str(statement))
            return 0

        def scalars(
            self, statement: object, params: dict[str, object] | None = None
        ) -> object:
            queries_run.append(str(statement))
            return []

    repo = repo_mod.ChunkRepository(session=MockScopingSession(), company_id=tenant_a)
    with pytest.raises(TypeError):
        repo.count_for_document("not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        repo.list_for_document("not-a-uuid")  # type: ignore[arg-type]

    repo.count_for_document(document_id)
    repo.list_for_document(document_id, limit=3)

    for query in queries_run:
        assert "company_id" in query.lower()
