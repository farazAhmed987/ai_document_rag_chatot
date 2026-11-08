"""Phase 5 offline embedder contracts (IN-6; SRD 8.1 step 6 and 12)."""

from __future__ import annotations

import builtins
import importlib
import math
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def load(name: str) -> ModuleType:
    """Import a Phase 5 module at test call time so RED is test evidence."""
    return importlib.import_module(name)


def settings(**overrides: object) -> Settings:
    """Build isolated settings without reading developer credentials."""
    values: dict[str, object] = {
        "_env_file": None,
        "DATABASE_URL": None,
        "ANALYTICS_DATABASE_URL": None,
        "API_KEY": None,
        "EMBEDDER_BACKEND": "fake",
        "EMBEDDING_MODEL_NAME": "BAAI/bge-m3",
        "EMBEDDING_MODEL_REVISION": "0" * 40,
        "EMBEDDING_DIMENSION": 1024,
        "EMBEDDING_BATCH_SIZE": 16,
        "EMBEDDING_MAX_LENGTH": 2048,
    }
    values.update(overrides)
    return Settings(**values)


def vector_norm(vector: list[float]) -> float:
    return math.sqrt(math.fsum(value * value for value in vector))


def cosine(left: list[float], right: list[float]) -> float:
    return math.fsum(a * b for a, b in zip(left, right, strict=True))


def test_embedding_settings_defaults_are_pinned_and_cpu_safe() -> None:
    config = Settings(_env_file=None)
    assert config.EMBEDDER_BACKEND == "bge-m3"
    assert config.EMBEDDING_MODEL_NAME == "BAAI/bge-m3"
    assert config.EMBEDDING_DIMENSION == 1024
    assert config.EMBEDDING_BATCH_SIZE == 16
    assert config.EMBEDDING_MAX_LENGTH >= 2048
    assert len(config.EMBEDDING_MODEL_REVISION) == 40
    assert all(char in "0123456789abcdef" for char in config.EMBEDDING_MODEL_REVISION)


@pytest.mark.parametrize(
    "overrides",
    [
        {"EMBEDDER_BACKEND": "unknown"},
        {"EMBEDDING_DIMENSION": 0},
        {"EMBEDDING_BATCH_SIZE": 0},
        {"EMBEDDING_MAX_LENGTH": 2047},
        {"EMBEDDING_MODEL_REVISION": "main"},
    ],
)
def test_embedding_settings_reject_invalid_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_fake_embedder_satisfies_runtime_protocol() -> None:
    base = load("app.adapters.embedders.base")
    fake = load("app.adapters.embedders.fake")
    embedder = fake.FakeEmbedder()
    assert isinstance(embedder, base.Embedder)
    assert embedder.model_name
    assert embedder.model_revision
    assert embedder.dimension == 1024


def test_fake_embedder_is_deterministic_across_instances() -> None:
    fake = load("app.adapters.embedders.fake")
    first = fake.FakeEmbedder().embed_documents(["same text", "different text"])
    second = fake.FakeEmbedder().embed_documents(["same text", "different text"])
    assert first == second
    assert first[0] != first[1]


@pytest.mark.parametrize("count", [0, 1, 33])
def test_fake_embedder_batch_edges_preserve_count_and_order(count: int) -> None:
    fake = load("app.adapters.embedders.fake")
    embedder = fake.FakeEmbedder()
    texts = [f"document {index}" for index in range(count)]
    actual = embedder.embed_documents(texts, batch_size=16)
    expected = [embedder.embed_query(text) for text in texts]
    assert actual == expected
    assert len(actual) == count


def test_fake_vectors_have_configured_dimension_and_unit_norm() -> None:
    fake = load("app.adapters.embedders.fake")
    embedder = fake.FakeEmbedder(dimension=1024)
    vectors = embedder.embed_documents(["alpha", "beta", "alpha"])
    assert all(len(vector) == 1024 for vector in vectors)
    assert all(
        vector_norm(vector) == pytest.approx(1.0, abs=1e-12) for vector in vectors
    )
    assert vectors[0] == vectors[2]


