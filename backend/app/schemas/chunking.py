"""Transient chunking results; no ORM/schema changes (IN-4, IN-5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SmallChunkReason = Literal["sole_chunk_of_segment", "unit_indivisible"]
SegmentKey = tuple[int | None, str, int]


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """Complete stored/embedded text and source-preserving transient metadata."""

    chunk_index: int
    page: int | None
    section: str
    header: str
    body: str
    content: str
    token_count: int
    is_table: bool
    table_part: str | None
    segment_key: SegmentKey
    sub_min_reason: SmallChunkReason | None = None
