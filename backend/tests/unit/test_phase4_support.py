"""Offline tokenizer, split-boundary and tenant-guard regression checks."""

from __future__ import annotations

import builtins
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.adapters.tokenizers import bge_m3
from app.core.errors import TenantRoleError
from app.repositories.base import admin_session, is_admin_session, tenant_session
from app.services.chunking import chunk_document
from tests.unit.test_chunking import (
    CharacterCounter,
    WordCounter,
    document,
    settings,
    unit,
    words,
)


@pytest.fixture(autouse=True)
def clear_tokenizer_cache() -> None:
    bge_m3._load_tokenizer.cache_clear()


def test_tokenizer_counter_is_lazy() -> None:
    before = set(sys.modules)
    bge_m3.BgeM3TokenCounter()
    assert not any(
        name == "torch" or name.startswith("torch.")
        for name in set(sys.modules) - before
    )


def test_tokenizer_cached_offline_and_preparation_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeTokenizer:
        def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
            assert add_special_tokens is False
            return list(range(len(text.split())))

    def load(name: str, **kwargs: object) -> FakeTokenizer:
        assert name == "BAAI/bge-m3"
        calls.append(kwargs)
        return FakeTokenizer()

    fake = ModuleType("transformers")
    fake.AutoTokenizer = SimpleNamespace(from_pretrained=load)
    monkeypatch.setitem(sys.modules, "transformers", fake)
    counter = bge_m3.BgeM3TokenCounter()
    assert counter.count("one two") == 2
    assert counter.count("one") == 1
    assert len(calls) == 1 and calls[0]["local_files_only"] is True
    counter.prepare_cache()
    assert len(calls) == 2 and calls[1]["local_files_only"] is False
    assert all(call["trust_remote_code"] is False for call in calls)


def test_missing_tokenizer_dependency_has_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = builtins.__import__

    def guarded(name: str, *args: object, **kwargs: object) -> object:
        if name == "transformers":
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(RuntimeError, match="transformers tokenizer dependency"):
        bge_m3.BgeM3TokenCounter().count("body")


def test_missing_cache_has_explicit_preparation_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise OSError("not cached")

    fake = ModuleType("transformers")
    fake.AutoTokenizer = SimpleNamespace(from_pretrained=missing)
    monkeypatch.setitem(sys.modules, "transformers", fake)
    with pytest.raises(RuntimeError, match="--prepare-model"):
        bge_m3.BgeM3TokenCounter().count("body")


def test_default_service_counter_is_injected_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bge_m3.BgeM3TokenCounter, "count", lambda self, text: len(text.split())
    )
    chunks = chunk_document(
        document([unit("Body.")]), company_name="Example", settings=settings()
    )
    assert len(chunks) == 1 and chunks[0].body == "Body."


def test_contiguous_units_with_same_locator_form_one_segment() -> None:
    chunks = chunk_document(
        document([unit("First body."), unit("Second body.")]),
        company_name="Example",
        token_counter=WordCounter(),
        settings=settings(),
    )
    assert len(chunks) == 1
    assert "First body." in chunks[0].body and "Second body." in chunks[0].body


def test_long_run_character_overlap_is_lossless() -> None:
    text = "ABCDE" * 350
    chunks = chunk_document(
        document([unit(text)]),
        company_name="Example",
        token_counter=CharacterCounter(),
        settings=settings(),
    )
    assert len(chunks) > 1
    assert chunks[0].body + "".join(chunk.body[60:] for chunk in chunks[1:]) == text
    assert all(chunk.token_count <= 575 for chunk in chunks)


def test_high_overlap_still_makes_progress_with_whole_paragraphs() -> None:
    text = words("left", 440) + "\n\n" + words("right", 440)
    chunks = chunk_document(
        document([unit(text)]),
        company_name="Example",
        token_counter=WordCounter(),
        settings=settings(CHUNK_OVERLAP=450),
    )
    assert len(chunks) == 2
    assert all(chunk.token_count <= 575 for chunk in chunks)
    assert "right0439" in chunks[-1].body


def test_fenced_table_like_text_stays_prose() -> None:
    text = "```text\n| ID | Value |\n| --- | --- |\n| literal | sample |\n```"
    chunks = chunk_document(
        document([unit(text)]),
        company_name="Example",
        token_counter=WordCounter(),
        settings=settings(),
    )
    assert len(chunks) == 1 and not chunks[0].is_table
    assert chunks[0].body == text


def test_repeated_heading_occurrences_are_distinct_sections() -> None:
    text = "# Same\nFirst occurrence.\n# Same\nSecond occurrence."
    chunks = chunk_document(
        document([unit(text)]),
        company_name="Example",
        token_counter=WordCounter(),
        settings=settings(),
    )
    assert len(chunks) == 2
    assert chunks[0].segment_key != chunks[1].segment_key


def test_tenant_guard_rejects_non_uuid_before_opening_connection() -> None:
    factory = MagicMock()
    with pytest.raises(TypeError, match="uuid.UUID"):
        with tenant_session(factory, "not-a-uuid"):
            pytest.fail("must not yield")
    factory.begin.assert_not_called()


def test_tenant_guard_rejects_unsafe_role_before_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = settings(DB_RUNTIME_ROLE='invalid"role')
    monkeypatch.setattr("app.repositories.base.get_settings", lambda: config)
    factory = MagicMock()
    with pytest.raises(TenantRoleError, match="Invalid configured"):
        with tenant_session(factory, uuid4()):
            pytest.fail("must not yield")
    factory.begin.assert_not_called()


def test_admin_marker_is_cleared_even_when_body_fails() -> None:
    session = MagicMock()
    session.info = {}
    factory = MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    with pytest.raises(RuntimeError, match="test interruption"):
        with admin_session(factory) as active:
            assert active is session and is_admin_session(session)
            raise RuntimeError("test interruption")
    assert not is_admin_session(session)


@pytest.mark.parametrize("kind", ["company", "document"])
def test_repository_constructor_rejects_invalid_tenant_before_sql(kind: str) -> None:
    from app.repositories.company import CompanyRepository
    from app.repositories.document import DocumentRepository

    session = MagicMock()
    repository = CompanyRepository if kind == "company" else DocumentRepository
    with pytest.raises(TypeError, match="uuid.UUID"):
        repository(session, "invalid-tenant")
    session.execute.assert_not_called()
    session.scalar.assert_not_called()