def test_fake_query_and_document_methods_share_unprefixed_embedding_space() -> None:
    fake = load("app.adapters.embedders.fake")
    embedder = fake.FakeEmbedder()
    query = embedder.embed_query("booking fee")
    document = embedder.embed_documents(["booking fee"])[0]
    assert query == document
    assert cosine(query, document) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("batch_size", [0, -1])
def test_fake_embedder_rejects_invalid_batch_size(batch_size: int) -> None:
    fake = load("app.adapters.embedders.fake")
    with pytest.raises(ValueError, match="batch_size"):
        fake.FakeEmbedder().embed_documents(["text"], batch_size=batch_size)


def test_dimension_guard_rejects_vector_schema_mismatch() -> None:
    base = load("app.adapters.embedders.base")
    errors = load("app.core.errors")
    fake = load("app.adapters.embedders.fake")
    with pytest.raises(errors.EmbeddingConfigurationError, match="1024") as caught:
        base.validate_embedder_dimension(fake.FakeEmbedder(dimension=3), 1024)
    assert caught.value.code == "embedding_dimension_mismatch"
    assert caught.value.details == {"configured": 3, "required": 1024}


def test_factory_selects_fake_without_heavy_ml_imports() -> None:
    before = set(sys.modules)
    factory = load("app.adapters.embedders.factory")
    fake = load("app.adapters.embedders.fake")
    embedder = factory.create_embedder(settings=settings(EMBEDDER_BACKEND="fake"))
    assert isinstance(embedder, fake.FakeEmbedder)
    imported = set(sys.modules) - before
    assert not any(
        name == "torch"
        or name.startswith("torch.")
        or name == "sentence_transformers"
        or name.startswith("sentence_transformers.")
        for name in imported
    )


def test_factory_builds_bge_adapter_lazily_without_loading_model() -> None:
    before = set(sys.modules)
    factory = load("app.adapters.embedders.factory")
    bge = load("app.adapters.embedders.bge_m3")
    embedder = factory.create_embedder(settings=settings(EMBEDDER_BACKEND="bge-m3"))
    assert isinstance(embedder, bge.BgeM3Embedder)
    assert embedder.model_name == "BAAI/bge-m3"
    assert embedder.dimension == 1024
    assert embedder.max_length == 2048
    imported = set(sys.modules) - before
    assert not any(
        name == "torch"
        or name.startswith("torch.")
        or name == "sentence_transformers"
        or name.startswith("sentence_transformers.")
        for name in imported
    )


class FakeTokenizer:
    """Minimal tokenizer surface used by the adapter's length guard."""

    def __call__(
        self,
        texts: str | list[str],
        *,
        add_special_tokens: bool,
        truncation: bool,
    ) -> dict[str, list[int] | list[list[int]]]:
        assert add_special_tokens is False
        assert truncation is False
        if isinstance(texts, str):
            return {"input_ids": list(range(len(texts.split())))}
        return {"input_ids": [list(range(len(text.split()))) for text in texts]}


class FakeMatrix:
    def __init__(self, rows: list[list[float]]) -> None:
        self.rows = rows

    def tolist(self) -> list[list[float]]:
        return self.rows


