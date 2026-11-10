"""Unit tests for ingestion command-line interface arguments, exit codes, and logging (SRD 8.1, 14)."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest


def _load_module() -> ModuleType:
    """Load future CLI module inside test body."""
    return importlib.import_module("app.cli")


def test_cli_parser_supports_all_phase6_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI parser must support create-company, ingest, list, info, reprocess, and delete."""
    cli = _load_module()
    parser = cli.build_parser()

    # create-company
    args = parser.parse_args(["create-company", "--name", "Acme Corp"])
    assert args.command == "create-company"
    assert args.name == "Acme Corp"

    # ingest
    args = parser.parse_args(
        ["ingest", "--company", "Acme Corp", "doc1.pdf", "doc2.docx"]
    )
    assert args.command == "ingest"
    assert args.company == "Acme Corp"
    assert args.paths == ["doc1.pdf", "doc2.docx"]

    # list
    args = parser.parse_args(["list", "--company", "Acme Corp"])
    assert args.command == "list"
    assert args.company == "Acme Corp"

    # info
    doc_id = str(uuid4())
    args = parser.parse_args(["info", "--company", "Acme Corp", "--document", doc_id])
    assert args.command == "info"
    assert args.document == doc_id

    # reprocess
    args = parser.parse_args(
        ["reprocess", "--company", "Acme Corp", "--document", doc_id]
    )
    assert args.command == "reprocess"
    assert args.document == doc_id

    # delete
    args = parser.parse_args(
        ["delete", "--company", "Acme Corp", "--document", doc_id, "--yes"]
    )
    assert args.command == "delete"
    assert args.yes is True

    # Main without command
    assert cli.main([]) == 1

    from types import SimpleNamespace

    class _FullFakeService:
        def __init__(self) -> None:
            self.session_factory = object()

        def resolve_company_name(self, name: str) -> UUID:
            del name
            return uuid4()

        def reprocess(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            return SimpleNamespace(status="indexed", chunk_count=2)

    class _MockAdminSession:
        def __enter__(self) -> _MockAdminSession:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    class _MockCompanyRepo:
        def __init__(self, session: object) -> None:
            pass

        def find_by_name(self, name: str) -> object:
            if name == "Exists":
                return SimpleNamespace(id=uuid4(), name=name)
            return None

        def create(self, name: str) -> object:
            return SimpleNamespace(id=uuid4(), name=name)

    class _MockDocRepo:
        def __init__(self, session: object, company_id: UUID) -> None:
            pass

        def list(self) -> list[object]:
            return [
                SimpleNamespace(
                    id=uuid4(),
                    status="indexed",
                    chunk_count=2,
                    filename="doc.pdf",
                )
            ]

        def get(self, doc_id: UUID) -> object:
            return SimpleNamespace(
                id=doc_id,
                status="indexed",
                chunk_count=2,
                filename="doc.pdf",
                sha256="abc",
                page_count=1,
                error_message="sample",
            )

    monkeypatch.setattr(
        cli, "get_ingestion_service", lambda *a, **k: _FullFakeService()
    )
    monkeypatch.setattr(cli, "admin_session", lambda f: _MockAdminSession())
    monkeypatch.setattr(cli, "tenant_session", lambda f, c: _MockAdminSession())
    monkeypatch.setattr(cli, "CompanyRepository", _MockCompanyRepo)
    monkeypatch.setattr(cli, "DocumentRepository", _MockDocRepo)
    orig_get_session_factory = cli.get_session_factory
    monkeypatch.setattr(cli, "get_session_factory", lambda *a, **k: object())

    assert cli.main(["create-company", "--name", "NewCo"]) == 0
    assert cli.main(["create-company", "--name", "Exists"]) == 0
    assert cli.main(["list", "--company", "NewCo"]) == 0
    assert cli.main(["info", "--company", "NewCo", "--document", doc_id]) == 0
    assert cli.main(["reprocess", "--company", "NewCo", "--document", doc_id]) == 0

    from app.core.config import Settings
    from app.core.errors import DatabaseConfigurationError

    dummy_settings = Settings(_env_file=None, DATABASE_URL="sqlite:///:memory:")
    with pytest.raises(DatabaseConfigurationError):
        orig_get_session_factory(dummy_settings)

    class _FailingRepo:
        def __init__(self, session: object, company_id: UUID) -> None:
            pass

        def get(self, d_id: UUID) -> object:
            del d_id
            return None

    class _FailingReprocessService(_FullFakeService):
        def reprocess(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            return SimpleNamespace(status="failed", message="reprocess error")

        def delete_document(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            return SimpleNamespace(deleted=False)

    monkeypatch.setattr(cli, "DocumentRepository", _FailingRepo)
    assert cli.main(["info", "--company", "NewCo", "--document", doc_id]) == 1

    monkeypatch.setattr(
        cli, "get_ingestion_service", lambda *a, **k: _FailingReprocessService()
    )
    assert cli.main(["reprocess", "--company", "NewCo", "--document", doc_id]) == 1
    assert (
        cli.main(["delete", "--company", "NewCo", "--document", doc_id, "--yes"]) == 1
    )


def test_cli_delete_requires_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI delete command without --yes must return nonzero exit code and not delete."""
    cli = _load_module()
    doc_id = str(uuid4())

    deleted_called = []

    class _FakeService:
        def resolve_company_name(self, name: str) -> UUID:
            del name
            return uuid4()

        def delete_document(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            deleted_called.append(True)
            from types import SimpleNamespace

            return SimpleNamespace(deleted=True, warning="Sample warning")

    monkeypatch.setattr(
        cli,
        "get_ingestion_service",
        lambda *args, **kwargs: _FakeService(),
        raising=False,
    )

    exit_code = cli.main(["delete", "--company", "Acme Corp", "--document", doc_id])
    assert exit_code != 0
    assert not deleted_called

    exit_code_ok = cli.main(
        ["delete", "--company", "Acme Corp", "--document", doc_id, "--yes"]
    )
    assert exit_code_ok == 0
    assert deleted_called


def test_cli_duplicate_is_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a duplicate document is encountered, CLI must exit with code 0."""
    cli = _load_module()
    sample = tmp_path / "test.txt"
    sample.write_text("Hello", encoding="utf-8")

    from types import SimpleNamespace

    class _FakeService:
        def resolve_company_name(self, name: str) -> UUID:
            del name
            return uuid4()

        def ingest_file(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            return SimpleNamespace(
                status="indexed", is_duplicate=True, document_id=uuid4()
            )

    monkeypatch.setattr(
        cli,
        "get_ingestion_service",
        lambda *args, **kwargs: _FakeService(),
        raising=False,
    )

    exit_code = cli.main(["ingest", "--company", "Acme", str(sample)])
    assert exit_code == 0


def test_cli_failure_is_nonzero_and_configures_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When an error occurs, CLI returns nonzero and ensures configure_logging was invoked."""
    cli = _load_module()
    sample = tmp_path / "test.txt"
    sample.write_text("Hello", encoding="utf-8")

    logging_calls: list[bool] = []
    monkeypatch.setattr(
        "app.core.logging.configure_logging",
        lambda *args, **kwargs: logging_calls.append(True),
        raising=False,
    )

    class _FailingService:
        def resolve_company_name(self, name: str) -> UUID:
            del name
            return uuid4()

        def ingest_file(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise RuntimeError("Ingestion failed")

    monkeypatch.setattr(
        cli,
        "get_ingestion_service",
        lambda *args, **kwargs: _FailingService(),
        raising=False,
    )

    exit_code = cli.main(["ingest", "--company", "Acme", str(sample)])
    assert exit_code != 0
    assert True in logging_calls


def test_cli_timing_reports_status_chunks_and_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI output must report status, chunks and elapsed seconds using monotonic clock."""
    cli = _load_module()
    sample = tmp_path / "timed.txt"
    sample.write_text("Timed document text", encoding="utf-8")

    from types import SimpleNamespace

    class _TimingService:
        def resolve_company_name(self, name: str) -> UUID:
            del name
            return uuid4()

        def ingest_file(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            return SimpleNamespace(
                status="indexed", is_duplicate=False, document_id=uuid4(), chunk_count=5
            )

    # Injected fake clock returning fixed intervals (100.0, 102.5 -> 2.50s)
    clock_values = [100.0, 102.5, 105.0]
    monkeypatch.setattr(
        cli,
        "_time_func",
        lambda: clock_values.pop(0) if clock_values else 110.0,
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "get_ingestion_service",
        lambda *args, **kwargs: _TimingService(),
        raising=False,
    )

    exit_code = cli.main(["ingest", "--company", "Acme", str(sample)])
    assert exit_code == 0
    captured = capsys.readouterr()
    stdout_lower = captured.out.lower()
    assert "status=" in stdout_lower or "indexed" in stdout_lower
    assert "chunks=" in stdout_lower or "5" in stdout_lower
    assert "seconds=" in stdout_lower or "2.5" in captured.out


def test_cli_create_company_never_initializes_ml_or_ingestion_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create-company must construct only admin DB session and CompanyRepository, never ML/IngestionService."""
    cli = _load_module()

    forbidden_calls: list[str] = []

    def _forbidden(name: str):
        def _fn(*args: object, **kwargs: object):
            forbidden_calls.append(name)
            raise AssertionError(f"create-company must never call {name}")

        return _fn

    monkeypatch.setattr(
        cli, "create_embedder", _forbidden("create_embedder"), raising=False
    )
    monkeypatch.setattr(
        cli, "BgeM3TokenCounter", _forbidden("BgeM3TokenCounter"), raising=False
    )
    monkeypatch.setattr(
        cli, "extract_document", _forbidden("extract_document"), raising=False
    )
    monkeypatch.setattr(
        cli, "IngestionService", _forbidden("IngestionService"), raising=False
    )

    from types import SimpleNamespace

    class _MockAdminSession:
        def __enter__(self) -> _MockAdminSession:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    class _MockCompanyRepo:
        def __init__(self, session: object) -> None:
            pass

        def find_by_name(self, name: str) -> object | None:
            return None

        def get_by_name(self, name: str) -> object | None:
            return None

        def create(self, name: str) -> object:
            return SimpleNamespace(id=uuid4(), name=name)

    monkeypatch.setattr(cli, "admin_session", lambda f: _MockAdminSession())
    monkeypatch.setattr(cli, "CompanyRepository", _MockCompanyRepo)
    monkeypatch.setattr(cli, "get_session_factory", lambda settings=None: object())

    exit_code = cli.main(["create-company", "--name", "ZeroMLCompany"])
    assert exit_code == 0
    assert not forbidden_calls, f"ML or IngestionService initialized: {forbidden_calls}"


def test_cli_sentinels_never_leak_in_stdout_stderr_or_logs_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI failure output must not leak raw exception text, sentinel filenames or document content."""
    cli = _load_module()

    sentinel_body = "SENTINEL_SECRET_CLI_BODY_112233"
    sentinel_name = "SENTINEL_CONFIDENTIAL_FILENAME_4455"
    sentinel_heading = "SENTINEL_HEADING_TITLE_7788"
    sentinel_exc = "SENTINEL_RAW_EXC_MESSAGE_9900"

    sample = tmp_path / f"{sentinel_name}.txt"
    sample.write_text(
        f"# {sentinel_heading}\nSecret body {sentinel_body}", encoding="utf-8"
    )

    class _LeakingService:
        def resolve_company_name(self, name: str) -> UUID:
            del name
            return uuid4()

        def ingest_file(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise RuntimeError(
                f"Database leak with {sentinel_body} for {sentinel_name} in {sentinel_heading}: {sentinel_exc}"
            )

    monkeypatch.setattr(
        cli,
        "get_ingestion_service",
        lambda *args, **kwargs: _LeakingService(),
        raising=False,
    )

    exit_code = cli.main(["ingest", "--company", "Acme", str(sample)])
    assert exit_code != 0

    captured = capsys.readouterr()
    combined_output = captured.out + captured.err
    assert sentinel_body not in combined_output, (
        "Document body sentinel leaked in CLI output"
    )
    assert sentinel_name not in combined_output, (
        "Filename sentinel leaked in CLI output"
    )
    assert sentinel_heading not in combined_output, (
        "Heading sentinel leaked in CLI output"
    )
    assert sentinel_exc not in combined_output, (
        "Raw exception message sentinel leaked in CLI output"
    )
    assert "Database leak with" not in combined_output, (
        "Raw exception message leaked in CLI output"
    )


def test_cli_missing_runtime_url_fails_closed_without_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When RUNTIME_DATABASE_URL is unconfigured, CLI must fail closed without constructing SQLite."""
    cli = _load_module()
    sample = tmp_path / "valid.txt"
    sample.write_text("Valid text body", encoding="utf-8")

    from pydantic import SecretStr

    from app.core.config import Settings

    # Settings without RUNTIME_DATABASE_URL
    no_runtime_settings = Settings(
        _env_file=None,
        DATABASE_URL=SecretStr("postgresql+psycopg://user:pass@localhost:5432/test_db"),
        RUNTIME_DATABASE_URL=None,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: no_runtime_settings)

    # Rejection: create_engine must never receive a SQLite URL
    orig_create_engine = cli.create_engine

    def _guarded_create_engine(url: str, *args: object, **kwargs: object):
        if "sqlite" in str(url) or ":memory:" in str(url):
            raise AssertionError(f"SQLite engine construction forbidden: {url}")
        return orig_create_engine(url, *args, **kwargs)

    monkeypatch.setattr(cli, "create_engine", _guarded_create_engine)

    exit_code = cli.main(["ingest", "--company", "Acme", str(sample)])
    assert exit_code != 0

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "DATABASE_CONFIG_ERROR" in combined
    assert "sqlite" not in combined.lower()
    assert ":memory:" not in combined.lower()
    assert "pass@" not in combined
    assert "localhost" not in combined


def test_cli_status_persist_failure_is_nonzero_and_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When status persistence fails, CLI exits nonzero, reports safe error code, and logs safely."""
    from typing import Any

    from app.core.errors import StatusPersistFailedError

    cli = _load_module()
    sample = tmp_path / "doc.txt"
    sample.write_text("Valid text content", encoding="utf-8")

    raw_sentinel = "RAW_STATUS_PERSIST_SENTINEL_556677"

    class _PersistFailingService:
        def resolve_company_name(self, name: str) -> UUID:
            del name
            return uuid4()

        def ingest_file(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise StatusPersistFailedError(
                f"Failed to record failure status in DB: {raw_sentinel}",
                code="INGESTION_STATUS_PERSIST_FAILED",
            )

    monkeypatch.setattr(
        cli,
        "get_ingestion_service",
        lambda *args, **kwargs: _PersistFailingService(),
        raising=False,
    )

    logged_events: list[dict[str, Any]] = []

    class _CaptureLogger:
        def error(self, event: str, **kwargs: Any) -> None:
            logged_events.append({"event": event, **kwargs})

        def info(self, event: str, **kwargs: Any) -> None:
            logged_events.append({"event": event, **kwargs})

        def warning(self, event: str, **kwargs: Any) -> None:
            logged_events.append({"event": event, **kwargs})

    monkeypatch.setattr(cli, "logger", _CaptureLogger(), raising=False)

    exit_code = cli.main(["ingest", "--company", "Acme", str(sample)])
    assert exit_code != 0

    captured = capsys.readouterr()
    combined_output = captured.out + captured.err
    assert "[INGESTION_STATUS_PERSIST_FAILED]" in captured.err
    assert raw_sentinel not in combined_output

    assert len(logged_events) >= 1
    for event_data in logged_events:
        for k, v in event_data.items():
            assert raw_sentinel not in str(k)
            assert raw_sentinel not in str(v)

    error_event = logged_events[-1]
    assert error_event["event"] == "cli_command_failed"
    assert error_event["command"] == "ingest"
    assert error_event["error_type"] == "StatusPersistFailedError"


def test_create_company_supplies_generated_uuid_to_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create-company must generate a company UUID and pass it to CompanyRepository constructor."""
    from collections.abc import Iterator
    from contextlib import contextmanager
    from typing import Any

    cli = _load_module()

    fixed_uuid = UUID("11111111-2222-3333-4444-555555555555")
    monkeypatch.setattr(cli, "uuid4", lambda: fixed_uuid, raising=False)

    admin_session_used: list[bool] = []
    repo_instances: list[Any] = []

    class _MockCompany:
        def __init__(self, cid: UUID, name: str) -> None:
            self.id = cid
            self.name = name

    class _InterceptCompanyRepo:
        def __init__(self, session: object, company_id: UUID | None = None) -> None:
            self.session = session
            self.company_id = company_id
            repo_instances.append(self)

        def find_by_name(self, name: str) -> object:
            del name
            return None

        def get_by_name(self, name: str) -> object:
            del name
            return None

        def create(self, *, name: str) -> object:
            if self.company_id is None:
                raise ValueError("company_id is required for company creation")
            return _MockCompany(self.company_id, name)

    monkeypatch.setattr(cli, "CompanyRepository", _InterceptCompanyRepo)

    @contextmanager
    def _mock_admin_session(fac: object) -> Iterator[object]:
        admin_session_used.append(True)
        yield object()

    monkeypatch.setattr(cli, "admin_session", _mock_admin_session)

    # Forbid runtime tenant session, ML, embedder, tokenizer
    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Forbidden component constructed during create-company")

    monkeypatch.setattr(cli, "tenant_session", _forbidden)
    monkeypatch.setattr(cli, "get_ingestion_service", _forbidden)
    monkeypatch.setattr(cli, "create_embedder", _forbidden)

    exit_code = cli.main(["create-company", "--name", "TestCo"])
    assert exit_code == 0
    assert admin_session_used == [True]
    creation_repos = [r for r in repo_instances if r.company_id == fixed_uuid]
    assert len(creation_repos) >= 1, (
        "CompanyRepository was never constructed with the generated UUID"
    )
