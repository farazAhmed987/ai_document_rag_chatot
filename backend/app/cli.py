"""Document ingestion and administration command-line interface.

SRD Sections 7, 8.1, 14.
Provides operator commands for company management, ingestion, listing,
inspecting, reprocessing, and safely deleting documents.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.embedders.factory import create_embedder
from app.adapters.extractors.factory import extract_document
from app.adapters.storage.local import LocalStorage
from app.adapters.tokenizers.bge_m3 import BgeM3TokenCounter
from app.core import logging as core_logging
from app.core.config import Settings, get_settings
from app.core.errors import DatabaseConfigurationError
from app.core.ingest_errors import sanitize_ingestion_exception
from app.repositories.base import admin_session, tenant_session
from app.repositories.company import CompanyRepository
from app.repositories.document import DocumentRepository
from app.services.ingestion import IngestionService

logger = structlog.get_logger(__name__)

_time_func = time.perf_counter


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser with all Phase 6 subcommands."""
    parser = argparse.ArgumentParser(
        prog="app.cli",
        description="Multi-tenant document ingestion and administration CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # 1. create-company
    p_comp = subparsers.add_parser(
        "create-company", help="Create a new tenant company via admin session"
    )
    p_comp.add_argument("--name", required=True, help="Unique company name")

    # 2. ingest
    p_ingest = subparsers.add_parser(
        "ingest", help="Ingest one or more documents for a tenant"
    )
    p_ingest.add_argument("--company", required=True, help="Tenant company name")
    p_ingest.add_argument(
        "paths",
        nargs="+",
        help="One or more document file paths (.pdf, .docx, .txt, .md)",
    )

    # 3. list
    p_list = subparsers.add_parser(
        "list", help="List all documents owned by a tenant company"
    )
    p_list.add_argument("--company", required=True, help="Tenant company name")

    # 4. info
    p_info = subparsers.add_parser(
        "info", help="Display details and status for a document"
    )
    p_info.add_argument("--company", required=True, help="Tenant company name")
    p_info.add_argument("--document", required=True, help="Document UUID")

    # 5. reprocess
    p_reproc = subparsers.add_parser(
        "reprocess", help="Reprocess an indexed or failed document idempotently"
    )
    p_reproc.add_argument("--company", required=True, help="Tenant company name")
    p_reproc.add_argument("--document", required=True, help="Document UUID")

    # 6. delete
    p_del = subparsers.add_parser(
        "delete", help="Safely delete a document from database and storage"
    )
    p_del.add_argument("--company", required=True, help="Tenant company name")
    p_del.add_argument("--document", required=True, help="Document UUID")
    p_del.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Explicit confirmation required to execute deletion",
    )

    return parser