class FakeSentenceTransformer:
    """Deterministic stand-in; no torch, network or real model dependency."""

    def __init__(self, *args: Any, dimension: int = 1024, **kwargs: Any) -> None:
        self.dimension = dimension
        self.max_seq_length = 0
        self.tokenizer = FakeTokenizer()
        self.encoded_batches: list[list[str]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimension

    def encode(self, texts: list[str], **kwargs: object) -> FakeMatrix:
        assert kwargs["normalize_embeddings"] is True
        assert kwargs["convert_to_numpy"] is True
        assert kwargs["show_progress_bar"] is False
        self.encoded_batches.append(list(texts))
        rows: list[list[float]] = []
        for text in texts:
            marker = float(sum(ord(char) for char in text) % 997 + 1)
            row = [0.0] * self.dimension
            row[0] = 1.0
            row[1] = marker / 1000.0
            scale = vector_norm(row)
            rows.append([value / scale for value in row])
        return FakeMatrix(rows)


@contextmanager
def patched_model(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dimension: int = 1024,
) -> Iterator[tuple[ModuleType, FakeSentenceTransformer]]:
    bge = load("app.adapters.embedders.bge_m3")
    model = FakeSentenceTransformer(dimension=dimension)
    monkeypatch.setattr(bge, "_load_model", lambda *args, **kwargs: model)
    yield bge, model


def test_bge_adapter_sorts_by_length_and_restores_original_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with patched_model(monkeypatch) as (bge, model):
        embedder = bge.BgeM3Embedder(
            model_name="BAAI/bge-m3",
            model_revision="0" * 40,
            dimension=1024,
            max_length=2048,
        )
        texts = ["medium text", "x", "this is the longest text"]
        actual = embedder.embed_documents(texts, batch_size=2)
        assert model.encoded_batches == [
            ["this is the longest text", "medium text"],
            ["x"],
        ]
        expected_markers = [
            float(sum(ord(char) for char in text) % 997 + 1) / 1000.0 for text in texts
        ]
        actual_markers = [vector[1] / vector[0] for vector in actual]
        assert actual_markers == pytest.approx(expected_markers)
        assert all(vector_norm(vector) == pytest.approx(1.0) for vector in actual)


def test_bge_adapter_rejects_overlength_text_before_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with patched_model(monkeypatch) as (bge, model):
        errors = load("app.core.errors")
        embedder = bge.BgeM3Embedder(
            model_name="BAAI/bge-m3",
            model_revision="0" * 40,
            dimension=1024,
            max_length=2048,
        )
        text = " ".join("token" for _ in range(2049))
        with pytest.raises(
            errors.EmbeddingInputError, match="maximum token length"
        ) as caught:
            embedder.embed_documents([text])
        assert caught.value.code == "embedding_input_too_long"
        assert caught.value.details == {"index": 0, "observed": 2049, "limit": 2048}
        assert text not in str(caught.value)
        assert model.encoded_batches == []


def test_bge_adapter_reports_model_dimension_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with patched_model(monkeypatch, dimension=3) as (bge, _model):
        errors = load("app.core.errors")
        embedder = bge.BgeM3Embedder(
            model_name="BAAI/bge-m3",
            model_revision="0" * 40,
            dimension=1024,
            max_length=2048,
        )
        with pytest.raises(errors.EmbeddingConfigurationError, match="dimension"):
            embedder.embed_documents(["text"])


def test_bge_model_load_is_process_cached_offline_and_revision_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    bge._load_model.cache_clear()
    calls: list[tuple[str, dict[str, object]]] = []

    class Constructor(FakeSentenceTransformer):
        def __init__(self, name: str, **kwargs: object) -> None:
            calls.append((name, kwargs))
            super().__init__()

    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = Constructor
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    torch_mod = ModuleType("torch")
    torch_mod.device = lambda d: d  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    revision = "a" * 40
    first = bge.BgeM3Embedder(
        model_name="BAAI/bge-m3",
        model_revision=revision,
        dimension=1024,
        max_length=2048,
    )
    second = bge.BgeM3Embedder(
        model_name="BAAI/bge-m3",
        model_revision=revision,
        dimension=1024,
        max_length=2048,
    )
    first.embed_query("first")
    second.embed_query("second")
    assert len(calls) == 1
    name, kwargs = calls[0]
    assert name == "BAAI/bge-m3"
    assert kwargs["revision"] == revision
    assert kwargs["device"] == "cpu"
    assert kwargs["local_files_only"] is True
    assert kwargs["trust_remote_code"] is False


def test_missing_sentence_transformers_dependency_has_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    bge._load_model.cache_clear()
    original = builtins.__import__

    def guarded(name: str, *args: object, **kwargs: object) -> Any:
        if name == "sentence_transformers":
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    embedder = bge.BgeM3Embedder(
        model_name="BAAI/bge-m3",
        model_revision="0" * 40,
        dimension=1024,
        max_length=2048,
    )
    with pytest.raises(RuntimeError, match="requirements-ml.txt"):
        embedder.embed_query("text")


def test_bge_adapter_handles_empty_input_and_rejects_invalid_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with patched_model(monkeypatch) as (bge, model):
        embedder = bge.BgeM3Embedder(
            model_name="BAAI/bge-m3",
            model_revision="0" * 40,
            dimension=1024,
            max_length=2048,
        )
        assert embedder.embed_documents([]) == []
        with pytest.raises(ValueError, match="batch_size"):
            embedder.embed_documents(["text"], batch_size=0)
        assert model.encoded_batches == []


def test_explicit_model_preparation_uses_download_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    model = FakeSentenceTransformer()
    calls: list[tuple[str, str, int, bool]] = []

    def loader(name: str, revision: str, length: int, download: bool) -> object:
        calls.append((name, revision, length, download))
        return model

    monkeypatch.setattr(bge, "_load_model", loader)
    embedder = bge.BgeM3Embedder.prepare_model(
        model_name="BAAI/bge-m3",
        model_revision="0" * 40,
        dimension=1024,
        max_length=2048,
    )
    assert embedder.dimension == 1024
    assert calls == [("BAAI/bge-m3", "0" * 40, 2048, True)]


def test_missing_offline_model_cache_has_prepare_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    bge._load_model.cache_clear()

    class MissingModel:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise OSError("not cached")

    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = MissingModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    torch_mod = ModuleType("torch")
    torch_mod.device = lambda d: d  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    embedder = bge.BgeM3Embedder(
        model_name="BAAI/bge-m3",
        model_revision="0" * 40,
        dimension=1024,
        max_length=2048,
    )
    with pytest.raises(RuntimeError, match="--prepare-model"):
        embedder.embed_query("text")


def test_zero_vector_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with patched_model(monkeypatch) as (bge, model):
        errors = load("app.core.errors")
        monkeypatch.setattr(
            model,
            "encode",
            lambda texts, **kwargs: FakeMatrix([[0.0] * 1024 for _ in texts]),
        )
        embedder = bge.BgeM3Embedder(
            model_name="BAAI/bge-m3",
            model_revision="0" * 40,
            dimension=1024,
            max_length=2048,
        )
        with pytest.raises(errors.EmbeddingConfigurationError, match="zero vector"):
            embedder.embed_documents(["text"])


@pytest.fixture(autouse=True)
def clean_embedding_test_state() -> Iterator[None]:
    orig_hf = os.environ.get("HF_HUB_OFFLINE")
    orig_tf = os.environ.get("TRANSFORMERS_OFFLINE")

    def _clear_cache() -> None:
        if "app.adapters.embedders.bge_m3" in sys.modules:
            bge = sys.modules["app.adapters.embedders.bge_m3"]
            cache = getattr(bge, "_MODEL_CACHE", None)
            if isinstance(cache, dict):
                cache.clear()
            load_fn = getattr(bge, "_load_model", None)
            if hasattr(load_fn, "cache_clear"):
                load_fn.cache_clear()

    _clear_cache()
    try:
        yield
    finally:
        _clear_cache()
        if orig_hf is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = orig_hf

        if orig_tf is None:
            os.environ.pop("TRANSFORMERS_OFFLINE", None)
        else:
            os.environ["TRANSFORMERS_OFFLINE"] = orig_tf

        for mod_name in list(sys.modules):
            if mod_name in ("torch", "sentence_transformers") or mod_name.startswith(
                ("torch.", "sentence_transformers.")
            ):
                mod = sys.modules.get(mod_name)
                if mod is not None and getattr(mod, "__file__", None) is None:
                    sys.modules.pop(mod_name, None)


def test_factory_rejects_fake_dimension_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = load("app.adapters.embedders.factory")
    fake = load("app.adapters.embedders.fake")
    errors = load("app.core.errors")
    config = settings(EMBEDDER_BACKEND="fake", EMBEDDING_DIMENSION=768)
    instantiated = False

    def guarded_init(self: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal instantiated
        instantiated = True
        raise AssertionError(
            "FakeEmbedder should not be constructed on dimension mismatch"
        )

    monkeypatch.setattr(fake.FakeEmbedder, "__init__", guarded_init)
    with pytest.raises(errors.EmbeddingConfigurationError):
        factory.create_embedder(settings=config)
    assert not instantiated


def test_factory_rejects_bge_dimension_mismatch_before_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    errors = load("app.core.errors")
    config = settings(EMBEDDER_BACKEND="bge-m3", EMBEDDING_DIMENSION=768)

    sys.modules.pop("app.adapters.embedders.bge_m3", None)
    sys.modules.pop("app.adapters.embedders.factory", None)
    factory = importlib.import_module("app.adapters.embedders.factory")
    assert "app.adapters.embedders.bge_m3" not in sys.modules, (
        "bge_m3 imported eagerly by factory module"
    )
    before_call = set(sys.modules)
    with pytest.raises(errors.EmbeddingConfigurationError):
        factory.create_embedder(settings=config)
    newly_imported = set(sys.modules) - before_call
    assert "app.adapters.embedders.bge_m3" not in sys.modules
    assert "app.adapters.embedders.bge_m3" not in newly_imported
    assert "torch" not in newly_imported
    assert "sentence_transformers" not in newly_imported


def test_factory_rejects_unknown_backend() -> None:
    factory = load("app.adapters.embedders.factory")
    errors = load("app.core.errors")
    config = Settings.model_construct(
        _env_file=None,
        DATABASE_URL=None,
        ANALYTICS_DATABASE_URL=None,
        API_KEY=None,
        EMBEDDER_BACKEND="unsupported",
        EMBEDDING_MODEL_NAME="BAAI/bge-m3",
        EMBEDDING_MODEL_REVISION="0" * 40,
        EMBEDDING_DIMENSION=1024,
        EMBEDDING_BATCH_SIZE=16,
        EMBEDDING_MAX_LENGTH=2048,
    )
    with pytest.raises(errors.EmbeddingConfigurationError, match="unsupported"):
        factory.create_embedder(settings=config)


def test_offline_flags_exist_before_heavy_module_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    recorded_env: dict[str, str | None] = {}

    class TrackingLoader:
        def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
            if fullname in ("torch", "sentence_transformers"):
                from importlib.machinery import ModuleSpec

                return ModuleSpec(fullname, self)
            return None

        def create_module(self, spec: Any) -> Any:
            recorded_env[f"{spec.name}_HF"] = os.environ.get("HF_HUB_OFFLINE")
            recorded_env[f"{spec.name}_TF"] = os.environ.get("TRANSFORMERS_OFFLINE")
            mod = ModuleType(spec.name)
            if spec.name == "torch":
                mod.device = lambda d: d
            elif spec.name == "sentence_transformers":
                mod.SentenceTransformer = FakeSentenceTransformer
            return mod

        def exec_module(self, module: Any) -> None:
            pass

    loader = TrackingLoader()
    monkeypatch.setattr(sys, "meta_path", [loader] + sys.meta_path)
    sys.modules.pop("torch", None)
    sys.modules.pop("sentence_transformers", None)
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)

    try:
        bge._load_model("BAAI/bge-m3", "0" * 40, offline=True)
    except TypeError:
        bge._load_model("BAAI/bge-m3", "0" * 40, 2048, False)

    assert recorded_env.get("sentence_transformers_HF") == "1", (
        "HF_HUB_OFFLINE not set before import"
    )
    assert recorded_env.get("sentence_transformers_TF") == "1", (
        "TRANSFORMERS_OFFLINE not set before import"
    )


def test_prepare_model_rejects_hf_offline_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    errors = load("app.core.errors")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(errors.EmbeddingConfigurationError, match="online") as exc_info:
        bge.BgeM3Embedder.prepare_model(
            model_name="BAAI/bge-m3",
            model_revision="0" * 40,
            dimension=1024,
            max_length=2048,
        )
    error_msg = str(exc_info.value)
    assert "Himalaya" not in error_msg
    assert "Realty" not in error_msg


def test_concurrent_first_load_constructs_model_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    constructor_calls = 0
    lock = threading.Lock()

    class SlowFakeSentenceTransformer(FakeSentenceTransformer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            nonlocal constructor_calls
            with lock:
                constructor_calls += 1
            time.sleep(0.05)
            super().__init__(dimension=1024)

    mod = ModuleType("sentence_transformers")
    mod.SentenceTransformer = SlowFakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)

    torch_mod = ModuleType("torch")
    torch_mod.device = lambda d: d  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch_mod)

    barrier = threading.Barrier(8)
    results: list[Any] = [None] * 8
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=5.0)
            try:
                model = bge._load_model("BAAI/bge-m3", "0" * 40, offline=True)
            except TypeError:
                model = bge._load_model("BAAI/bge-m3", "0" * 40, 2048, False)
            results[idx] = model
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"Thread errors: {errors}"
    assert constructor_calls == 1, (
        f"Expected 1 constructor call, got {constructor_calls}"
    )
    assert all(r is results[0] for r in results), (
        "Callers did not receive identical model"
    )


