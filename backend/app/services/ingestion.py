"""Document ingestion service, file validation, and transaction lifecycle.

SRD Sections 7, 8.1, 9, 10; DM-2, DM-3, DM-5, DM-7, IN-1 to IN-6, MT-1, MT-3, MT-5.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import filetype  # type: ignore[import-untyped]
import pymupdf
import structlog

from app.adapters.extractors.factory import extract_document
from app.adapters.extractors.text import _utf16_without_bom, decode_text
from app.adapters.storage.base import Storage
from app.adapters.storage.local import sanitize_filename
from app.core.config import Settings, get_settings
from app.core.errors import (
    CorruptFileError,
    EmptyDocumentError,
    FileTooLargeError,
    StatusPersistFailedError,
    UnsupportedFileTypeError,
)
from app.core.ingest_errors import log_ingestion_error, sanitize_ingestion_exception
from app.repositories.base import admin_session, tenant_session
from app.repositories.chunk import ChunkRepository
from app.repositories.company import CompanyRepository
from app.repositories.document import DocumentRepository
from app.services.chunking import chunk_document
from app.services.cleaning import clean_document

logger = structlog.get_logger(__name__)

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")


# ---------------------------------------------------------------------------
# Immutable Data Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatedFile:
    """Immutable result of pre-ingestion file validation."""

    path: Path
    filename: str
    extension: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class IngestionResult:
    """Immutable result of document ingestion or reprocessing."""

    document_id: UUID
    status: str
    chunk_count: int
    page_count: int
    is_duplicate: bool
    warning: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class DeleteResult:
    """Immutable result of document deletion."""

    deleted: bool
    warning: str | None = None


# ---------------------------------------------------------------------------
# File Validation & Status Machine
# ---------------------------------------------------------------------------


def validate_ingestion_file(
    path: Path,
    *,
    original_filename: str | None = None,
    max_mb: int | None = None,
) -> ValidatedFile:
    """Validate document presence, size, magic bytes, and content integrity.

    Execution order:
    1. Regular file existence
    2. Safe filename and allowed extension
    3. Non-empty
    4. MAX_UPLOAD_MB limit
    5. Real file-type/magic validation
    6. Corruption/decoding validation
    7. SHA-256 computation
    """
    if not path.is_file():
        raise CorruptFileError("Document file does not exist", code="CORRUPT_FILE")

    clean_filename = sanitize_filename(original_filename or path.name)
    ext = Path(clean_filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type: {ext or '(none)'}",
            code="UNSUPPORTED_FILE_TYPE",
        )

    size = path.stat().st_size
    if size == 0:
        raise EmptyDocumentError("Uploaded file is empty", code="EMPTY_FILE")

    limit = max_mb if max_mb is not None else get_settings().MAX_UPLOAD_MB
    if size > limit * 1024 * 1024:
        raise FileTooLargeError(
            f"Upload exceeds {limit} MB limit",
            code="FILE_TOO_LARGE",
        )

    data = path.read_bytes()

    if ext == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise UnsupportedFileTypeError(
                "PDF magic bytes '%PDF-' missing",
                code="UNSUPPORTED_FILE_TYPE",
            )
        guessed = filetype.guess(data[:261])
        if guessed is None or guessed.mime != "application/pdf":
            raise UnsupportedFileTypeError(
                "File content does not match PDF format",
                code="UNSUPPORTED_FILE_TYPE",
            )
        try:
            pdf_doc = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
            page_count = pdf_doc.page_count
            pdf_doc.close()  # type: ignore[no-untyped-call]
        except Exception as exc:
            raise CorruptFileError(
                "Malformed or corrupt PDF document", code="CORRUPT_FILE"
            ) from exc
        if page_count < 1:
            raise EmptyDocumentError("PDF contains no pages", code="EMPTY_FILE")

    elif ext == ".docx":
        if not zipfile.is_zipfile(path):
            raise UnsupportedFileTypeError(
                "File content is not a valid ZIP/DOCX package",
                code="UNSUPPORTED_FILE_TYPE",
            )
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                if (
                    "[Content_Types].xml" not in names
                    or "word/document.xml" not in names
                ):
                    raise UnsupportedFileTypeError(
                        "ZIP package missing essential DOCX parts",
                        code="UNSUPPORTED_FILE_TYPE",
                    )
        except (zipfile.BadZipFile, OSError) as exc:
            raise UnsupportedFileTypeError(
                "Corrupt or invalid DOCX archive",
                code="UNSUPPORTED_FILE_TYPE",
            ) from exc

    elif ext in (".txt", ".md"):
        guessed = filetype.guess(data[:261])
        if guessed is not None:
            raise UnsupportedFileTypeError(
                "Binary content cannot be uploaded as text",
                code="UNSUPPORTED_FILE_TYPE",
            )
        is_utf16 = (
            data.startswith((b"\xff\xfe", b"\xfe\xff"))
            or _utf16_without_bom(data) is not None
        )
        if b"\x00" in data and not is_utf16:
            raise CorruptFileError(
                "Text file contains invalid binary NUL sequences",
                code="CORRUPT_FILE",
            )
        try:
            text_content, _ = decode_text(data)
        except Exception as exc:
            raise CorruptFileError(
                "Text decoding failed", code="DECODE_FAILED"
            ) from exc
        if not text_content.strip():
            raise EmptyDocumentError(
                "Text file contains no printable content", code="EMPTY_FILE"
            )

    sha256 = hashlib.sha256(data).hexdigest()

    return ValidatedFile(
        path=path,
        filename=clean_filename,
        extension=ext,
        size_bytes=size,
        sha256=sha256,
    )


def validate_status_transition(current: str, target: str) -> bool:
    """Enforce strict document status machine transitions."""
    allowed = {
        ("uploaded", "processing"),
        ("processing", "indexed"),
        ("processing", "failed"),
        ("indexed", "processing"),
        ("failed", "processing"),
        ("processing", "processing"),
    }
    if (current, target) in allowed:
        return True
    raise ValueError(f"Illegal status transition from {current} to {target}")


# ---------------------------------------------------------------------------
# Ingestion Service
# ---------------------------------------------------------------------------


class IngestionService:
    """Multi-tenant ingestion orchestrator managing pipeline lifecycle and transactions."""

    def __init__(
        self,
        session_factory: Any,
        storage: Storage,
        embedder: Any,
        extractor_factory: Any,
        token_counter: Any,
        settings: Settings | None = None,
        admin_session_factory: Any | None = None,
    ) -> None:
        """Initialize IngestionService with injected dependencies."""
        self.session_factory = session_factory
        self.storage = storage
        self.embedder = embedder
        self.extractor_factory = extractor_factory
        self.token_counter = token_counter
        self.settings: Settings = settings or get_settings()
        self.admin_session_factory = admin_session_factory or session_factory

    def ingest_file(
        self,
        company_id: UUID,
        file_path: Path,
        *,
        original_filename: str | None = None,
    ) -> IngestionResult:
        """Validate, store, extract, clean, chunk, embed, and index a document file."""
        # 1. Validation before storage or DB mutation
        validated = validate_ingestion_file(
            file_path,
            original_filename=original_filename,
            max_mb=self.settings.MAX_UPLOAD_MB,
        )

        # 2. Tenant-scoped duplicate check
        with tenant_session(self.session_factory, company_id) as session:
            doc_repo = DocumentRepository(session, company_id)
            existing = doc_repo.find_by_sha256(validated.sha256)
            if existing is not None:
                warning = None
                message = None
                if existing.status == "failed":
                    warning = "Existing document previously failed. Please use reprocess to index it."
                    message = warning
                return IngestionResult(
                    document_id=existing.id,
                    status="SKIPPED_DUPLICATE",
                    chunk_count=existing.chunk_count or 0,
                    page_count=existing.page_count or 1,
                    is_duplicate=True,
                    warning=warning,
                    message=message,
                )

        # 3. Generate document UUID and store original
        document_id = uuid4()
        stored_path = self.storage.store(
            company_id, document_id, validated.filename, validated.path
        )

        # 4. Commit document row as uploaded
        try:
            with tenant_session(self.session_factory, company_id) as session:
                doc_repo = DocumentRepository(session, company_id)
                created_doc = doc_repo.create(
                    document_id=document_id,
                    filename=validated.filename,
                    file_type=validated.extension.lstrip("."),
                    file_path=str(stored_path),
                    sha256=validated.sha256,
                    size_bytes=validated.size_bytes,
                )
                if created_doc is not None and hasattr(created_doc, "id"):
                    document_id = created_doc.id
        except Exception:
            # Best effort cleanup of stored file if row creation fails
            try:
                self.storage.delete(company_id, document_id)
            except Exception:
                pass
            raise

        # 5. In a separate short transaction, commit processing status
        with tenant_session(self.session_factory, company_id) as session:
            doc_repo = DocumentRepository(session, company_id)
            doc_repo.update_status(
                document_id,
                "processing",
                error_message=None,
                warnings=[],
            )

        # 6. Process document outside holding transactions
        return self._process_pipeline(
            company_id=company_id,
            document_id=document_id,
            stored_path=stored_path,
            filename=validated.filename,
        )

    def _process_pipeline(
        self,
        company_id: UUID,
        document_id: UUID,
        stored_path: Path,
        filename: str,
    ) -> IngestionResult:
        """Run extraction, cleaning, chunking, embedding and chunk replacement."""
        stage = "preparation"
        try:
            # Resolve company name through tenant-visible company row
            try:
                with tenant_session(self.session_factory, company_id) as session:
                    comp_repo = CompanyRepository(session, company_id)
                    comp = comp_repo.get() if hasattr(comp_repo, "get") else None
                    company_name = (
                        comp.name if comp and hasattr(comp, "name") else str(company_id)
                    )
            except Exception:
                company_name = str(company_id)

            # Extraction outside DB transaction
            stage = "extraction"
            if callable(self.extractor_factory):
                extracted = self.extractor_factory(
                    stored_path,
                    original_filename=filename,
                    max_mb=self.settings.MAX_UPLOAD_MB,
                )
            else:
                extracted = extract_document(
                    stored_path,
                    original_filename=filename,
                    max_mb=self.settings.MAX_UPLOAD_MB,
                )

            # Cleaning outside DB transaction
            stage = "cleaning"
            cleaned = clean_document(extracted)

            # Chunking outside DB transaction
            stage = "chunking"
            chunks = chunk_document(
                cleaned,
                company_name=company_name,
                token_counter=self.token_counter,
                settings=self.settings,
            )

            # Embedding outside DB transaction
            stage = "embedding"
            texts = [c.content for c in chunks]
            batch_size = getattr(self.settings, "EMBEDDING_BATCH_SIZE", 16)
            embeddings = self.embedder.embed_documents(texts, batch_size=batch_size)

            if len(embeddings) != len(chunks):
                raise RuntimeError(
                    f"Generated {len(embeddings)} embeddings for {len(chunks)} chunks"
                )
            for emb in embeddings:
                if len(emb) != 1024:
                    raise RuntimeError(f"Embedding dimension {len(emb)} != 1024")

            # Chunk replacement in one short tenant transaction
            stage = "indexing"
            with tenant_session(self.session_factory, company_id) as session:
                chunk_repo = ChunkRepository(session, company_id)
                chunk_repo.replace_for_document(document_id, chunks, embeddings)
                doc_repo = DocumentRepository(session, company_id)
                doc_repo.update_status(
                    document_id,
                    "indexed",
                    chunk_count=len(chunks),
                    page_count=extracted.page_count,
                    error_message=None,
                    processed_at=datetime.now(UTC),
                )

            return IngestionResult(
                document_id=document_id,
                status="indexed",
                chunk_count=len(chunks),
                page_count=extracted.page_count or 1,
                is_duplicate=False,
            )

        except Exception as exc:
            # Rollback happened automatically; record failure in a new transaction
            safe_err = sanitize_ingestion_exception(exc, stage=stage)
            log_ingestion_error(exc, stage=stage, document_id=document_id)
            try:
                with tenant_session(self.session_factory, company_id) as session:
                    doc_repo = DocumentRepository(session, company_id)
                    doc_repo.update_status(
                        document_id,
                        "failed",
                        error_message=safe_err,
                        chunk_count=0,
                    )
            except Exception as persist_exc:
                logger.error(
                    "status_persist_failed",
                    stage=stage,
                    document_id=str(document_id),
                    error_code="INGESTION_STATUS_PERSIST_FAILED",
                    error_type=type(persist_exc).__name__,
                )
                raise StatusPersistFailedError(
                    f"Failed to record failure status for document {document_id}",
                    code="INGESTION_STATUS_PERSIST_FAILED",
                ) from persist_exc

            return IngestionResult(
                document_id=document_id,
                status="failed",
                chunk_count=0,
                page_count=0,
                is_duplicate=False,
                message=safe_err,
            )

    def reprocess(
        self,
        company_id: UUID,
        document_id: UUID,
    ) -> IngestionResult:
        """Reprocess an existing indexed or failed document idempotently."""
        with tenant_session(self.session_factory, company_id) as session:
            doc_repo = DocumentRepository(session, company_id)
            doc = doc_repo.get(document_id)
            if doc is None:
                raise ValueError("Document not found")
            if doc.status not in ("indexed", "failed", "processing"):
                raise ValueError(f"Cannot reprocess document with status {doc.status}")
            filename = doc.filename
            stored_path = self.storage.get_path(company_id, document_id, filename)
            if not stored_path.is_file():
                raise FileNotFoundError("Stored document file does not exist")

            doc_repo.update_status(
                document_id,
                "processing",
                error_message=None,
                warnings=[],
            )

        return self._process_pipeline(
            company_id=company_id,
            document_id=document_id,
            stored_path=stored_path,
            filename=filename,
        )

    def delete_document(
        self,
        company_id: UUID,
        document_id: UUID,
    ) -> DeleteResult:
        """Delete document from database before removing stored files."""
        with tenant_session(self.session_factory, company_id) as session:
            doc_repo = DocumentRepository(session, company_id)
            doc = doc_repo.get(document_id)
            if doc is None:
                return DeleteResult(deleted=False, warning=None)
            filename = doc.filename
            doc_repo.delete(document_id)

        warning = None
        try:
            self.storage.delete(company_id, document_id, filename)
        except Exception as exc:
            warning = f"Storage deletion failed: {type(exc).__name__}"
            logger.warning(
                "storage_delete_failed",
                company_id=str(company_id),
                document_id=str(document_id),
                error=type(exc).__name__,
            )

        return DeleteResult(deleted=True, warning=warning)

    def resolve_company_name(self, company_name: str) -> UUID:
        """Resolve company name to UUID via read-only admin lookup."""
        with admin_session(self.admin_session_factory) as session:
            comp_repo = CompanyRepository(session)
            comp = (
                comp_repo.find_by_name(company_name)
                if hasattr(comp_repo, "find_by_name")
                else comp_repo.get_by_name(company_name)
            )
            if comp is None:
                raise ValueError(f"Company not found: {company_name}")
            return comp.id