def get_session_factory(
    settings: Settings | None = None,
    *,
    use_runtime: bool = False,
) -> sessionmaker[Session]:
    """Create a SQLAlchemy sessionmaker bound to the configured database engine."""
    cfg = settings or get_settings()
    try:
        db_url = (
            cfg.require_runtime_database_url()
            if use_runtime
            else cfg.require_database_url()
        )
    except ValueError as err:
        raise DatabaseConfigurationError(
            "Database URL configuration is missing or invalid",
            code="DATABASE_CONFIG_ERROR",
        ) from err

    if db_url.startswith("sqlite") or ":memory:" in db_url:
        raise DatabaseConfigurationError(
            "SQLite or in-memory databases are strictly forbidden",
            code="DATABASE_CONFIG_ERROR",
        )

    engine = create_engine(
        db_url,
        pool_pre_ping=True,
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_ingestion_service(settings: Settings | None = None) -> Any:
    """Build an IngestionService instance using configured production adapters."""
    cfg = settings or get_settings()
    admin_session_fac = get_session_factory(cfg, use_runtime=False)
    runtime_session_fac = get_session_factory(cfg, use_runtime=True)
    storage = LocalStorage(cfg.STORAGE_PATH)
    embedder = create_embedder(settings=cfg)
    token_counter = BgeM3TokenCounter()
    return IngestionService(
        session_factory=runtime_session_fac,
        admin_session_factory=admin_session_fac,
        storage=storage,
        embedder=embedder,
        extractor_factory=extract_document,
        token_counter=token_counter,
        settings=cfg,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint dispatching commands and returning POSIX exit codes."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    settings = get_settings()
    core_logging.configure_logging(level=settings.LOG_LEVEL)

    try:
        if args.command == "create-company":
            session_fac = get_session_factory(settings)
            with admin_session(session_fac) as session:
                comp_repo = CompanyRepository(session)
                existing = (
                    comp_repo.find_by_name(args.name)
                    if hasattr(comp_repo, "find_by_name")
                    else comp_repo.get_by_name(args.name)
                )
                if existing is not None:
                    print(
                        f"Company '{args.name}' already exists with ID: {existing.id}"
                    )
                    return 0
                created = comp_repo.create(name=args.name)
                print(f"Created company '{args.name}' with ID: {created.id}")
                return 0

        if args.command == "delete":
            if not args.yes:
                print(
                    "Error: Deletion requires --yes confirmation flag",
                    file=sys.stderr,
                )
                return 1
            service = get_ingestion_service(settings)
            company_id = service.resolve_company_name(args.company)
            doc_uuid = UUID(args.document)
            del_result = service.delete_document(
                company_id=company_id, document_id=doc_uuid
            )
            if not del_result.deleted:
                print(f"Document {doc_uuid} not found", file=sys.stderr)
                return 1
            if del_result.warning:
                print(f"Deleted document {doc_uuid} with warning: {del_result.warning}")
            else:
                print(f"Deleted document {doc_uuid}")
            return 0

        if args.command == "ingest":
            service = get_ingestion_service(settings)
            company_id = service.resolve_company_name(args.company)
            had_failure = False
            for path_str in args.paths:
                file_path = Path(path_str)
                t0 = _time_func()
                result = service.ingest_file(company_id=company_id, file_path=file_path)
                elapsed = _time_func() - t0
                status_str = getattr(result, "status", "unknown")
                chunk_cnt = getattr(result, "chunk_count", 0)
                if status_str == "failed":
                    had_failure = True
                    msg = getattr(result, "message", "unknown")
                    print(
                        f"status={status_str} chunks={chunk_cnt} seconds={elapsed:.2f} - Failed: {file_path.name} - {msg}",
                        file=sys.stderr,
                    )
                elif getattr(result, "is_duplicate", False):
                    warn = getattr(result, "warning", None)
                    msg = f"status={status_str} chunks={chunk_cnt} seconds={elapsed:.2f} - Skipped duplicate: {file_path.name} (id={getattr(result, 'document_id', None)})"
                    if warn:
                        msg += f" - {warn}"
                    print(msg)
                else:
                    print(
                        f"status={status_str} chunks={chunk_cnt} seconds={elapsed:.2f} - Indexed: {file_path.name} (id={getattr(result, 'document_id', None)})"
                    )
            return 1 if had_failure else 0

        if args.command == "list":
            service = get_ingestion_service(settings)
            company_id = service.resolve_company_name(args.company)
            with tenant_session(service.session_factory, company_id) as session:
                doc_repo = DocumentRepository(session, company_id)
                docs = doc_repo.list() if hasattr(doc_repo, "list") else []
                print(
                    f"Documents for company '{args.company}' ({company_id}): {len(docs)}"
                )
                for doc in docs:
                    print(
                        f"  {doc.id} | {doc.status:<10} | chunks: {doc.chunk_count or 0:<4} | {doc.filename}"
                    )
            return 0

        if args.command == "info":
            service = get_ingestion_service(settings)
            company_id = service.resolve_company_name(args.company)
            doc_uuid = UUID(args.document)
            with tenant_session(service.session_factory, company_id) as session:
                doc_repo = DocumentRepository(session, company_id)
                found_doc = doc_repo.get(doc_uuid)
                if found_doc is None:
                    print(
                        f"Document {doc_uuid} not found for company '{args.company}'",
                        file=sys.stderr,
                    )
                    return 1
                print(f"Document ID: {found_doc.id}")
                print(f"Filename:    {found_doc.filename}")
                print(f"Status:      {found_doc.status}")
                print(f"SHA-256:     {found_doc.sha256}")
                print(f"Chunks:      {found_doc.chunk_count}")
                print(f"Pages:       {found_doc.page_count}")
                if found_doc.error_message:
                    print(f"Error:       {found_doc.error_message}")
            return 0

        if args.command == "reprocess":
            service = get_ingestion_service(settings)
            company_id = service.resolve_company_name(args.company)
            doc_uuid = UUID(args.document)
            result = service.reprocess(company_id=company_id, document_id=doc_uuid)
            if getattr(result, "status", None) == "failed":
                print(
                    f"Reprocess failed: {getattr(result, 'message', 'unknown')}",
                    file=sys.stderr,
                )
                return 1
            print(
                f"Reprocessed: {doc_uuid} (status={getattr(result, 'status', None)}, chunks={getattr(result, 'chunk_count', 0)})"
            )
            return 0

    except Exception as exc:
        cmd_name = getattr(args, "command", "cli") or "cli"
        safe_msg = sanitize_ingestion_exception(exc, stage=cmd_name)
        logger.error(
            "cli_command_failed", command=cmd_name, error_type=type(exc).__name__
        )
        print(f"Error executing {cmd_name}: {safe_msg}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