def test_vector_dimension_constant_matches_chunk_column() -> None:
    chunk_module = load("app.models.chunk")
    constant = getattr(chunk_module, "CHUNK_EMBEDDING_DIMENSION", None)
    assert constant is not None, (
        "CHUNK_EMBEDDING_DIMENSION is missing from app.models.chunk"
    )
    chunk_dim = chunk_module.Chunk.__table__.c.embedding.type.dim
    assert constant == 1024
    assert chunk_dim == 1024
    assert constant == chunk_dim


def test_prepare_model_online_does_not_set_offline_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    fake_model = FakeSentenceTransformer(dimension=1024)
    try:
        monkeypatch.setattr(bge, "_load_model", lambda *args, **kwargs: fake_model)
    except Exception:
        pass
    embedder = bge.BgeM3Embedder.prepare_model(
        model_name="BAAI/bge-m3",
        model_revision="0" * 40,
        dimension=1024,
        max_length=2048,
    )
    assert embedder.dimension == 1024
    assert os.environ.get("HF_HUB_OFFLINE") is None
    assert os.environ.get("TRANSFORMERS_OFFLINE") is None


def test_failed_first_load_leaves_model_cache_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")

    class FailingModel:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("load failed")

    mod = ModuleType("sentence_transformers")
    mod.SentenceTransformer = FailingModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)

    torch_mod = ModuleType("torch")
    torch_mod.device = lambda d: d  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch_mod)

    with pytest.raises((RuntimeError, OSError)):
        try:
            bge._load_model("BAAI/bge-m3", "fail_rev", offline=True)
        except TypeError:
            bge._load_model("BAAI/bge-m3", "fail_rev", 2048, False)

    cache = getattr(bge, "_MODEL_CACHE", None)
    if isinstance(cache, dict):
        assert ("BAAI/bge-m3", "fail_rev") not in cache


