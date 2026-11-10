"""Unit tests for IngestionService lifecycle, transactions, and state transitions (DM-3, DM-5, IN-5)."""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest

from app.core.errors import ChunkingBudgetError
from app.schemas.chunking import ChunkDraft
from app.schemas.extraction import ExtractedDocument, ExtractedUnit


def _load_module() -> ModuleType:
    """Load future ingestion service module inside test body."""
    return importlib.import_module("app.services.ingestion")


# ---------------------------------------------------------------------------
# Test-owned doubles for isolated unit testing
# ---------------------------------------------------------------------------


class _TestStorage:
    def __init__(
        self, root: Path = Path("/tmp/storage"), should_raise_on_delete: bool = False
    ) -> None:
        self.root = root
        self.should_raise_on_delete = should_raise_on_delete
        self.actions: list[str] = []

    def store(
        self,
        company_id: UUID,
        document_id: UUID,
        filename: str,
        content: bytes | Path,
    ) -> Path:
        target_dir = self.root / str(company_id) / str(document_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / filename
        data = content if isinstance(content, bytes) else content.read_bytes()
        target_file.write_bytes(data)
        return target_file

    def get_path(self, company_id: UUID, document_id: UUID, filename: str) -> Path:
        return self.root / str(company_id) / str(document_id) / filename

    def delete(
        self,
        company_id: UUID,
        document_id: UUID,
        filename: str | None = None,
    ) -> None:
        self.actions.append("storage_delete")
        if self.should_raise_on_delete:
            raise OSError("Disk failure during file removal")
        doc_dir = self.root / str(company_id) / str(document_id)
        if filename:
            target = doc_dir / filename
            if target.exists():
                target.unlink()
        elif doc_dir.exists():
            import shutil

            shutil.rmtree(doc_dir)


class _TestEmbedder:
    dimension: int = 1024

    def __init__(self, raise_on_batch: int | None = None) -> None:
        self.raise_on_batch = raise_on_batch
        self.batch_count = 0
        self.actions: list[str] = []

    def embed_documents(
        self, texts: list[str], batch_size: int = 16
    ) -> list[list[float]]:
        self.actions.append("embed_documents")
        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            self.batch_count += 1
            if (
                self.raise_on_batch is not None
                and self.batch_count >= self.raise_on_batch
            ):
                raise RuntimeError("Embedder failure during batch processing")
            batch_texts = texts[i : i + batch_size]
            results.extend([[0.1] * self.dimension for _ in batch_texts])
        return results

    def embed_query(self, text: str) -> list[float]:
        del text
        return [0.1] * self.dimension


class _TestDocRecord:
    def __init__(
        self,
        id: UUID,
        company_id: UUID,
        sha256: str,
        status: str = "uploaded",
        chunk_count: int = 0,
    ) -> None:
        self.id = id
        self.company_id = company_id
        self.sha256 = sha256
        self.filename = "test.txt"
        self.file_type = "txt"
        self.file_path = f"{company_id}/{id}/test.txt"
        self.status = status
        self.error_message: str | None = None
        self.warnings: list[str] = []
        self.page_count: int | None = 1
        self.chunk_count = chunk_count


class _TestDocRepo:
    def __init__(
        self,
        company_id: UUID,
        docs: dict[UUID, _TestDocRecord],
        actions: list[str],
    ) -> None:
        self.company_id = company_id
        self.docs = docs
        self.actions = actions

    def create(
        self,
        *,
        document_id: UUID | None = None,
        filename: str,
        file_type: str,
        file_path: str,
        sha256: str,
        size_bytes: int | None = None,
    ) -> _TestDocRecord:
        del size_bytes
        doc = _TestDocRecord(
            id=document_id if document_id is not None else uuid4(),
            company_id=self.company_id,
            sha256=sha256,
            status="uploaded",
        )
        doc.filename = filename
        doc.file_type = file_type
        doc.file_path = file_path
        self.docs[doc.id] = doc
        return doc

    def get(self, document_id: UUID) -> _TestDocRecord | None:
        doc = self.docs.get(document_id)
        return doc if (doc and doc.company_id == self.company_id) else None

    def find_by_sha256(self, sha256: str) -> _TestDocRecord | None:
        for doc in self.docs.values():
            if doc.company_id == self.company_id and doc.sha256 == sha256:
                return doc
        return None

    def update_status(
        self, document_id: UUID, status: str, **kwargs: object
    ) -> _TestDocRecord | None:
        self.actions.append(f"status_{status}_committed")
        doc = self.get(document_id)
        if doc is None:
            return None
        doc.status = status
        if "error_message" in kwargs:
            doc.error_message = kwargs["error_message"]  # type: ignore[assignment]
        if "chunk_count" in kwargs and kwargs["chunk_count"] is not None:
            doc.chunk_count = int(kwargs["chunk_count"])  # type: ignore[arg-type]
        return doc

    def delete(self, document_id: UUID) -> bool:
        self.actions.append("db_delete")
        if document_id in self.docs:
            del self.docs[document_id]
            return True
        return False


class _TestChunkRepo:
    def __init__(
        self,
        company_id: UUID,
        chunks: dict[UUID, list[object]],
        actions: list[str],
        raise_on_replace: bool = False,
    ) -> None:
        self.company_id = company_id
        self.chunks = chunks
        self.actions = actions
        self.raise_on_replace = raise_on_replace

    def replace_for_document(
        self,
        document_id: UUID,
        chunks: Sequence[ChunkDraft],
        embeddings: Sequence[Sequence[float]],
    ) -> list[object]:
        self.actions.append("chunk_transaction")
        if self.raise_on_replace:
            raise RuntimeError("Database error writing chunks")
        self.chunks[document_id] = list(chunks)
        del embeddings
        return list(chunks)

    def count_for_document(self, document_id: UUID) -> int:
        return len(self.chunks.get(document_id, []))


class _TestSession:
    def __init__(self, actions: list[str]) -> None:
        self.actions = actions

    def __enter__(self) -> _TestSession:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if exc_type is not None:
            self.actions.append("chunk_rollback")

    def scalar(self, *args: object, **kwargs: object) -> object:
        return None


class _TestSessionFactory:
    def __init__(self, actions: list[str]) -> None:
        self.actions = actions

    def begin(self) -> _TestSession:
        return _TestSession(self.actions)


def _make_tenant_session(actions: list[str]):
    @contextmanager
    def _tenant_session(factory: object, company_id: UUID) -> Iterator[_TestSession]:
        del factory, company_id
        session = _TestSession(actions)
        try:
            yield session
        except Exception:
            actions.append("chunk_rollback")
            raise

    return _tenant_session


class _TestTokenCounter:
    def count(self, text: str) -> int:
        return len(text.split())


class _TestExtractorFactory:
    def __call__(self, path: Path, **kwargs: object) -> ExtractedDocument:
        del kwargs
        text = path.read_text(encoding="utf-8") if path.is_file() else "Sample text"
        file_type = path.suffix.lstrip(".")
        if file_type not in ("pdf", "docx", "txt", "md"):
            file_type = "txt"
        return ExtractedDocument(
            filename=path.name,
            file_type=file_type,  # type: ignore[arg-type]
            units=[
                ExtractedUnit(
                    kind="section",
                    index=0,
                    page_number=1,
                    heading_path=[],
                    markdown=text,
                )
            ],
            page_count=1,
        )

    def extract_document(self, path: Path, **kwargs: object) -> ExtractedDocument:
        return self(path, **kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_duplicate_returns_existing_document_without_new_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate file for the same tenant must return existing ID with SKIPPED_DUPLICATE."""
    srv = _load_module()
    company_id = uuid4()
    existing_id = uuid4()

    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("Tenant document body.", encoding="utf-8")
    sha = hashlib.sha256(sample_file.read_bytes()).hexdigest()

    docs = {
        existing_id: _TestDocRecord(
            existing_id, company_id, sha, status="indexed", chunk_count=3
        )
    }
    actions: list[str] = []

    storage = _TestStorage(tmp_path / "storage")
    embedder = _TestEmbedder()
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )

    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    result = service.ingest_file(company_id=company_id, file_path=sample_file)
    assert result.is_duplicate is True
    assert result.document_id == existing_id
    assert result.status == "SKIPPED_DUPLICATE"
    assert len(docs) == 1


def test_failed_duplicate_requires_reprocess_instead_of_reingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uploading a duplicate of a failed document must return SKIPPED_DUPLICATE with reprocess guidance."""
    srv = _load_module()
    company_id = uuid4()
    failed_id = uuid4()

    sample_file = tmp_path / "failed.txt"
    sample_file.write_text("Failed document body.", encoding="utf-8")
    sha = hashlib.sha256(sample_file.read_bytes()).hexdigest()

    docs = {
        failed_id: _TestDocRecord(
            failed_id, company_id, sha, status="failed", chunk_count=0
        )
    }
    actions: list[str] = []

    storage = _TestStorage(tmp_path / "storage")
    embedder = _TestEmbedder()
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    result = service.ingest_file(company_id=company_id, file_path=sample_file)
    assert result.is_duplicate is True
    assert result.document_id == failed_id
    assert result.status == "SKIPPED_DUPLICATE"
    assert len(docs) == 1

    warning_or_msg = getattr(result, "warning", None) or getattr(
        result, "message", None
    )
    assert warning_or_msg is not None
    assert "reprocess" in warning_or_msg.lower()


def test_status_machine_accepts_uploaded_processing_indexed() -> None:
    """Status transitions uploaded -> processing -> indexed are valid."""
    srv = _load_module()
    assert srv.validate_status_transition("uploaded", "processing") is True
    assert srv.validate_status_transition("processing", "indexed") is True


def test_status_machine_accepts_indexed_or_failed_to_processing() -> None:
    """Reprocessing transitions indexed -> processing and failed -> processing are valid."""
    srv = _load_module()
    assert srv.validate_status_transition("indexed", "processing") is True
    assert srv.validate_status_transition("failed", "processing") is True


def test_status_machine_rejects_illegal_transition() -> None:
    """Illegal transitions (e.g. uploaded -> indexed, processing -> uploaded) must be rejected."""
    srv = _load_module()
    with pytest.raises(ValueError):
        srv.validate_status_transition("uploaded", "indexed")
    with pytest.raises(ValueError):
        srv.validate_status_transition("processing", "uploaded")
    with pytest.raises(ValueError):
        srv.validate_status_transition("failed", "indexed")


def test_failure_on_second_embedding_batch_rolls_back_all_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Embedding failure on batch 2 must leave zero persisted chunks and status failed."""
    srv = _load_module()
    company_id = uuid4()
    doc_path = tmp_path / "long_doc.txt"
    doc_path.write_text("Paragraph.\n\n" * 30, encoding="utf-8")

    actions: list[str] = []
    docs: dict[UUID, _TestDocRecord] = {}
    chunks: dict[UUID, list[object]] = {}

    storage = _TestStorage(tmp_path / "storage")
    embedder = _TestEmbedder(raise_on_batch=2)
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(
        srv,
        "ChunkRepository",
        lambda session, c_id: _TestChunkRepo(c_id, chunks, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    # Monkeypatch srv.chunk_document to return 20 ChunkDraft instances (> batch_size=16)
    def _many_chunks(*args: object, **kwargs: object) -> list[ChunkDraft]:
        del args, kwargs
        return [
            ChunkDraft(
                chunk_index=i,
                page=1,
                section="Section",
                header="",
                body=f"Chunk body {i}",
                content=f"Chunk body {i}",
                token_count=10,
                is_table=False,
                table_part=None,
                segment_key=(1, "Section", i),
                sub_min_reason=None,
            )
            for i in range(20)
        ]

    monkeypatch.setattr(srv, "chunk_document", _many_chunks, raising=False)

    result = service.ingest_file(company_id=company_id, file_path=doc_path)
    assert result.status == "failed"
    assert embedder.batch_count >= 2
    assert actions.count("chunk_transaction") == 0
    assert len(chunks) == 0
    doc = next(iter(docs.values()))
    assert doc.status == "failed"
    assert doc.chunk_count == 0


def test_failure_does_not_modify_other_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed ingestion of document B must leave existing document A completely intact."""
    srv = _load_module()
    company_id = uuid4()
    doc_a_id = uuid4()

    docs = {
        doc_a_id: _TestDocRecord(
            doc_a_id, company_id, "sha_a", status="indexed", chunk_count=5
        )
    }
    chunks: dict[UUID, list[object]] = {doc_a_id: [object()] * 5}
    actions: list[str] = []

    storage = _TestStorage(tmp_path / "storage")
    embedder = _TestEmbedder(raise_on_batch=1)  # fails immediately for doc B
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(
        srv,
        "ChunkRepository",
        lambda session, c_id: _TestChunkRepo(c_id, chunks, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    doc_b_path = tmp_path / "doc_b.txt"
    doc_b_path.write_text("Doc B content", encoding="utf-8")

    result = service.ingest_file(company_id=company_id, file_path=doc_b_path)
    assert result.status == "failed"
    assert docs[doc_a_id].status == "indexed"
    assert docs[doc_a_id].chunk_count == 5
    assert len(chunks[doc_a_id]) == 5


def test_chunk_budget_error_code_passes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ChunkingBudgetError code CHUNK_BUDGET_EXCEEDED must pass through safely to error_message."""
    srv = _load_module()
    company_id = uuid4()
    doc_path = tmp_path / "budget_doc.txt"
    doc_path.write_text("Budget text.", encoding="utf-8")

    docs: dict[UUID, _TestDocRecord] = {}
    actions: list[str] = []

    storage = _TestStorage(tmp_path / "storage")
    embedder = _TestEmbedder()
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    def _budget_fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ChunkingBudgetError("Heading too large", code="CHUNK_BUDGET_EXCEEDED")

    monkeypatch.setattr(srv, "chunk_document", _budget_fail, raising=False)

    result = service.ingest_file(company_id=company_id, file_path=doc_path)
    assert result.status == "failed"
    doc = next(iter(docs.values()))
    assert doc.error_message is not None
    assert "[CHUNK_BUDGET_EXCEEDED]" in doc.error_message


def test_reprocess_replaces_chunks_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reprocess must replace old chunks with new chunks without duplicates."""
    srv = _load_module()
    company_id = uuid4()
    doc_id = uuid4()

    docs = {
        doc_id: _TestDocRecord(
            doc_id, company_id, "some_sha", status="indexed", chunk_count=3
        )
    }
    chunks: dict[UUID, list[object]] = {doc_id: [object()] * 3}
    actions: list[str] = []

    storage = _TestStorage(tmp_path / "storage")
    sample_file = tmp_path / "test.txt"
    sample_file.write_text("New content with 4 units.", encoding="utf-8")
    storage.store(company_id, doc_id, "test.txt", sample_file)

    embedder = _TestEmbedder()
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(
        srv,
        "ChunkRepository",
        lambda session, c_id: _TestChunkRepo(c_id, chunks, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    result = service.reprocess(company_id=company_id, document_id=doc_id)
    assert result.status == "indexed"
    assert actions.count("chunk_transaction") == 1

    # Missing document raises ValueError
    with pytest.raises(ValueError, match="Document not found"):
        service.reprocess(company_id=company_id, document_id=uuid4())

    # Invalid status raises ValueError
    uploaded_doc_id = uuid4()
    docs[uploaded_doc_id] = _TestDocRecord(
        uploaded_doc_id, company_id, "sha2", status="uploaded"
    )
    with pytest.raises(ValueError, match="Cannot reprocess"):
        service.reprocess(company_id=company_id, document_id=uploaded_doc_id)

    # Missing stored file raises FileNotFoundError
    missing_file_doc_id = uuid4()
    docs[missing_file_doc_id] = _TestDocRecord(
        missing_file_doc_id, company_id, "sha3", status="indexed"
    )
    with pytest.raises(FileNotFoundError):
        service.reprocess(company_id=company_id, document_id=missing_file_doc_id)


def test_delete_removes_database_row_before_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Document delete must remove DB record (cascading chunks) before removing stored file."""
    srv = _load_module()
    company_id = uuid4()
    doc_id = uuid4()

    actions: list[str] = []
    docs = {
        doc_id: _TestDocRecord(
            doc_id, company_id, "sha", status="indexed", chunk_count=2
        )
    }

    storage = _TestStorage(tmp_path / "storage")
    storage.actions = actions
    embedder = _TestEmbedder()
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    result = service.delete_document(company_id=company_id, document_id=doc_id)
    assert result.deleted is True
    assert result.warning is None
    assert actions.index("db_delete") < actions.index("storage_delete")
    assert doc_id not in docs

    # Deleting missing document returns deleted=False
    missing_del = service.delete_document(company_id=company_id, document_id=uuid4())
    assert missing_del.deleted is False


def test_file_delete_failure_returns_warning_without_restoring_database_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Storage deletion failure logs a warning and does not restore the deleted DB record."""
    srv = _load_module()
    company_id = uuid4()
    doc_id = uuid4()

    actions: list[str] = []
    docs = {
        doc_id: _TestDocRecord(
            doc_id, company_id, "sha", status="indexed", chunk_count=2
        )
    }

    storage = _TestStorage(tmp_path / "storage", should_raise_on_delete=True)
    storage.actions = actions
    embedder = _TestEmbedder()
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    result = service.delete_document(company_id=company_id, document_id=doc_id)
    assert result.deleted is True
    assert result.warning is not None and len(result.warning) > 0
    assert doc_id not in docs
    assert actions.index("db_delete") < actions.index("storage_delete")


def test_company_name_resolution_is_read_only_admin_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolving company by name must use read-only admin_session and reject creation."""
    srv = _load_module()
    actions: list[str] = []
    factory = _TestSessionFactory(actions)
    storage = _TestStorage(tmp_path / "storage")
    embedder = _TestEmbedder()
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )

    company_id = uuid4()
    company_name = "Example Realty"

    @contextmanager
    def _fake_admin_session(session_factory: object) -> Iterator[object]:
        del session_factory
        actions.append("admin_session_enter")
        try:
            yield object()
        finally:
            actions.append("admin_session_exit")

    class _FakeCompRepo:
        def __init__(self, session: object, target_id: UUID | None = None) -> None:
            del session, target_id

        def find_by_name(self, name: str) -> object | None:
            actions.append(f"company_lookup_{name}")
            if name == "Missing":
                return None
            from types import SimpleNamespace

            return SimpleNamespace(id=company_id, name=name)

        def get_by_name(self, name: str) -> object | None:
            actions.append(f"company_lookup_{name}")
            if name == "Missing":
                return None
            from types import SimpleNamespace

            return SimpleNamespace(id=company_id, name=name)

    monkeypatch.setattr(srv, "admin_session", _fake_admin_session)
    monkeypatch.setattr(srv, "CompanyRepository", _FakeCompRepo)

    resolved = service.resolve_company_name(company_name)
    assert resolved == company_id
    assert "admin_session_enter" in actions
    assert "admin_session_exit" in actions
    assert f"company_lookup_{company_name}" in actions
    for action in actions:
        assert not any(
            action.startswith(prefix)
            for prefix in ("create", "add", "delete", "update")
        )

    # Missing company raises ValueError
    with pytest.raises(ValueError, match="Company not found"):
        service.resolve_company_name("Missing")


def test_processing_status_is_committed_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Processing status must be committed to DB before extraction begins."""
    srv = _load_module()
    company_id = uuid4()
    doc_path = tmp_path / "order_doc.txt"
    doc_path.write_text("Content.", encoding="utf-8")

    actions: list[str] = []
    docs: dict[UUID, _TestDocRecord] = {}

    storage = _TestStorage(tmp_path / "storage")
    embedder = _TestEmbedder()
    factory = _TestSessionFactory(actions)

    class _TracedExtractorFactory:
        def __call__(self, *args: object, **kwargs: object) -> ExtractedDocument:
            del args, kwargs
            actions.append("start_extraction")
            return ExtractedDocument(
                filename="order_doc.txt",
                file_type="txt",
                units=[ExtractedUnit(kind="text", index=0, markdown="Content.")],
                page_count=1,
            )

        def extract_document(
            self, *args: object, **kwargs: object
        ) -> ExtractedDocument:
            return self(*args, **kwargs)

    extractor_factory = _TracedExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))
    monkeypatch.setattr(srv, "extract_document", extractor_factory, raising=False)

    service.ingest_file(company_id=company_id, file_path=doc_path)
    assert actions.index("status_processing_committed") < actions.index(
        "start_extraction"
    )


def test_embeddings_finish_before_chunk_transaction_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All embedding vectors must be generated before opening the chunk insertion transaction."""
    srv = _load_module()
    company_id = uuid4()
    doc_path = tmp_path / "embed_first_doc.txt"
    doc_path.write_text("Content text.", encoding="utf-8")

    actions: list[str] = []
    docs: dict[UUID, _TestDocRecord] = {}
    chunks: dict[UUID, list[object]] = {}

    storage = _TestStorage(tmp_path / "storage")
    embedder = _TestEmbedder()
    embedder.actions = actions
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(
        srv,
        "ChunkRepository",
        lambda session, c_id: _TestChunkRepo(c_id, chunks, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    service.ingest_file(company_id=company_id, file_path=doc_path)
    assert actions.index("embed_documents") < actions.index("chunk_transaction")


def test_failed_status_is_written_after_chunk_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When chunk write fails, chunk rollback must occur before committing failed status."""
    srv = _load_module()
    company_id = uuid4()
    doc_path = tmp_path / "rollback_doc.txt"
    doc_path.write_text("Payload text.", encoding="utf-8")

    actions: list[str] = []
    docs: dict[UUID, _TestDocRecord] = {}
    chunks: dict[UUID, list[object]] = {}

    storage = _TestStorage(tmp_path / "storage")
    embedder = _TestEmbedder()
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    service = srv.IngestionService(
        session_factory=factory,
        storage=storage,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(
        srv,
        "ChunkRepository",
        lambda session, c_id: _TestChunkRepo(
            c_id, chunks, actions, raise_on_replace=True
        ),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    result = service.ingest_file(company_id=company_id, file_path=doc_path)
    assert result.status == "failed"
    assert actions.index("chunk_rollback") < actions.index("status_failed_committed")


def _make_service(actions: list[str], storage: object | None = None) -> object:
    srv = _load_module()
    st = storage or _TestStorage(Path("/tmp/storage"))
    embedder = _TestEmbedder()
    factory = _TestSessionFactory(actions)
    extractor_factory = _TestExtractorFactory()
    token_counter = _TestTokenCounter()
    return srv.IngestionService(
        session_factory=factory,
        storage=st,
        embedder=embedder,
        extractor_factory=extractor_factory,
        token_counter=token_counter,
    )


def test_status_persist_failure_emits_event_and_raises_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When writing status=failed fails after rollback, service must emit status_persist_failed and raise typed error."""
    srv = _load_module()
    sentinel_body = "SENTINEL_PERSIST_FAIL_BODY_12345"
    sentinel_exc = "SENTINEL_PERSIST_FAIL_EXC_67890"

    doc_path = tmp_path / "persist_fail.txt"
    doc_path.write_text(f"Persist fail test {sentinel_body}", encoding="utf-8")

    company_id = uuid4()
    actions: list[str] = []
    docs: dict[UUID, _TestDocRecord] = {}

    class _FailOnFailedUpdateDocRepo(_TestDocRepo):
        def update_status(self, doc_id: UUID, status: str, **kwargs: object) -> None:
            if status == "failed":
                actions.append("fail_status_persist_error")
                raise RuntimeError(
                    f"Database connection dropped during status=failed persistence: {sentinel_exc}"
                )
            super().update_status(doc_id, status, **kwargs)

    service = _make_service(actions)
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _FailOnFailedUpdateDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(
        srv,
        "ChunkRepository",
        lambda session, c_id: _TestChunkRepo(c_id, {}, actions, raise_on_replace=True),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    logged_events: list[str] = []

    def _mock_error(event: str, **kwargs: object) -> None:
        logged_events.append(event)

    monkeypatch.setattr(srv.logger, "error", _mock_error)

    from app.core.errors import StatusPersistFailedError

    # Must raise exact typed error StatusPersistFailedError with code INGESTION_STATUS_PERSIST_FAILED
    with pytest.raises(StatusPersistFailedError) as exc_info:
        service.ingest_file(company_id=company_id, file_path=doc_path)

    err = exc_info.value
    assert err.code == "INGESTION_STATUS_PERSIST_FAILED"
    assert "status_persist_failed" in logged_events
    assert sentinel_body not in str(err)
    assert sentinel_exc not in str(err)


def test_reprocess_accepts_processing_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reprocess must accept documents with status='processing' (e.g. stuck during crash)."""
    srv = _load_module()
    company_id = uuid4()
    doc_id = uuid4()
    actions: list[str] = []
    docs: dict[UUID, _TestDocRecord] = {}
    chunks: dict[UUID, list[object]] = {}

    # Seed a document stuck in 'processing' status
    stored_path = tmp_path / "stuck.txt"
    stored_path.write_text("Stuck in processing", encoding="utf-8")
    docs[doc_id] = _TestDocRecord(doc_id, company_id, "sha_stuck", status="processing")

    class _ProcessingStorage(_TestStorage):
        def get_path(self, c_id: UUID, d_id: UUID, filename: str) -> Path:
            return stored_path

    service = _make_service(actions)
    service.storage = _ProcessingStorage()
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(
        srv,
        "ChunkRepository",
        lambda session, c_id: _TestChunkRepo(c_id, chunks, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    result = service.reprocess(company_id=company_id, document_id=doc_id)
    assert result.status == "indexed"


def test_document_id_identity_passed_without_type_error_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DocumentRepository.create must strictly require document_id without TypeError fallback."""
    srv = _load_module()
    company_id = uuid4()
    actions: list[str] = []
    docs: dict[UUID, _TestDocRecord] = {}

    created_ids: list[UUID | None] = []

    class _StrictDocRepo(_TestDocRepo):
        def create(
            self, *, document_id: UUID | None = None, **kwargs: object
        ) -> _TestDocRecord:
            created_ids.append(document_id)
            if document_id is None:
                raise TypeError("document_id is strictly required")
            return super().create(document_id=document_id, **kwargs)

    service = _make_service(actions)
    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _StrictDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(
        srv,
        "ChunkRepository",
        lambda session, c_id: _TestChunkRepo(c_id, {}, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    doc_file = tmp_path / "strict_id.txt"
    doc_file.write_text("Strict document identity", encoding="utf-8")

    result = service.ingest_file(company_id=company_id, file_path=doc_file)
    assert result.status == "indexed"
    assert len(created_ids) == 1
    assert created_ids[0] is not None
    assert created_ids[0] == result.document_id


def test_unknown_failure_stores_safe_error_message_without_sentinels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When an unexpected failure occurs after document row creation, safe error message is stored without leaking sentinels."""
    from typing import Any

    import app.core.ingest_errors as ie_mod

    srv = _load_module()
    company_id = uuid4()
    actions: list[str] = []
    docs: dict[UUID, _TestDocRecord] = {}

    sentinel_file = "SENTINEL_CONFIDENTIAL_FILENAME_8877"
    sentinel_body = "SENTINEL_SECRET_DOCUMENT_BODY_6655"
    sentinel_heading = "SENTINEL_SECRET_HEADING_4433"
    sentinel_exc = "SENTINEL_RAW_UNEXPECTED_EXCEPTION_2211"

    doc_file = tmp_path / f"{sentinel_file}.txt"
    doc_file.write_text(f"# {sentinel_heading}\n{sentinel_body}\n", encoding="utf-8")

    class _CrashEmbedder(_TestEmbedder):
        def embed_documents(
            self, texts: list[str], batch_size: int = 16
        ) -> list[list[float]]:
            del texts, batch_size
            raise RuntimeError(f"Embedding raw crash: {sentinel_exc}")

    service = _make_service(actions)
    service.embedder = _CrashEmbedder()

    monkeypatch.setattr(
        srv,
        "DocumentRepository",
        lambda session, c_id: _TestDocRepo(c_id, docs, actions),
    )
    monkeypatch.setattr(
        srv,
        "ChunkRepository",
        lambda session, c_id: _TestChunkRepo(c_id, {}, actions),
    )
    monkeypatch.setattr(srv, "tenant_session", _make_tenant_session(actions))

    logged_entries: list[dict[str, Any]] = []

    def _capture_log(event: str, **kwargs: Any) -> None:
        logged_entries.append({"event": event, **kwargs})

    monkeypatch.setattr(srv.logger, "error", _capture_log)
    monkeypatch.setattr(ie_mod.logger, "error", _capture_log)

    result = service.ingest_file(company_id=company_id, file_path=doc_file)

    assert result.status == "failed"
    assert result.document_id in docs
    stored_doc = docs[result.document_id]
    assert stored_doc.status == "failed"
    assert stored_doc.error_message is not None
    assert "[INGESTION_UNEXPECTED_ERROR]" in stored_doc.error_message

    all_sentinels = (sentinel_file, sentinel_body, sentinel_heading, sentinel_exc)
    for sentinel in all_sentinels:
        assert sentinel not in stored_doc.error_message
        if result.message:
            assert sentinel not in result.message

    assert len(logged_entries) >= 1
    for entry in logged_entries:
        for k, v in entry.items():
            for sentinel in all_sentinels:
                assert sentinel not in str(k)
                assert sentinel not in str(v)
