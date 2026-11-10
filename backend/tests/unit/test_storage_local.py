"""Unit tests for local file storage layout and path confinement (DM-3, SRD 8.1)."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from uuid import uuid4


def _load_storage_modules() -> tuple[ModuleType, ModuleType]:
    """Load future storage protocol and implementation modules inside test body."""
    base_mod = importlib.import_module("app.adapters.storage.base")
    local_mod = importlib.import_module("app.adapters.storage.local")
    return base_mod, local_mod


def test_local_storage_confines_resolved_path_to_root(tmp_path: Path) -> None:
    """Storage must prevent path traversal escaping the configured root."""
    base_mod, local_mod = _load_storage_modules()
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    storage = local_mod.LocalStorage(storage_root)
    assert isinstance(storage, base_mod.Storage)

    company_id = uuid4()
    document_id = uuid4()

    try:
        stored_path = storage.store(
            company_id,
            document_id,
            "../../etc/passwd",
            b"data",
        )
        assert storage_root.resolve() in stored_path.resolve().parents
    except (ValueError, PermissionError):
        pass

    try:
        got_path = storage.get_path(company_id, document_id, "../../etc/passwd")
        assert storage_root.resolve() in got_path.resolve().parents
    except (ValueError, PermissionError):
        pass

    # sanitize_filename edge cases: empty stem fallback, extension > 255
    assert local_mod.sanitize_filename("..") == "document"
    long_ext = "." + "e" * 300
    assert local_mod.sanitize_filename(f"file{long_ext}") == long_ext[:255]


def test_local_storage_uses_company_document_filename_layout(
    tmp_path: Path,
) -> None:
    """Files must be stored at <root>/<company_id>/<document_id>/<sanitized_filename>."""
    base_mod, local_mod = _load_storage_modules()
    storage_root = tmp_path / "storage"
    storage = local_mod.LocalStorage(storage_root)
    assert isinstance(storage, base_mod.Storage)

    company_id = uuid4()
    document_id = uuid4()
    filename = "test_document.pdf"
    content = b"%PDF-1.4 test payload"

    stored_path = storage.store(company_id, document_id, filename, content)
    expected_path = storage_root / str(company_id) / str(document_id) / filename

    assert Path(stored_path).resolve() == expected_path.resolve()
    assert expected_path.is_file()
    assert expected_path.read_bytes() == content

    # Test storing from a Path source
    src_file = tmp_path / "source.pdf"
    src_file.write_bytes(content)
    stored_copy = storage.store(company_id, document_id, "copy.pdf", src_file)
    assert stored_copy.is_file()

    # Test invalid content type raises TypeError
    import pytest

    with pytest.raises(TypeError):
        storage.store(company_id, document_id, filename, 12345)  # type: ignore[arg-type]

    # Test delete with filename
    storage.delete(company_id, document_id, filename)
    assert not expected_path.exists()

    # Test delete without filename removes entire directory
    storage.delete(company_id, document_id)
    assert not expected_path.parent.exists()


def test_storage_directory_and_file_permissions(tmp_path: Path) -> None:
    """Storage files must use mode 0600 and directories must use mode 0700."""
    _, local_mod = _load_storage_modules()
    storage_root = tmp_path / "storage"
    storage = local_mod.LocalStorage(storage_root)

    company_id = uuid4()
    document_id = uuid4()
    filename = "secure_doc.pdf"
    content = b"%PDF-1.4 confidential payload"

    stored_path = storage.store(company_id, document_id, filename, content)
    assert stored_path.is_file()

    # File mode must be 0600 (read/write by owner only)
    file_mode = stored_path.stat().st_mode & 0o777
    assert file_mode == 0o600, f"Expected file mode 0600, got {oct(file_mode)}"

    # Directory modes must be 0700 (read/write/execute by owner only)
    doc_dir = stored_path.parent
    comp_dir = doc_dir.parent
    doc_dir_mode = doc_dir.stat().st_mode & 0o777
    comp_dir_mode = comp_dir.stat().st_mode & 0o777
    assert doc_dir_mode == 0o700, f"Expected doc dir mode 0700, got {oct(doc_dir_mode)}"
    assert comp_dir_mode == 0o700, (
        f"Expected comp dir mode 0700, got {oct(comp_dir_mode)}"
    )
