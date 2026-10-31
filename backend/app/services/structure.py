"""Heading context for page and section units (IN-1, IN-5; SRD §8.1).

PDF heading levels depend on per-page font-size heuristics from pymupdf4llm;
they are not guaranteed to match the author's intended outline.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from app.schemas.extraction import ExtractedDocument, ExtractedUnit

_HEAD = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")


def iter_headings(markdown: str) -> Iterator[tuple[int, int, str]]:
    """Yield (zero-based line index, level, heading) in document order."""
    fenced = False
    for index, line in enumerate(markdown.splitlines()):
        if line.strip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        found = _HEAD.match(line)
        if found:
            yield index, len(found.group(1)), found.group(2).strip()


def _advance(path: list[str], level: int, title: str) -> list[str]:
    """Apply one heading, tolerating skipped levels without empty names."""
    return path[: max(0, level - 1)] + [title]


def annotate_heading_paths(document: ExtractedDocument) -> ExtractedDocument:
    """Annotate the heading path at the start of each unit across PDF pages.

    The first heading on a unit is included in its starting context when
    it precedes substantive content; subsequent headings affect later units.
    """
    path: list[str] = []
    for unit in document.units:
        headings = list(iter_headings(unit.markdown))
        first_content = next(
            (
                i
                for i, line in enumerate(unit.markdown.splitlines())
                if line.strip() and not line.strip().startswith("<!--")
            ),
            None,
        )
        start = list(path)
        if headings and first_content is not None and headings[0][0] == first_content:
            _, level, title = headings[0]
            start = _advance(path, level, title)
        unit.heading_path = start
        for _, level, title in headings:
            path = _advance(path, level, title)
    return document


def heading_context(unit: ExtractedUnit, line_index: int) -> list[str]:
    """Return the outline applicable at a line within a unit."""
    if line_index < 0:
        raise ValueError("line_index must be nonnegative")
    path = list(unit.heading_path)
    for index, level, title in iter_headings(unit.markdown):
        if index > line_index:
            break
        path = _advance(path, level, title)
    return path
