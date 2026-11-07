"""BGE-M3 token counting without loading model weights (IN-4; SRD 12)."""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol, cast


class _Tokenizer(Protocol):
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]: ...


class _TokenizerFactory(Protocol):
    def from_pretrained(
        self,
        name: str,
        *,
        local_files_only: bool,
        trust_remote_code: bool,
        use_fast: bool,
    ) -> _Tokenizer: ...


@lru_cache(maxsize=2)
def _load_tokenizer(allow_download: bool) -> _Tokenizer:
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "Install the project's transformers tokenizer dependency"
        ) from exc
    try:
        tokenizer = cast(_TokenizerFactory, AutoTokenizer).from_pretrained(
            "BAAI/bge-m3",
            local_files_only=not allow_download,
            trust_remote_code=False,
            use_fast=True,
        )
    except OSError as exc:
        raise RuntimeError(
            "BGE-M3 tokenizer is not cached; run verify_chunks.py --prepare-tokenizer first"
        ) from exc
    return tokenizer


class BgeM3TokenCounter:
    """Load the tokenizer on first use; normal counting is strictly offline."""

    @staticmethod
    def prepare_cache() -> None:
        """Explicitly download tokenizer files, never model weights."""
        _load_tokenizer(True)

    def count(self, text: str) -> int:
        """Count content tokens, excluding tokenizer-added special tokens."""
        return len(_load_tokenizer(False).encode(text, add_special_tokens=False))