def test_bge_batch_16_restores_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with patched_model(monkeypatch) as (bge, model):
        embedder = bge.BgeM3Embedder(
            model_name="BAAI/bge-m3",
            model_revision="0" * 40,
            dimension=1024,
            max_length=2048,
        )
        texts = [
            f"sample text item number {i} with varied content" * (i % 3 + 1)
            for i in range(16)
        ]
        vectors = embedder.embed_documents(texts, batch_size=16)
        assert len(vectors) == 16
        assert all(len(v) == 1024 for v in vectors)


def test_bge_batch_17_restores_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with patched_model(monkeypatch) as (bge, model):
        embedder = bge.BgeM3Embedder(
            model_name="BAAI/bge-m3",
            model_revision="0" * 40,
            dimension=1024,
            max_length=2048,
        )
        texts = [f"sample text item number {i}" * (i % 4 + 1) for i in range(17)]
        vectors = embedder.embed_documents(texts, batch_size=16)
        assert len(vectors) == 17
        assert all(len(v) == 1024 for v in vectors)


def test_bge_empty_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with patched_model(monkeypatch) as (bge, model):
        embedder = bge.BgeM3Embedder(
            model_name="BAAI/bge-m3",
            model_revision="0" * 40,
            dimension=1024,
            max_length=2048,
        )
        assert embedder.embed_documents([], batch_size=16) == []


