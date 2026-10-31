"""RED tests for strict single-line PDF fact verification (B7)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.core.config import REPO_ROOT


def _module():
    """Import the repository script by filename without sys.path changes."""
    path = REPO_ROOT / "scripts" / "verify_facts.py"
    spec = importlib.util.spec_from_file_location("phase_b_facts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pipeline_table_match_requires_one_row() -> None:
    """Cells spread across lines must not pass pipeline mode."""
    module = _module()
    snippet = "| Fee | PKR 500 | In advance |"
    split_rows = "| Fee | Other | Last |\n| Other | PKR 500 | In advance |"
    assert module.match_snippet([split_rows], snippet, mode="pipeline") == (
        [],
        "not_found",
    )


def test_pipeline_table_row_mode_and_raw_compatibility() -> None:
    """The same ordered row should match using the new table_row mode."""
    module = _module()
    snippet = "| Fee | PKR 500 | In advance |"
    row = "| Fee | PKR 500 | In advance |"
    assert module.match_snippet([row], snippet, mode="pipeline") == ([1], "table_row")
    assert (
        module.match_snippet(["Fee\nPKR 500\nIn advance"], snippet, mode="raw")[1]
        == "table_cells"
    )


def test_page_map_comparator_lists_mismatched_fact_ids() -> None:
    """Emit the fact ID and snippet where raw and pipeline pages differ."""
    path = REPO_ROOT / "scripts" / "compare_page_maps.py"
    spec = importlib.util.spec_from_file_location("phase_b_maps", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    left = {"KF-001": {"pages": [2], "snippets": [{"snippet": "Sample fee"}]}}
    right = {"KF-001": {"pages": [3]}}
    differences = module.compare(left, right)
    assert len(differences) == 1
    assert "KF-001" in differences[0]
    assert "Sample fee" in differences[0]


def test_raw_map_writes_and_pipeline_default_does_not(
    tmp_path: Path, monkeypatch
) -> None:
    """Pipeline verification cannot overwrite the committed raw page map."""
    module = _module()
    docs_dir = tmp_path / "pdfs"
    docs_dir.mkdir()
    (docs_dir / "QA.pdf").write_bytes(b"%PDF-test")
    facts_path = tmp_path / "facts.json"
    facts_path.write_text("[]", encoding="utf-8")
    target_map = tmp_path / "raw_page_map.json"
    monkeypatch.setattr(module, "DEFAULT_PAGE_MAP", target_map)
    monkeypatch.setattr(module, "REQUIRED_TABLE_DOCUMENTS", set())
    monkeypatch.setattr(
        module,
        "load_facts",
        lambda path: [
            {"id": "KF-001", "document": "QA.md", "snippets": ["Known fact"]}
        ],
    )
    monkeypatch.setattr(
        module, "inspect_pdf", lambda path: {"pages": 1, "text_pages": 1, "tables": 0}
    )
    monkeypatch.setattr(module, "extract_pages", lambda path, mode: ["Known fact"])
    assert module.verify(
        docs_dir=docs_dir, facts_path=facts_path, extractor="pipeline", report_path=None
    )
    assert not target_map.exists()
    assert module.verify(
        docs_dir=docs_dir, facts_path=facts_path, extractor="raw", report_path=None
    )
    assert target_map.is_file()
