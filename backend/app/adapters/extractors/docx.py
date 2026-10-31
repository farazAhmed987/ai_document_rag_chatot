"""DOCX extraction retaining body order and editable table semantics.

SRD §8.1 step 3 and requirement IN-1. Word headers and footers are ignored.
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.document import Document as WordDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.core.errors import CorruptFileError, EmptyDocumentError, ExtractionError
from app.schemas.extraction import ExtractedDocument, ExtractedUnit


def _cell_text(value: str) -> str:
    """Escape Markdown syntax and preserve a cell on a single line."""
    return " ".join(value.split()).replace("|", r"\|")


def _table_markdown(table: Table) -> str:
    """Convert an editable Word table to consistent Markdown pipe rows."""
    rows = [[_cell_text(cell.text) for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |"]
    lines.append("| " + " | ".join(["---"] * width) + " |")
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def _paragraph_markdown(
    paragraph: Paragraph, *, number_index: int = 1
) -> tuple[str, int | None]:
    """Render headings and lists from actual Word paragraph styles."""
    text = paragraph.text.strip()
    if not text:
        return "", None
    style = paragraph.style.name if paragraph.style is not None else ""
    heading = re.match(r"^Heading\s+([1-9])\b", style, re.IGNORECASE)
    if heading:
        level = int(heading.group(1))
        return f"{'#' * level} {text}", level
    if "List Bullet" in style or "List Paragraph" in style:
        return f"- {text}", None
    if "List Number" in style:
        return f"{number_index}. {text}", None
    return text, None


class DocxExtractor:
    """Produce one section unit per top-level Heading 1 block."""

    def extract(
        self, path: Path, *, original_filename: str | None = None
    ) -> ExtractedDocument:
        """Walk paragraphs and tables in body XML order.

        Args:
            path: Validated DOCX path.
            original_filename: Optional client-supplied display name.

        Returns:
            Original-order DOCX Markdown sections.
        """
        try:
            document: WordDocument = Document(str(path))
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError("DOCX is corrupt or unreadable") from exc

        units: list[ExtractedUnit] = []
        lines: list[str] = []
        heading_path: list[str] = []
        numbered_index = 0

        def emit() -> None:
            """Flush the current section without shifting existing indices."""
            content = "\n\n".join(lines).strip()
            if content:
                units.append(
                    ExtractedUnit(
                        kind="section",
                        index=len(units),
                        page_number=None,
                        heading_path=list(heading_path),
                        markdown=content,
                    )
                )
            lines.clear()

        try:
            for element in document.element.body.iterchildren():
                if element.tag == qn("w:p"):
                    paragraph = Paragraph(element, document)
                    is_numbered = (
                        paragraph.style is not None
                        and "List Number" in paragraph.style.name
                    )
                    numbered_index = numbered_index + 1 if is_numbered else 0
                    text, level = _paragraph_markdown(
                        paragraph, number_index=numbered_index or 1
                    )
                    if not text:
                        continue
                    if level == 1 and lines:
                        emit()
                    if level is not None:
                        heading_text = paragraph.text.strip()
                        heading_path[:] = heading_path[: level - 1]
                        heading_path.append(heading_text)
                    lines.append(text)
                elif element.tag == qn("w:tbl"):
                    table_text = _table_markdown(Table(element, document))
                    if table_text:
                        lines.append(table_text)
            emit()
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError("DOCX body extraction failed") from exc

        if not units:
            raise EmptyDocumentError("DOCX contains no body text")
        return ExtractedDocument(
            filename=original_filename or path.name,
            file_type="docx",
            units=units,
            page_count=None,
            metadata={
                "title": document.core_properties.title or "",
                "author": document.core_properties.author or "",
            },
        )