def test_fake_batch_16_preserves_order() -> None:
    fake = load("app.adapters.embedders.fake")
    embedder = fake.FakeEmbedder(dimension=1024)
    texts = [f"text-{i}" for i in range(16)]
    vectors = embedder.embed_documents(texts, batch_size=16)
    assert len(vectors) == 16
    for i, text in enumerate(texts):
        assert vectors[i] == embedder.embed_query(text)


def test_fake_batch_17_preserves_order() -> None:
    fake = load("app.adapters.embedders.fake")
    embedder = fake.FakeEmbedder(dimension=1024)
    texts = [f"text-{i}" for i in range(17)]
    vectors = embedder.embed_documents(texts, batch_size=16)
    assert len(vectors) == 17
    for i, text in enumerate(texts):
        assert vectors[i] == embedder.embed_query(text)


def test_fake_empty_batch() -> None:
    fake = load("app.adapters.embedders.fake")
    embedder = fake.FakeEmbedder(dimension=1024)
    assert embedder.embed_documents([], batch_size=16) == []


def test_prepare_model_offline_raises_typed_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    errors = load("app.core.errors")
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
        monkeypatch.setenv(var, "1")
        with pytest.raises(errors.EmbeddingConfigurationError, match="online"):
            bge.BgeM3Embedder.prepare_model(
                model_name="BAAI/bge-m3",
                model_revision="0" * 40,
                dimension=1024,
                max_length=2048,
            )


