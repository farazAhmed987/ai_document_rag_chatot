"""Local filesystem storage adapter with strict root confinement.

SRD Sections 8.1, 9; DM-3, MT-1.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal and shell injection.

    - Strips directory components for '/' and '\\'
    - Removes control characters
    - Removes leading dots
    - Caps total length at 255 while preserving extension
    - Uses safe fallback if basename becomes empty
    """
    # Remove directory separators (both POSIX and Windows)
    name = filename.replace("\\", "/").split("/")[-1]
    # Remove control characters
    name = _CONTROL_CHARS.sub("", name).strip()
    # Remove leading dots to prevent hidden files or traversal prefixes
    name = name.lstrip(".")

    # Split extension
    dot_idx = name.rfind(".")
    if dot_idx > 0:
        stem = name[:dot_idx]
        ext = name[dot_idx:]
    else:
        stem = name
        ext = ""

    # Safe fallback if stem is empty
    if not stem:
        stem = "document"

    # Enforce max 255 chars total, preserving extension
    max_stem_len = 255 - len(ext)
    if max_stem_len <= 0:
        return ext[:255]
    if len(stem) > max_stem_len:
        stem = stem[:max_stem_len]

    return f"{stem}{ext}"


class LocalStorage:
    """Local filesystem storage confined strictly to a configured root."""

    def __init__(self, root: Path | str) -> None:
        """Anchor storage root immutably."""
        self.root: Path = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_target(
        self,
        company_id: UUID,
        document_id: UUID,
        filename: str | None = None,
    ) -> Path:
        """Resolve and verify path confinement within root."""
        base_dir = self.root / str(company_id) / str(document_id)
        if filename is not None:
            clean_name = sanitize_filename(filename)
            target = base_dir / clean_name
        else:
            target = base_dir

        resolved = target.resolve()
        # Verify resolved target is within self.root
        if resolved != self.root and self.root not in resolved.parents:
            raise PermissionError(
                "Path traversal detected: target escapes storage root"
            )
        return resolved

    def store(
        self,
        company_id: UUID,
        document_id: UUID,
        filename: str,
        content: bytes | Path,
    ) -> Path:
        """Store content safely beneath <root>/<company_id>/<document_id>/<filename>."""
        comp_dir = self.root / str(company_id)
        comp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            comp_dir.chmod(0o700)
        except OSError:
            pass

        doc_dir = comp_dir / str(document_id)
        doc_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            doc_dir.chmod(0o700)
        except OSError:
            pass

        target_path = self._resolve_target(company_id, document_id, filename)

        if isinstance(content, (bytes, bytearray)):
            target_path.write_bytes(content)
        elif isinstance(content, Path):
            shutil.copyfile(content, target_path)
        else:
            raise TypeError("content must be bytes or Path")

        # Restrict permissions: 0600 (owner read/write only)
        try:
            target_path.chmod(0o600)
        except OSError:
            pass

        logger.info(
            "file_stored",
            company_id=str(company_id),
            document_id=str(document_id),
            filename=target_path.name,
        )
        return target_path

    def get_path(
        self,
        company_id: UUID,
        document_id: UUID,
        filename: str,
    ) -> Path:
        """Return the resolved path of a stored document."""
        return self._resolve_target(company_id, document_id, filename)

    def delete(
        self,
        company_id: UUID,
        document_id: UUID,
        filename: str | None = None,
    ) -> None:
        """Delete stored file or entire document directory."""
        target = self._resolve_target(company_id, document_id, filename)
        if filename is not None:
            if target.is_file():
                target.unlink()
        else:
            if target.is_dir():
                shutil.rmtree(target)

        logger.info(
            "file_deleted",
            company_id=str(company_id),
            document_id=str(document_id),
            filename=filename,
        )
