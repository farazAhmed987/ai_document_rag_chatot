"""SHA-256 helpers for per-company duplicate detection (DM-7, SRD §8.1)."""

from __future__ import annotations

import hashlib
from pathlib import Path


def file_sha256(path: Path, *, block_size: int = 1024 * 1024) -> str:
    """Return a file's SHA-256 without loading the entire file into memory.

    Args:
        path: File to hash.
        block_size: Number of bytes read per iteration.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def text_sha256(text: str) -> str:
    """Return the SHA-256 of a text's UTF-8 bytes.

    Args:
        text: Text to hash.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