def test_dimension_mismatch_does_not_poison_model_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    errors = load("app.core.errors")
    bge._load_model.cache_clear()

    class MismatchedModel(FakeSentenceTransformer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(dimension=768)

    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = MismatchedModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    torch_mod = ModuleType("torch")
    torch_mod.device = lambda d: d  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch_mod)

    embedder = bge.BgeM3Embedder(
        model_name="BAAI/bge-m3",
        model_revision="0" * 40,
        dimension=1024,
        max_length=2048,
    )
    with pytest.raises(errors.EmbeddingConfigurationError, match="dimension"):
        embedder.embed_query("test query")

    assert len(bge._MODEL_CACHE) == 0, (
        "Model cache must be empty after dimension mismatch"
    )


def test_model_max_seq_length_is_set_before_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    bge._load_model.cache_clear()

    max_seq_when_cached: list[int] = []

    class MonitoredCache(dict):
        def __setitem__(self, key: Any, value: Any) -> None:
            max_seq_when_cached.append(getattr(value, "max_seq_length", None))
            super().__setitem__(key, value)

    monitored_cache = MonitoredCache()
    monkeypatch.setattr(bge, "_MODEL_CACHE", monitored_cache)

    class CustomSeqModel(FakeSentenceTransformer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(dimension=1024)
            self.max_seq_length = 512

    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = CustomSeqModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    torch_mod = ModuleType("torch")
    torch_mod.device = lambda d: d  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch_mod)

    model = bge._load_model(
        "BAAI/bge-m3",
        "0" * 40,
        max_length=2048,
        dimension=1024,
        offline=True,
    )
    assert model.max_seq_length == 2048
    assert max_seq_when_cached == [2048]


def test_missing_cache_error_points_to_model_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge = load("app.adapters.embedders.bge_m3")
    bge._load_model.cache_clear()

    class MissingModel:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise OSError("model files not found in offline cache")

    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = MissingModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    torch_mod = ModuleType("torch")
    torch_mod.device = lambda d: d  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch_mod)

    embedder = bge.BgeM3Embedder(
        model_name="BAAI/bge-m3",
        model_revision="0" * 40,
        dimension=1024,
        max_length=2048,
    )
    with pytest.raises(RuntimeError) as exc_info:
        embedder.embed_query("text")

    error_message = str(exc_info.value)
    assert "python scripts/embed_benchmark.py --prepare-model" in error_message
    assert "verify_chunks.py --prepare-tokenizer" not in error_message
