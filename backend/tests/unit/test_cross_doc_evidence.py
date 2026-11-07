"""A-6 fail-first evidence-map integrity and independent source/chunk checks."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from copy import deepcopy
from types import ModuleType
from typing import Any

import pytest

from app.core.config import REPO_ROOT

FACTS = [
    {
        "id": "KF-001",
        "type": "single",
        "document": "A.md",
        "snippets": ["Primary fact."],
        "related_documents": ["B.md"],
    }
]
KEY_HASH = "a" * 64
SOURCE_HASHES = {"A": "b" * 64, "B": "c" * 64}


def verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "a6_verifier", REPO_ROOT / "scripts/verify_chunks.py"
    )
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def evidence() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "map_version": "1.0.0",
        "key_facts_sha256": KEY_HASH,
        "source_sha256": dict(SOURCE_HASHES),
        "entries": [
            {
                "fact_id": "KF-001",
                "document": "B",
                "evidence_type": "prose",
                "snippets": ["The commission is 2%."],
                "anchor": "2%",
                "note": "Provides the commission rate.",
            }
        ],
    }


def validate(api: ModuleType, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return api.validate_evidence_map(
        payload, FACTS, key_facts_sha256=KEY_HASH, source_sha256=SOURCE_HASHES
    )


def test_valid_map_covers_related_pairs_on_single_facts_without_mutation() -> None:
    api = verifier()
    payload = evidence()
    original = deepcopy(payload)
    assert validate(api, payload) == payload["entries"]
    assert payload == original
    assert api.expected_related_pairs(FACTS) == [("KF-001", "B")]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("map_version", "invalid"),
        ("map_version", "2.0.0"),
    ],
)
def test_schema_or_map_version_is_rejected(field: str, value: object) -> None:
    api = verifier()
    payload = evidence()
    payload[field] = value
    with pytest.raises(api.EvidenceMapError):
        validate(api, payload)


@pytest.mark.parametrize(
    "mode", ["key_facts", "source", "missing_source", "extra_source"]
)
def test_stale_or_incomplete_hash_binding_is_rejected(mode: str) -> None:
    api = verifier()
    payload = evidence()
    if mode == "key_facts":
        payload["key_facts_sha256"] = "d" * 64
    elif mode == "source":
        payload["source_sha256"]["B"] = "d" * 64
    elif mode == "missing_source":
        del payload["source_sha256"]["B"]
    else:
        payload["source_sha256"]["C"] = "d" * 64
    with pytest.raises(api.EvidenceMapError):
        validate(api, payload)


@pytest.mark.parametrize(
    "mode", ["missing", "extra", "duplicate", "unknown_fact", "unknown_document"]
)
def test_completeness_and_pair_identity_are_enforced(mode: str) -> None:
    api = verifier()
    payload = evidence()
    if mode == "missing":
        payload["entries"] = []
    elif mode == "duplicate":
        payload["entries"].append(deepcopy(payload["entries"][0]))
    elif mode == "extra":
        payload["entries"][0]["document"] = "A"
    elif mode == "unknown_fact":
        payload["entries"][0]["fact_id"] = "UNKNOWN"
    else:
        payload["entries"][0]["document"] = "UNKNOWN"
    with pytest.raises(api.EvidenceMapError):
        validate(api, payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("snippets", []),
        ("snippets", [""]),
        ("snippets", ["**"]),
        ("evidence_type", "heading"),
        ("anchor", "99%"),
        ("anchor", ""),
        ("anchor", "**"),
        ("note", ""),
    ],
)
def test_invalid_evidence_payload_is_rejected(field: str, value: object) -> None:
    api = verifier()
    payload = evidence()
    payload["entries"][0][field] = value
    with pytest.raises(api.EvidenceMapError):
        validate(api, payload)


@pytest.mark.parametrize(
    ("snippet", "matches"),
    [
        ("The fee is PKR 10,000.", True),
        ("The fee is PKR 10,000,000.", False),
        ("The value is 110,000.", False),
        ("The value is 1.10,000.", False),
    ],
)
def test_anchor_matching_uses_number_boundaries(snippet: str, matches: bool) -> None:
    api = verifier()
    assert api.anchor_matches_snippet("10,000", snippet) is matches


def test_numeric_substring_anchor_is_rejected_by_map_validation() -> None:
    api = verifier()
    payload = evidence()
    payload["entries"][0].update(
        snippets=["The valuation ceiling is PKR 10,000,000."],
        anchor="10,000",
    )
    with pytest.raises(api.EvidenceMapError) as error:
        validate(api, payload)
    assert "entry_0:anchor_missing_from_snippets" in error.value.issues


def test_numeric_anchor_followed_by_sentence_period_is_accepted() -> None:
    api = verifier()
    payload = evidence()
    payload["entries"][0].update(
        snippets=["The fee is PKR 10,000."],
        anchor="10,000",
    )
    assert validate(api, payload) == payload["entries"]


def test_table_snippet_must_be_one_markdown_row() -> None:
    api = verifier()
    payload = evidence()
    payload["entries"][0].update(
        evidence_type="table_row", snippets=["| Fee |\n| 2% |"]
    )
    with pytest.raises(api.EvidenceMapError):
        validate(api, payload)


def test_source_failure_is_separate_from_chunk_failure() -> None:
    api = verifier()
    entry = evidence()["entries"][0]
    result = api.check_related_entry(
        entry, ["No supporting text."], ["The commission is 2%."]
    )
    assert result["source_present"] is False
    assert result["one_chunk_present"] is True
    assert result["matched"] is False
    assert result["failing_checks"] == ["a_source"]


def test_present_source_split_across_chunks_fails_without_joining() -> None:
    api = verifier()
    entry = evidence()["entries"][0]
    entry["snippets"] = ["Commission is 2%.", "The 2% rate is fixed."]
    source = "Commission is 2%. The 2% rate is fixed."
    result = api.check_related_entry(
        entry, [source], ["Commission is 2%.", "The 2% rate is fixed."]
    )
    assert result["source_present"] is True
    assert result["one_chunk_present"] is False
    assert result["failing_checks"] == ["b_one_chunk"]


@pytest.mark.parametrize(
    "chunks",
    [["| Fee | other |\n| other | 2% |"], ["| Fee | other |", "| other | 2% |"]],
)
def test_table_evidence_cannot_be_assembled_across_rows_or_chunks(
    chunks: list[str],
) -> None:
    api = verifier()
    entry = evidence()["entries"][0]
    entry.update(evidence_type="table_row", snippets=["| Fee | 2% |"])
    result = api.check_related_entry(entry, ["| Fee | 2% |"], chunks)
    assert result["source_present"] is True
    assert result["one_chunk_present"] is False


def test_multiple_table_snippets_must_share_one_row_even_within_one_chunk() -> None:
    api = verifier()
    entry = evidence()["entries"][0]
    entry.update(evidence_type="table_row", snippets=["| Fee | 2% |", "| Other | 2% |"])
    result = api.check_related_entry(
        entry, ["| Fee | 2% |\n| Other | 2% |"], ["| Fee | 2% |\n| Other | 2% |"]
    )
    assert result["source_present"] is False
    assert result["one_chunk_present"] is False


def test_related_results_are_deterministically_ordered_and_variants_all_required() -> (
    None
):
    api = verifier()
    first = evidence()["entries"][0]
    second = {**first, "fact_id": "KF-002"}
    artifacts = {
        "B.txt": {"source": ["The commission is 2%."], "bodies": ["missing"]},
        "B.pdf": {
            "source": ["The commission is 2%."],
            "bodies": ["The commission is 2%."],
        },
    }
    expected = [("KF-001", "B"), ("KF-002", "B")]
    left = api.evaluate_related([second, first], expected, artifacts)
    right = api.evaluate_related(
        [first, second],
        list(reversed(expected)),
        dict(reversed(list(artifacts.items()))),
    )
    assert left == right
    assert [row["fact_id"] for row in left] == ["KF-001", "KF-002"]
    assert all(row["source_present"] and not row["one_chunk_present"] for row in left)


def test_golden_source_coverage_information_is_not_a_gate() -> None:
    api = verifier()
    questions = [
        {"id": "Q2", "type": "multi_doc", "expected_sources": [{"document": "C.md"}]},
        {"id": "Q1", "type": "multi_doc", "expected_sources": [{"document": "B.md"}]},
    ]
    assert api.golden_unmapped_sources(questions, evidence()["entries"]) == [
        {"id": "Q2", "documents": ["C"]}
    ]


def test_repository_evidence_map_has_176_exact_unique_valid_pairs() -> None:
    api = verifier()
    facts_path = REPO_ROOT / "data/facts/key_facts.json"
    map_path = REPO_ROOT / "data/facts/cross_doc_evidence.json"
    source_dir = REPO_ROOT / "data/sample_docs_src"
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    payload = json.loads(map_path.read_text(encoding="utf-8"))
    source_hashes = {
        path.stem: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(source_dir.glob("*.md"))
    }
    entries = api.validate_evidence_map(
        payload,
        facts,
        key_facts_sha256=hashlib.sha256(facts_path.read_bytes()).hexdigest(),
        source_sha256=source_hashes,
    )
    expected = set(api.expected_related_pairs(facts))
    actual = {(entry["fact_id"], entry["document"]) for entry in entries}
    assert len(entries) == 176
    assert len(actual) == 176
    assert actual == expected
    assert all(str(entry["note"]).strip() for entry in entries)
