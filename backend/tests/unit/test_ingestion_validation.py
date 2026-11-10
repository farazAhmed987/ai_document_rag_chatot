"""Unit tests for ingestion file validation and filename sanitization (DM-2, IN-1)."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from types import ModuleType

import docx
import pymupdf
import pytest

from app.core.errors import (
    CorruptFileError,
    EmptyDocumentError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)


def _load_ingestion() -> ModuleType:
    """Load future ingestion service module inside test body."""
    return importlib.import_module("app.services.ingestion")


def _load_storage() -> ModuleType:
    """Load future storage module inside test body."""
    return importlib.import_module("app.adapters.storage.local")


def test_validation_rejects_unsupported_extension(tmp_path: Path) -> None:
    """Extensions outside (.pdf, .docx, .txt, .md) must be rejected with typed error."""
    ing = _load_ingestion()
    bad_file = tmp_path / "executable.exe"
    bad_file.write_bytes(b"MZ\x90\x00Binary executable")

    with pytest.raises(UnsupportedFileTypeError) as exc_info:
        ing.validate_ingestion_file(bad_file)
    assert exc_info.value.code == "UNSUPPORTED_FILE_TYPE"


def test_validation_rejects_pdf_with_wrong_magic(tmp_path: Path) -> None:
    """Files named .pdf without %PDF- magic bytes must be rejected."""
    ing = _load_ingestion()
    fake_pdf = tmp_path / "spoofed.pdf"
    fake_pdf.write_bytes(b"This is a text file renamed to pdf.")

    with pytest.raises(UnsupportedFileTypeError) as exc_info:
        ing.validate_ingestion_file(fake_pdf)
    assert exc_info.value.code == "UNSUPPORTED_FILE_TYPE"

    # Corrupt PDF with valid magic but unparseable
    corrupt_pdf = tmp_path / "corrupt.pdf"
    corrupt_pdf.write_bytes(b"%PDF-1.4\nCorrupt PDF structure trailing...")
    with pytest.raises(CorruptFileError) as c_exc:
        ing.validate_ingestion_file(corrupt_pdf)
    assert c_exc.value.code == "CORRUPT_FILE"


def test_validation_rejects_docx_with_wrong_magic(tmp_path: Path) -> None:
    """Files named .docx that are not valid ZIP/DOCX packages must be rejected."""
    ing = _load_ingestion()
    fake_docx = tmp_path / "spoofed.docx"
    fake_docx.write_bytes(b"Not a zip file content.")

    with pytest.raises(UnsupportedFileTypeError) as exc_info:
        ing.validate_ingestion_file(fake_docx)
    assert exc_info.value.code == "UNSUPPORTED_FILE_TYPE"

    # Valid zip but missing DOCX parts
    import zipfile

    zip_not_docx = tmp_path / "not_docx.docx"
    with zipfile.ZipFile(zip_not_docx, "w") as zf:
        zf.writestr("test.txt", "not docx")
    with pytest.raises(UnsupportedFileTypeError) as z_exc:
        ing.validate_ingestion_file(zip_not_docx)
    assert z_exc.value.code == "UNSUPPORTED_FILE_TYPE"


def test_validation_rejects_empty_file(tmp_path: Path) -> None:
    """Empty 0-byte files must be rejected before storage."""
    ing = _load_ingestion()
    empty_file = tmp_path / "empty.txt"
    empty_file.write_bytes(b"")

    with pytest.raises(EmptyDocumentError) as exc_info:
        ing.validate_ingestion_file(empty_file)
    assert exc_info.value.code == "EMPTY_FILE"

    # Nonexistent file raises CorruptFileError
    with pytest.raises(CorruptFileError) as ne_exc:
        ing.validate_ingestion_file(tmp_path / "nonexistent.txt")
    assert ne_exc.value.code == "CORRUPT_FILE"


def test_validation_rejects_oversized_file(tmp_path: Path) -> None:
    """Files exceeding the configured MAX_UPLOAD_MB limit must be rejected."""
    ing = _load_ingestion()
    oversized = tmp_path / "big.txt"
    oversized.write_bytes(b"A" * (2 * 1024 * 1024))

    with pytest.raises(FileTooLargeError) as exc_info:
        ing.validate_ingestion_file(oversized, max_mb=1)
    assert exc_info.value.code == "FILE_TOO_LARGE"


def test_validation_rejects_corrupt_text(tmp_path: Path) -> None:
    """Text files containing un-decodable binary sequences must be rejected."""
    ing = _load_ingestion()
    corrupt = tmp_path / "corrupt.txt"
    corrupt.write_bytes(b"\xff\xfe\x00a\x00b\x00c\x01")

    with pytest.raises(CorruptFileError) as exc_info:
        ing.validate_ingestion_file(corrupt)
    assert exc_info.value.code == "DECODE_FAILED"


def test_validation_accepts_pdf_docx_txt_and_md(tmp_path: Path) -> None:
    """Valid PDF, DOCX, TXT and MD files must pass content validation and return ValidatedFile."""
    ing = _load_ingestion()

    # 1. Genuine minimal valid PDF
    pdf_path = tmp_path / "valid.pdf"
    pdf_doc = pymupdf.open()
    page = pdf_doc.new_page()
    page.insert_text((50, 50), "Valid sample PDF text")
    pdf_doc.save(str(pdf_path))
    pdf_doc.close()

    # 2. Genuine minimal valid DOCX
    docx_path = tmp_path / "valid.docx"
    doc = docx.Document()
    doc.add_paragraph("Valid sample DOCX paragraph")
    doc.save(str(docx_path))

    # 3. Valid UTF-8 TXT
    txt_path = tmp_path / "valid.txt"
    txt_path.write_text("Valid plain text body.", encoding="utf-8")

    # 4. Valid UTF-8 MD
    md_path = tmp_path / "valid.md"
    md_path.write_text("# Heading\nValid markdown body.", encoding="utf-8")

    for file_path in (pdf_path, docx_path, txt_path, md_path):
        validated = ing.validate_ingestion_file(file_path)
        expected_bytes = file_path.read_bytes()
        expected_sha = hashlib.sha256(expected_bytes).hexdigest()

        assert validated.path == file_path
        assert validated.filename == file_path.name
        assert validated.extension == file_path.suffix.lower()
        assert validated.size_bytes == len(expected_bytes)
        assert validated.sha256 == expected_sha


def test_filename_sanitizer_strips_directories_controls_and_leading_dots() -> None:
    """Filename sanitizer must strip path traversal, control chars, and leading dots."""
    storage = _load_storage()
    raw = "../../.hidden\x00malicious\r\nfile.pdf"
    clean = storage.sanitize_filename(raw)

    assert "/" not in clean
    assert "\\" not in clean
    assert "\x00" not in clean
    assert "\r" not in clean
    assert "\n" not in clean
    assert not clean.startswith(".")
    assert clean.endswith(".pdf")


def test_filename_sanitizer_preserves_extension_and_caps_length() -> None:
    """Filename sanitizer must preserve the extension and enforce <= 255 chars."""
    storage = _load_storage()
    long_name = "a" * 300 + ".docx"
    clean = storage.sanitize_filename(long_name)

    assert len(clean) <= 255
    assert clean.endswith(".docx")


def test_validation_sentinels_never_leak_in_errors_and_codes_are_fixed(
    tmp_path: Path,
) -> None:
    """Sentinels from filename, content or exceptions must never appear in validation errors."""
    ing = _load_ingestion()

    sentinel_body = "SENTINEL_SECRET_BODY_987654321"
    sentinel_name = "SENTINEL_LEAK_FILENAME_12345"
    sentinel_heading = "SENTINEL_HEADING_TITLE_24680"

    # 1. UNSUPPORTED_FILE_TYPE: unsupported extension with sentinel name, heading, and body
    bad_ext = tmp_path / f"{sentinel_name}.exe"
    bad_ext.write_text(
        f"# {sentinel_heading}\ncontent with {sentinel_body}", encoding="utf-8"
    )
    with pytest.raises(UnsupportedFileTypeError) as exc1:
        ing.validate_ingestion_file(bad_ext, original_filename=f"{sentinel_name}.exe")
    assert exc1.value.code == "UNSUPPORTED_FILE_TYPE"
    assert sentinel_body not in str(exc1.value)
    assert sentinel_name not in str(exc1.value)
    assert sentinel_heading not in str(exc1.value)

    # 2. CORRUPT_FILE: nonexistent file
    with pytest.raises(CorruptFileError) as exc2:
        ing.validate_ingestion_file(tmp_path / f"{sentinel_name}_nonexistent.pdf")
    assert exc2.value.code == "CORRUPT_FILE"
    assert sentinel_name not in str(exc2.value)

    # 2b. CORRUPT_FILE: text file with NUL byte
    nul_txt = tmp_path / f"{sentinel_name}_nul.txt"
    nul_txt.write_bytes(f"# {sentinel_heading}\n{sentinel_body}\x00trailing".encode())
    with pytest.raises(CorruptFileError) as exc2b:
        ing.validate_ingestion_file(nul_txt)
    assert exc2b.value.code == "CORRUPT_FILE"
    assert sentinel_body not in str(exc2b.value)
    assert sentinel_heading not in str(exc2b.value)

    # 3. EMPTY_FILE: empty 0-byte file
    empty_f = tmp_path / f"{sentinel_name}_empty.txt"
    empty_f.write_bytes(b"")
    with pytest.raises(EmptyDocumentError) as exc3:
        ing.validate_ingestion_file(empty_f)
    assert exc3.value.code == "EMPTY_FILE"
    assert sentinel_name not in str(exc3.value)

    # 4. FILE_TOO_LARGE: oversized file
    big_f = tmp_path / f"{sentinel_name}_big.txt"
    big_f.write_bytes(b"x" * (2 * 1024 * 1024))
    with pytest.raises(FileTooLargeError) as exc4:
        ing.validate_ingestion_file(big_f, max_mb=1)
    assert exc4.value.code == "FILE_TOO_LARGE"
    assert sentinel_name not in str(exc4.value)

    # 5. DECODE_FAILED: corrupt text with invalid encoding bytes
    decode_fail_f = tmp_path / f"{sentinel_name}_bad_encoding.txt"
    decode_fail_f.write_bytes(
        b"\xff\xfe\x00" + f"\n# {sentinel_heading}\n{sentinel_body}".encode()
    )
    with pytest.raises(CorruptFileError) as exc5:
        ing.validate_ingestion_file(decode_fail_f)
    assert exc5.value.code == "DECODE_FAILED"
    assert sentinel_body not in str(exc5.value)
    assert sentinel_heading not in str(exc5.value)


def test_filetype_dependency_is_pinned_exact_1_2_0() -> None:
    """filetype must be strictly pinned to ==1.2.0, not a range."""
    repo_root = Path(__file__).resolve().parents[3]
    req_txt = repo_root / "backend" / "requirements.txt"
    assert req_txt.is_file()

    content = req_txt.read_text(encoding="utf-8")
    assert "filetype==1.2.0" in content, "requirements.txt must pin filetype==1.2.0"
    assert "filetype>=" not in content, (
        "requirements.txt must not use a range for filetype"
    )
