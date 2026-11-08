"""BGE-M3 token counting without loading model weights (IN-4; SRD 12)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol, cast

from app.core.config import get_settings


class _Tokenizer(Protocol):
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]: ...


class _TokenizerFactory(Protocol):
    def from_pretrained(
        self,
        name: str,
        *,
        revision: str,
        local_files_only: bool,
        trust_remote_code: bool,
        use_fast: bool,
    ) -> _Tokenizer: ...


@lru_cache(maxsize=8)
def _load_tokenizer(
    model_name: str,
    model_revision: str,
    allow_download: bool,
) -> _Tokenizer:
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "Install the project's transformers tokenizer dependency"
        ) from exc
    try:
        tokenizer = cast(_TokenizerFactory, AutoTokenizer).from_pretrained(
            model_name,
            revision=model_revision,
            local_files_only=not allow_download,
            trust_remote_code=False,
            use_fast=True,
        )
    except OSError:
        raise RuntimeError(
            "BGE-M3 tokenizer is not cached; run python scripts/embed_benchmark.py --prepare-model first"
        ) from None
    return tokenizer


class BgeM3TokenCounter:
    """Load the tokenizer on first use; normal counting is strictly offline."""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        model_revision: str | None = None,
    ) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.EMBEDDING_MODEL_NAME
        self.model_revision = model_revision or settings.EMBEDDING_MODEL_REVISION

    def prepare_cache(self: Any = None) -> None:
        """Explicitly download tokenizer files, never model weights."""
        if isinstance(self, BgeM3TokenCounter):
            name = self.model_name
            rev = self.model_revision
        else:
            settings = get_settings()
            name = settings.EMBEDDING_MODEL_NAME
            rev = settings.EMBEDDING_MODEL_REVISION
        _load_tokenizer(name, rev, True)

    def count(self, text: str) -> int:
        """Count content tokens, excluding tokenizer-added special tokens."""
        tokenizer = _load_tokenizer(self.model_name, self.model_revision, False)
        return len(tokenizer.encode(text, add_special_tokens=False))

    count_tokens = count
