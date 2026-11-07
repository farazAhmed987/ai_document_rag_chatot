"""Strict verification contracts: never assemble a fact across chunks or rows."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from app.core.config import REPO_ROOT


def verifier() -> ModuleType:
    path = REPO_ROOT / "scripts" / "verify_chunks.py"
    spec = importlib.util.spec_from_file_location("phase4_verify_chunks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_fact_snippets_must_share_one_chunk() -> None:
    module = verifier()
    assert not module.matches_one_chunk(
        ["First fact.", "Second fact."], ["First fact.", "Second fact."]
    )
    assert module.matches_one_chunk(
        ["First fact. Second fact."], ["First fact.", "Second fact."]
    )


def test_table_cells_must_share_one_markdown_row() -> None:
    module = verifier()
    snippet = "| Fee | PKR 500 | In advance |"
    assert not module.matches_one_chunk(
        ["| Fee | wrong |\n| other | PKR 500 | In advance |"], [snippet]
    )
    assert module.matches_one_chunk([snippet], [snippet])


def test_cross_document_targets_include_all_related_documents() -> None:
    module = verifier()
    assert module.required_documents(
        {
            "document": "A.md",
            "type": "cross_doc",
            "related_documents": ["B.md", "C.md", "B.md"],
        }
    ) == ["A.md", "B.md", "C.md"]


def test_verifier_distinguishes_absent_from_split_fact() -> None:
    module = verifier()
    assert module.match_status(["A", "B"], ["A", "B"]) == "split_between_chunks"
    assert module.match_status(["A"], ["B"]) == "missing_snippet_in_document"


def test_verifier_does_not_overwrite_canonical_fact_data(tmp_path: Path) -> None:
    module = verifier()
    original = (REPO_ROOT / "data/facts/key_facts.json").read_bytes()
    assert module.main(["--docs-dir", str(tmp_path)]) == 1
    assert (REPO_ROOT / "data/facts/key_facts.json").read_bytes() == original
