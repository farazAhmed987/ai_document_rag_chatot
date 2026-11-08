"""Tests-first contract for Phase 6 tokenizer and preparation hardening."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.core.config import get_settings

MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def tokenizer_module() -> Iterator[ModuleType]:
    """Import the tokenizer adapter and clear its process cache."""
    module = importlib.import_module("app.adapters.tokenizers.bge_m3")
    module._load_tokenizer.cache_clear()
    yield module
    module._load_tokenizer.cache_clear()


@pytest.fixture
def tokenizer_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, Any]]]:
    """Install a fake transformers module and capture tokenizer loading."""
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeTokenizer:
        def encode(
            self,
            text: str,
            *,
            add_special_tokens: bool,
        ) -> list[int]:
            del add_special_tokens
            return list(range(len(text.split())))

    class FakeAutoTokenizer:
        @classmethod
        def from_pretrained(
            cls,
            name: str,
            **kwargs: Any,
        ) -> FakeTokenizer:
            del cls
            calls.append((name, kwargs))
            return FakeTokenizer()

    fake_transformers = ModuleType("transformers")
    fake_transformers.AutoTokenizer = FakeAutoTokenizer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    return calls


@pytest.fixture
def benchmark_module() -> Iterator[ModuleType]:
    """Import the benchmark script without relying on scripts as a package."""
    script_path = REPOSITORY_ROOT / "scripts" / "embed_benchmark.py"
    module_name = "_phase6_embed_benchmark_test"
    specification = importlib.util.spec_from_file_location(
        module_name,
        script_path,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(module_name, None)


def test_tokenizer_uses_settings_revision_by_default(
    tokenizer_module: ModuleType,
    tokenizer_calls: list[tuple[str, dict[str, Any]]],
) -> None:
    """Default tokenizer revision must match the embedding settings."""
    counter = tokenizer_module.BgeM3TokenCounter()
    counter.count("default revision")
    assert tokenizer_calls[0][1]["revision"] == (
        get_settings().EMBEDDING_MODEL_REVISION
    )


def test_tokenizer_revision_is_constructor_overridable(
    tokenizer_module: ModuleType,
    tokenizer_calls: list[tuple[str, dict[str, Any]]],
) -> None:
    """Tests may explicitly override the otherwise pinned revision."""
    counter = tokenizer_module.BgeM3TokenCounter(
        model_revision=MODEL_REVISION,
    )
    counter.count("overridden revision")
    assert tokenizer_calls[0][1]["revision"] == MODEL_REVISION


def test_tokenizer_normal_load_is_strictly_offline(
    tokenizer_module: ModuleType,
    tokenizer_calls: list[tuple[str, dict[str, Any]]],
) -> None:
    """Normal token counting must use only locally cached tokenizer files."""
    counter = tokenizer_module.BgeM3TokenCounter()
    counter.count("offline load")
    kwargs = tokenizer_calls[0][1]
    assert kwargs["local_files_only"] is True
    assert kwargs["trust_remote_code"] is False
    assert kwargs["use_fast"] is True


def test_tokenizer_missing_cache_points_to_prepare_model(
    tokenizer_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing tokenizer cache must identify the approved setup command."""

    class FailingAutoTokenizer:
        @classmethod
        def from_pretrained(
            cls,
            name: str,
            **kwargs: Any,
        ) -> object:
            del cls, name, kwargs
            raise OSError("sentinel provider error")

    fake_transformers = ModuleType("transformers")
    fake_transformers.AutoTokenizer = FailingAutoTokenizer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    tokenizer_module._load_tokenizer.cache_clear()

    with pytest.raises(RuntimeError) as captured:
        tokenizer_module.BgeM3TokenCounter().count("cache missing")

    message = str(captured.value)
    assert "python scripts/embed_benchmark.py --prepare-model" in message
    assert "verify_chunks.py --prepare-tokenizer" not in message
    assert "sentinel provider error" not in message


def test_prepare_model_cli_prepares_pinned_tokenizer(
    benchmark_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existing prepare-model path must prepare model and tokenizer."""
    events: list[tuple[object, ...]] = []

    class FakeEmbedder:
        @classmethod
        def prepare_model(cls, **kwargs: Any) -> None:
            del cls
            events.append(("model", kwargs))

    class FakeTokenCounter:
        def __init__(
            self,
            *,
            model_name: str,
            model_revision: str,
        ) -> None:
            events.append(("tokenizer_init", model_name, model_revision))

        def prepare_cache(self) -> None:
            events.append(("tokenizer_prepare",))

    monkeypatch.setattr(
        benchmark_module,
        "BgeM3Embedder",
        FakeEmbedder,
    )
    monkeypatch.setattr(
        benchmark_module,
        "BgeM3TokenCounter",
        FakeTokenCounter,
        raising=False,
    )

    settings = get_settings()
    benchmark_module.prepare(settings)

    assert (
        "tokenizer_init",
        settings.EMBEDDING_MODEL_NAME,
        settings.EMBEDDING_MODEL_REVISION,
    ) in events
    assert ("tokenizer_prepare",) in events
    assert any(event[0] == "model" for event in events)


def test_module_level_prepare_model_alias_is_absent() -> None:
    """Only BgeM3Embedder.prepare_model remains public."""
    module = importlib.import_module("app.adapters.embedders.bge_m3")
    assert not hasattr(module, "prepare_model")


def test_embedder_class_prepare_model_remains_available() -> None:
    """The supported class preparation API must remain available."""
    module = importlib.import_module("app.adapters.embedders.bge_m3")
    assert callable(module.BgeM3Embedder.prepare_model)


def test_chunk_fingerprint_harness_is_committed() -> None:
    """The exact Phase 5 fingerprint harness becomes a versioned script."""
    fingerprint_script = REPOSITORY_ROOT / "scripts" / "chunk_fingerprint.py"
    assert fingerprint_script.is_file()
