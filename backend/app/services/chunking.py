"""Deterministic citation-preserving chunking (IN-4, IN-5; SRD 8.1).

Budget and minimum exceptions follow Claude's approved Phase 4 decisions.
No database, storage or embedding operations occur in this module.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Protocol

import structlog

from app.core.config import Settings, get_settings
from app.core.errors import ChunkingBudgetError
from app.schemas.chunking import ChunkDraft, SegmentKey, SmallChunkReason
from app.schemas.extraction import CleanedDocument
from app.services.structure import heading_context, iter_headings

logger = structlog.get_logger(__name__)
_HEADER = re.compile(r"\ACompany: [^\n]*\nDocument: [^\n]*\nSection: [^\n]*\n\n")
_SEPARATORS = (r"\n[ \t]*\n+", r"\n", r"(?<=[.!?۔؟])\s+", r"\s+")


class TokenCounter(Protocol):
    """Inject token counting without depending on a model or network."""

    def count(self, text: str) -> int:
        """Count the supplied text deterministically."""
        ...


def strip_context_header(content: str) -> str:
    """Strip only a complete context header at the beginning of content."""
    match = _HEADER.match(content)
    return content[match.end() :] if match else content


def _single_line(value: str) -> str:
    return " ".join(value.split())


@dataclass(frozen=True, slots=True)
class _Block:
    page: int | None
    section: str
    unit_index: int
    text: str
    is_table: bool


@dataclass(frozen=True, slots=True)
class _Atom:
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _Piece:
    first: int
    stop: int
    start: int
    end: int


def _table_line(line: str) -> bool:
    return line.lstrip().startswith("|") and line.count("|") >= 2


def _blocks(document: CleanedDocument) -> list[_Block]:
    result: list[_Block] = []
    force_new = False

    def add(page: int | None, section: str, index: int, text: str, table: bool) -> None:
        nonlocal force_new
        text = text.strip()
        if not text:
            return
        # Consecutive cleaned units with equal locators are one prose segment.
        if (
            result
            and not force_new
            and not table
            and not result[-1].is_table
            and (result[-1].page, result[-1].section) == (page, section)
        ):
            result[-1] = replace(result[-1], text=result[-1].text + "\n\n" + text)
        else:
            result.append(_Block(page, section, index, text, table))
        force_new = False

    for unit in document.units:
        lines = unit.markdown.splitlines()
        headings = {index for index, _, _ in iter_headings(unit.markdown)}
        section = " > ".join(_single_line(part) for part in unit.heading_path)
        buffer: list[str] = []
        page = unit.page_number if document.file_type == "pdf" else None
        index = 0
        fence: str | None = None
        while index < len(lines):
            marker = re.match(r"^\s*(`{3,}|~{3,})", lines[index])
            if marker:
                buffer.append(lines[index])
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
                index += 1
            elif fence is not None:
                buffer.append(lines[index])
                index += 1
            elif index in headings:
                add(page, section, unit.index, "\n".join(buffer), False)
                buffer = []
                force_new = True
                section = " > ".join(
                    _single_line(part) for part in heading_context(unit, index)
                )
                index += 1
            elif _table_line(lines[index]):
                add(page, section, unit.index, "\n".join(buffer), False)
                buffer = []
                rows: list[str] = []
                while index < len(lines) and _table_line(lines[index]):
                    rows.append(lines[index])
                    index += 1
                add(page, section, unit.index, "\n".join(rows), True)
            else:
                buffer.append(lines[index])
                index += 1
        add(page, section, unit.index, "\n".join(buffer), False)
    return result


class _Chunker:
    def __init__(self, config: Settings, counter: TokenCounter) -> None:
        self.config = config
        self.counter = counter
        self.ceiling = config.CHUNK_TOKENS * 115 // 100
        self._counts: dict[str, int] = {}

    def count(self, text: str) -> int:
        if text not in self._counts:
            self._counts[text] = self.counter.count(text)
        return self._counts[text]

    def budget_error(
        self, block: _Block, reason: str, observed: int, limit: int
    ) -> None:
        raise ChunkingBudgetError(
            "Required chunk structure exceeds its token budget",
            details={
                "sub_reason": reason,
                "page": block.page,
                "unit_index": block.unit_index,
                "observed_tokens": observed,
                "limit": limit,
            },
        )

    def atoms(self, text: str, header: str) -> list[_Atom]:
        result: list[_Atom] = []

        def split(start: int, end: int, level: int) -> None:
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if start == end:
                return
            if (
                self.count(header + "\n\n" + text[start:end])
                <= self.config.CHUNK_TOKENS
            ):
                result.append(_Atom(start, end))
                return
            if level < len(_SEPARATORS):
                cursor = start
                ranges: list[tuple[int, int]] = []
                for delimiter in re.finditer(_SEPARATORS[level], text[start:end]):
                    ranges.append((cursor, start + delimiter.start()))
                    cursor = start + delimiter.end()
                ranges.append((cursor, end))
                if level == 0:
                    # DOCX emits separate paragraphs for a list's lead-in and
                    # each bullet. Treat the lead-in/list as one paragraph
                    # candidate; recurse normally when that group is too big.
                    grouped: list[tuple[int, int]] = []
                    for lower, upper in ranges:
                        if grouped and re.match(
                            r"\s*(?:[-+*]\s+|\d+[.)]\s+)", text[lower:upper]
                        ):
                            grouped[-1] = (grouped[-1][0], upper)
                        else:
                            grouped.append((lower, upper))
                    ranges = grouped
                for lower, upper in ranges:
                    split(lower, upper, level + 1)
                return
            logger.debug("hard_split", hard_split=True)
            cursor = start
            while cursor < end:
                low, high = cursor + 1, end
                best = cursor
                while low <= high:
                    middle = (low + high) // 2
                    if (
                        self.count(header + "\n\n" + text[cursor:middle])
                        <= self.config.CHUNK_TOKENS
                    ):
                        best = middle
                        low = middle + 1
                    else:
                        high = middle - 1
                # A Unicode code point is the last lossless fallback. Real BGE-M3
                # always fits a code point when the approved header cap is met.
                best = max(cursor + 1, best)
                result.append(_Atom(cursor, best))
                cursor = best

        split(0, len(text), 0)
        return result

    def suffix_start(self, text: str, start: int, end: int) -> int:
        desired = self.config.CHUNK_OVERLAP
        if not desired:
            return end
        # Prefer word boundaries; exact for the deterministic word-counter port.
        for found in re.finditer(r"\S+", text[start:end]):
            offset = start + found.start()
            if self.count(text[offset:end]) <= desired:
                return offset
        # An overlong single run may have no suitable word boundary.
        low, high = start, end
        while low < high:
            middle = (low + high) // 2
            if self.count(text[middle:end]) <= desired:
                high = middle
            else:
                low = middle + 1
        return low

    def prose(
        self, block: _Block, header: str
    ) -> list[tuple[str, SmallChunkReason | None]]:
        text = block.text
        atoms = self.atoms(text, header)
        pieces: list[_Piece] = []
        first = 0
        while first < len(atoms):
            start = atoms[first].start
            if pieces:
                start = self.suffix_start(text, pieces[-1].start, pieces[-1].end)
                # Keep configured overlap whenever feasible, but guarantee new
                # text progresses even for extreme custom overlap settings.
                while (
                    start < atoms[first].start
                    and self.count(header + "\n\n" + text[start : atoms[first].end])
                    > self.ceiling
                ):
                    start += 1
            stop = first
            while stop < len(atoms):
                count = self.count(header + "\n\n" + text[start : atoms[stop].end])
                if count > self.config.CHUNK_TOKENS and stop > first:
                    break
                if count > self.ceiling:
                    break
                stop += 1
                if count > self.config.CHUNK_TOKENS:
                    break
            if stop == first:
                # Only pathological injected counters can reach this branch:
                # split the atom further without discarding source characters.
                end = atoms[first].start + 1
                pieces.append(_Piece(first, first + 1, atoms[first].start, end))
                if end < atoms[first].end:
                    atoms[first] = _Atom(end, atoms[first].end)
                else:
                    first += 1
                continue
            pieces.append(_Piece(first, stop, start, atoms[stop - 1].end))
            first = stop

        # Fix every undersized fragment, including a short leading paragraph.
        index = 0
        while index < len(pieces) and len(pieces) > 1:
            piece = pieces[index]
            if (
                self.count(text[piece.start : piece.end])
                >= self.config.MIN_CHUNK_TOKENS
            ):
                index += 1
                continue
            left_index = max(0, index - 1)
            left, right = pieces[left_index : left_index + 2]
            if (
                self.count(header + "\n\n" + text[left.start : right.end])
                <= self.ceiling
            ):
                pieces[left_index : left_index + 2] = [
                    _Piece(left.first, right.stop, left.start, right.end)
                ]
                index = max(0, left_index - 1)
                continue
            # Shift only whole recursively selected units. Paragraphs that fit
            # the target remain indivisible here; word fallback units can move.
            boundaries = range(left.stop - 1, left.first, -1)
            for boundary in boundaries:
                end = atoms[boundary - 1].end
                right_start = self.suffix_start(text, left.start, end)
                if not self.config.CHUNK_OVERLAP:
                    right_start = atoms[boundary].start
                left_body, right_body = (
                    text[left.start : end],
                    text[right_start : right.end],
                )
                if (
                    self.count(left_body) >= self.config.MIN_CHUNK_TOKENS
                    and self.count(right_body) >= self.config.MIN_CHUNK_TOKENS
                    and self.count(header + "\n\n" + left_body) <= self.ceiling
                    and self.count(header + "\n\n" + right_body) <= self.ceiling
                ):
                    pieces[left_index : left_index + 2] = [
                        _Piece(left.first, boundary, left.start, end),
                        _Piece(boundary, right.stop, right_start, right.end),
                    ]
                    break
            index += 1

        output: list[tuple[str, SmallChunkReason | None]] = []
        for piece in pieces:
            body = text[piece.start : piece.end]
            reason: SmallChunkReason | None = None
            if self.count(body) < self.config.MIN_CHUNK_TOKENS:
                reason = (
                    "sole_chunk_of_segment" if len(pieces) == 1 else "unit_indivisible"
                )
            output.append((body, reason))
        return output

    def tables(self, block: _Block, header: str) -> list[str]:
        lines = block.text.splitlines()
        # Extractors emit a header row and Markdown separator. A malformed
        # table-like block still keeps every line; no row is split or dropped.
        prefix = lines[:2]
        rows = lines[2:]
        fixed = "\n".join(prefix)
        for row in rows or [""]:
            content = header + "\n\n" + fixed + ("\n" + row if row else "")
            observed = self.count(content)
            if observed > self.config.MAX_TABLE_TOKENS:
                self.budget_error(
                    block, "TABLE_ROW_TOO_LARGE", observed, self.config.MAX_TABLE_TOKENS
                )
        if self.count(header + "\n\n" + block.text) <= self.config.MAX_TABLE_TOKENS:
            return [block.text]
        parts: list[str] = []
        current = list(prefix)
        for row in rows:
            candidate = "\n".join([*current, row])
            if self.count(header + "\n\n" + candidate) > self.config.MAX_TABLE_TOKENS:
                parts.append("\n".join(current))
                current = list(prefix)
            current.append(row)
        parts.append("\n".join(current))
        return parts


def chunk_document(
    cleaned: CleanedDocument,
    *,
    company_name: str,
    token_counter: TokenCounter | None = None,
    settings: Settings | None = None,
) -> list[ChunkDraft]:
    """Return a complete deterministic chunk list or raise a safe typed error.

    Tables are hard segment barriers. Lead-in paragraphs are kept as prose;
    attachment to a table is optional in the approved Phase 4 contract.
    """
    config = settings or get_settings()
    if token_counter is None:
        from app.adapters.tokenizers.bge_m3 import BgeM3TokenCounter

        token_counter = BgeM3TokenCounter()
    chunker = _Chunker(config, token_counter)
    result: list[ChunkDraft] = []
    for ordinal, block in enumerate(_blocks(cleaned)):
        header = (
            f"Company: {_single_line(company_name)}\n"
            f"Document: {_single_line(cleaned.filename)}\n"
            f"Section: {block.section}"
        )
        header_count = chunker.count(header)
        header_limit = math.floor(config.CHUNK_TOKENS * 0.25)
        if header_count > header_limit:
            chunker.budget_error(block, "HEADER_TOO_LARGE", header_count, header_limit)
        key: SegmentKey = (block.page, block.section, ordinal)
        bodies: list[tuple[str, SmallChunkReason | None]]
        if block.is_table:
            bodies = [(body, None) for body in chunker.tables(block, header)]
        else:
            bodies = chunker.prose(block, header)
        for part_index, (body, reason) in enumerate(bodies, 1):
            content = header + "\n\n" + body
            result.append(
                ChunkDraft(
                    chunk_index=len(result),
                    page=block.page,
                    section=block.section,
                    header=header,
                    body=body,
                    content=content,
                    token_count=chunker.count(content),
                    is_table=block.is_table,
                    table_part=f"{part_index}/{len(bodies)}"
                    if block.is_table and len(bodies) > 1
                    else None,
                    segment_key=key,
                    sub_min_reason=reason,
                )
            )
    return result
