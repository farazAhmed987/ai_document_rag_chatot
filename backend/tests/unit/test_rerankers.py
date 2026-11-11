"""Unit tests for reranker adapters, factory, and CrossEncoder API contracts.

SRD Section 8.2; A-12.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.rerankers.base import Reranker
from app.adapters.rerankers.bge_reranker import _RERANKER_CACHE, BgeM3Reranker
from app.adapters.rerankers.factory import create_reranker
from app.adapters.rerankers.fake import FakeReranker
from app.core.config import Settings
from app.core.errors import RerankerConfigurationError


def test_factory_returns_none_for_none_backend() -> None:
    """Factory returns None when RERANKER_BACKEND is 'none'."""
    settings = Settings(RERANKER_BACKEND="none")
    reranker = create_reranker(settings=settings)
    assert reranker is None


def test_factory_returns_fake_reranker() -> None:
    """Factory builds FakeReranker conforming to Reranker protocol."""
    settings = Settings(RERANKER_BACKEND="fake", RERANKER_MAX_LENGTH=512)
    reranker = create_reranker(settings=settings)
    assert isinstance(reranker, FakeReranker)
    assert isinstance(reranker, Reranker)
    assert reranker.max_length == 512
    assert reranker.model_name == "fake"


def test_factory_rejects_unsupported_backend() -> None:
    """Factory raises RerankerConfigurationError for unsupported backend."""
    settings = MagicMock(spec=Settings)
    settings.RERANKER_BACKEND = "unsupported_backend"
    with pytest.raises(RerankerConfigurationError) as exc_info:
        create_reranker(settings=settings)
    assert exc_info.value.code == "RERANKER_CONFIG_ERROR"


def test_fake_reranker_predict_contracts() -> None:
    """FakeReranker produces deterministic scores and validates parameters."""
    reranker = FakeReranker(max_length=1024)

    # 1. Empty pairs returns empty list
    assert reranker.predict([]) == []

    # 2. Invalid batch size raises ValueError
    with pytest.raises(ValueError, match="batch_size must be positive"):
        reranker.predict([("q", "p")], batch_size=0)

    # 3. Invalid max_length raises ValueError
    with pytest.raises(ValueError, match="max_length must be positive"):
        FakeReranker(max_length=0)

    # 4. Deterministic scoring
    pairs = [
        ("deposit policy", "The security deposit policy is strictly enforced."),
        ("pet policy", "Pets are not allowed on the property premises."),
    ]
    scores_1 = reranker.predict(pairs)
    scores_2 = reranker.predict(pairs)

    assert len(scores_1) == 2
    assert all(isinstance(s, float) for s in scores_1)
    # Determinism across repeated calls
    assert scores_1 == scores_2
    # Pair with higher token overlap gets higher score
    assert scores_1[0] > scores_1[1]


def test_bge_m3_reranker_validates_inputs() -> None:
    """BgeM3Reranker validates parameter bounds before model loading."""
    with pytest.raises(ValueError, match="max_length must be positive"):
        BgeM3Reranker(max_length=0)

    reranker = BgeM3Reranker()
    assert reranker.predict([]) == []

    with pytest.raises(ValueError, match="batch_size must be positive"):
        reranker.predict([("q", "p")], batch_size=-1)


def test_bge_m3_reranker_cross_encoder_contracts() -> None:
    """Verifies Decision D2: CrossEncoder receives model_name_or_path and activation_fn=None."""
    mock_cross_encoder_cls = MagicMock()
    mock_instance = MagicMock()
    mock_cross_encoder_cls.return_value = mock_instance
    mock_instance.predict.return_value = [1.234, -0.567]

    fake_st = ModuleType("sentence_transformers")
    fake_st.CrossEncoder = mock_cross_encoder_cls  # type: ignore[attr-defined]

    dummy_revision = "0123456789abcdef0123456789abcdef01234567"
    key = ("mock-reranker", dummy_revision)

    with patch.dict(sys.modules, {"sentence_transformers": fake_st}):
        _RERANKER_CACHE.pop(key, None)
        reranker = BgeM3Reranker(
            model_name="mock-reranker",
            model_revision=dummy_revision,
            max_length=1024,
        )

        pairs = [("query 1", "passage 1"), ("query 2", "passage 2")]
        scores = reranker.predict(pairs, batch_size=16)

        # 1. CrossEncoder.__init__ contract (Decision D2: model_name_or_path, not model_name)
        mock_cross_encoder_cls.assert_called_once_with(
            model_name_or_path="mock-reranker",
            revision=dummy_revision,
            local_files_only=True,
            trust_remote_code=False,
            max_length=1024,
            device="cpu",
        )

        # 2. CrossEncoder.predict contract (Decision D2: activation_fn=None for raw logits)
        mock_instance.predict.assert_called_once_with(
            pairs,
            batch_size=16,
            show_progress_bar=False,
            convert_to_numpy=True,
            activation_fn=None,
        )

        # 3. Output float conversion
        assert scores == [1.234, -0.567]

        # 4. Singleton caching: second invocation reuses cached instance
        mock_cross_encoder_cls.reset_mock()
        reranker_2 = BgeM3Reranker(
            model_name="mock-reranker",
            model_revision=dummy_revision,
        )
        _ = reranker_2.model
        mock_cross_encoder_cls.assert_not_called()
        _RERANKER_CACHE.pop(key, None)


def test_bge_m3_reranker_handles_missing_dependency() -> None:
    """Raises typed RerankerConfigurationError when sentence-transformers is missing."""
    key = ("uninstalled-model", "rev")
    _RERANKER_CACHE.pop(key, None)

    with patch.dict(sys.modules, {"sentence_transformers": None}):
        reranker = BgeM3Reranker(
            model_name="uninstalled-model",
            model_revision="rev",
        )
        with pytest.raises(
            RerankerConfigurationError, match="sentence-transformers is not installed"
        ):
            _ = reranker.model
