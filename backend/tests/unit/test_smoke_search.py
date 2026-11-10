"""Unit tests for smoke_search script logic, question selection, matching, and safety (IN-1..IN-8)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SMOKE_SCRIPT_PATH = REPO_ROOT / "scripts" / "smoke_search.py"
GOLDEN_PATH = REPO_ROOT / "eval" / "golden_set_draft.json"


def _load_smoke_module() -> ModuleType:
    """Load scripts/smoke_search.py dynamically."""
    if not SMOKE_SCRIPT_PATH.exists():
        raise FileNotFoundError(f"Script not found at {SMOKE_SCRIPT_PATH}")
    spec = importlib.util.spec_from_file_location("smoke_search", SMOKE_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {SMOKE_SCRIPT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["smoke_search"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_smoke_search_question_selection_satisfies_category_allocation() -> None:
    """Smoke search selects exactly six distinct questions matching category rules:

    3 English direct, 1 table, 1 multi-doc, 1 Roman Urdu.
    """
    mod = _load_smoke_module()
    questions = mod.select_smoke_questions(GOLDEN_PATH)
    assert len(questions) == 6

    ids = [q["id"] for q in questions]
    assert len(set(ids)) == 6, "All 6 question IDs must be distinct"

    direct_en = [
        q for q in questions if q.get("type") == "direct" and q.get("language") == "en"
    ]
    assert len(direct_en) == 3, (
        f"Expected 3 English direct questions, got {len(direct_en)}"
    )

    tables = [q for q in questions if q.get("type") == "table"]
    assert len(tables) == 1, f"Expected 1 table question, got {len(tables)}"

    multi_docs = [q for q in questions if q.get("type") in ("multi_doc", "multi_part")]
    assert len(multi_docs) == 1, f"Expected 1 multi-doc question, got {len(multi_docs)}"

    roman_urdu = [
        q
        for q in questions
        if q.get("type") == "roman_urdu"
        or q.get("language") in ("ur-Latn", "roman_urdu")
    ]
    assert len(roman_urdu) == 1, (
        f"Expected 1 Roman Urdu question, got {len(roman_urdu)}"
    )


def test_smoke_search_question_selection_is_stable_and_deterministic() -> None:
    """Question selection returns identical IDs and order across repeated invocations."""
    mod = _load_smoke_module()
    q1 = mod.select_smoke_questions(GOLDEN_PATH)
    q2 = mod.select_smoke_questions(GOLDEN_PATH)
    assert [q["id"] for q in q1] == [q["id"] for q in q2]
    assert mod.SELECTED_QUESTION_IDS == tuple(q["id"] for q in q1)


def test_smoke_search_matching_normalizes_doc_name_and_page() -> None:
    """Matching handles .md vs .pdf stems and checks page when specified."""
    mod = _load_smoke_module()

    # Case 1: Matching document stem (Company_Overview.md vs Company_Overview.pdf)
    expected_sources = [{"document": "Company_Overview.md", "section": "1. About"}]
    top_candidates = [
        {"filename": "Company_Overview.pdf", "page": 1, "score": 0.92},
        {"filename": "FAQs.pdf", "page": 2, "score": 0.81},
    ]
    hit = mod.evaluate_hit(expected_sources, top_candidates)
    assert hit is True

    # Case 2: Matching with page requirement
    expected_with_page = [{"document": "Services_and_Fees.md", "page": 3}]
    candidates_wrong_page = [
        {"filename": "Services_and_Fees.pdf", "page": 1, "score": 0.90},
    ]
    assert mod.evaluate_hit(expected_with_page, candidates_wrong_page) is False

    candidates_right_page = [
        {"filename": "Services_and_Fees.pdf", "page": 3, "score": 0.88},
    ]
    assert mod.evaluate_hit(expected_with_page, candidates_right_page) is True


def test_smoke_search_retrieval_miss_reports_miss_without_failure() -> None:
    """A retrieval miss reports hit_at_5=False but does not cause run failure."""
    mod = _load_smoke_module()
    expected_sources = [{"document": "Nonexistent_Doc.md"}]
    top_candidates = [
        {"filename": "Company_Overview.pdf", "page": 1, "score": 0.50},
    ]
    hit = mod.evaluate_hit(expected_sources, top_candidates)
    assert hit is False

    # Evaluation summary marks hit=False and produces status
    row = mod.build_result_row(
        question={
            "id": "Q999",
            "type": "direct",
            "language": "en",
            "expected_sources": expected_sources,
        },
        top_candidates=top_candidates,
    )
    assert row["hit_at_5"] is False
    assert row["question_id"] == "Q999"


def test_smoke_search_format_table_contains_required_fields() -> None:
    """Formatted report table contains ID, category, expected source, hit status, and ranks."""
    mod = _load_smoke_module()
    sample_rows = [
        {
            "question_id": "Q001",
            "category": "direct (en)",
            "expected": "Company_Overview.md",
            "hit_at_5": True,
            "ranked_sources": ["Company_Overview.pdf (p.1)", "FAQs.pdf (p.2)"],
        },
        {
            "question_id": "Q011",
            "category": "table (en)",
            "expected": "Services_and_Fees.md",
            "hit_at_5": False,
            "ranked_sources": ["FAQs.pdf (p.1)"],
        },
    ]
    formatted = mod.format_smoke_table(sample_rows)
    assert "Q001" in formatted
    assert "Q011" in formatted
    assert "direct (en)" in formatted
    assert "table (en)" in formatted
    assert "HIT" in formatted
    assert "MISS" in formatted
    assert "Company_Overview" in formatted


def test_smoke_search_query_and_result_ordering_deterministic() -> None:
    """Processing sequence strictly preserves selected question ordering."""
    mod = _load_smoke_module()
    fake_questions = [
        {"id": "Q003", "type": "direct", "language": "en", "expected_sources": []},
        {"id": "Q001", "type": "direct", "language": "en", "expected_sources": []},
    ]
    rows = [
        mod.build_result_row(q, [{"filename": "doc.pdf", "page": 1, "score": 0.9}])
        for q in fake_questions
    ]
    assert [r["question_id"] for r in rows] == ["Q003", "Q001"]


def test_smoke_search_help_works_without_torch_or_loading_model() -> None:
    """Invoking scripts/smoke_search.py --help must succeed without importing torch."""
    res = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "usage:" in res.stdout.lower() or "options:" in res.stdout.lower()
    assert "smoke" in res.stdout.lower()


def test_smoke_search_errors_mask_database_url_and_document_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When an error occurs during smoke search, sensitive tokens and URLs are never printed."""
    mod = _load_smoke_module()

    raw_password = "SUPER_SECRET_DB_PASSWORD_12345"
    db_url_string = f"postgresql+psycopg://user:{raw_password}@dbhost:5432/rag_app"
    secret_body = "CONFIDENTIAL_DOCUMENT_BODY_998877"

    mock_engine = MagicMock()
    mock_engine.connect.side_effect = RuntimeError(
        f"Failed to connect: {db_url_string} with {secret_body}"
    )

    with pytest.raises(SystemExit) as exc_info:
        mod.run_smoke_search(
            company_name="FakeCompany",
            golden_path=GOLDEN_PATH,
            engine_override=mock_engine,
        )
    assert exc_info.value.code != 0

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert raw_password not in combined
    assert "dbhost" not in combined
    assert secret_body not in combined
