"""A-6 primary and source-specific related evidence verification (IN-4, IN-5).

Tokenizer prepared via scripts/embed_benchmark.py --prepare-model. Normal verification is offline.
Never rewrites canonical facts/page maps. JSON output requires --report explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from app.adapters.extractors.factory import extract_document
from app.adapters.tokenizers.bge_m3 import BgeM3TokenCounter
from app.core.config import REPO_ROOT, Settings, get_settings
from app.core.errors import AppError
from app.schemas.chunking import ChunkDraft, SegmentKey
from app.services.chunking import TokenCounter, chunk_document
from app.services.cleaning import clean_document

DEFAULT_DOCS = REPO_ROOT / "data/sample_docs"
DEFAULT_FACTS = REPO_ROOT / "data/facts/key_facts.json"
DEFAULT_EVIDENCE = REPO_ROOT / "data/facts/cross_doc_evidence.json"
DEFAULT_SOURCES = REPO_ROOT / "data/sample_docs_src"
DEFAULT_GOLDEN = REPO_ROOT / "eval/golden_set_draft.json"
COMPANY = "Himalaya Crest Realty (Pvt.) Ltd."


def _fact_helpers() -> ModuleType:
    path = REPO_ROOT / "scripts/verify_facts.py"
    spec = importlib.util.spec_from_file_location("phase4_fact_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the existing strict fact verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FACTS = _fact_helpers()


def _snippet_matches(body: str, snippet: str) -> bool:
    if "|" in snippet:
        return bool(_FACTS.ordered_table_row_match(body, snippet))
    target = str(_FACTS.normalize(snippet))
    return bool(target) and target in str(_FACTS.normalize(body))


def matches_one_chunk(bodies: Sequence[str], snippets: Sequence[str]) -> bool:
    """Require ALL snippets in ONE body, never in headers or concatenated chunks."""
    return bool(snippets) and any(
        all(_snippet_matches(body, snippet) for snippet in snippets) for body in bodies
    )


def match_status(bodies: Sequence[str], snippets: Sequence[str]) -> str:
    if matches_one_chunk(bodies, snippets):
        return "matched"
    if all(
        any(_snippet_matches(body, snippet) for body in bodies) for snippet in snippets
    ):
        return "split_between_chunks"
    return "missing_snippet_in_document"


def required_documents(fact: Mapping[str, Any]) -> list[str]:
    names = [str(fact["document"])]
    if fact.get("type") == "cross_doc":
        names.extend(str(name) for name in fact.get("related_documents", []))
    return list(dict.fromkeys(names))


def check_invariants(
    chunks: Sequence[ChunkDraft],
    config: Settings,
    counter: TokenCounter,
) -> tuple[list[str], list[dict[str, object]]]:
    errors: list[str] = []
    exceptions: list[dict[str, object]] = []
    groups: dict[SegmentKey, list[ChunkDraft]] = defaultdict(list)
    if [chunk.chunk_index for chunk in chunks] != list(range(len(chunks))):
        errors.append("noncontiguous_chunk_indices")
    for chunk in chunks:
        if not chunk.body.strip():
            errors.append(f"chunk={chunk.chunk_index}:empty_body")
        if chunk.content != chunk.header + "\n\n" + chunk.body:
            errors.append(f"chunk={chunk.chunk_index}:invalid_content_header")
        if chunk.token_count != counter.count(chunk.content):
            errors.append(f"chunk={chunk.chunk_index}:incorrect_token_count")
        limit = (
            config.MAX_TABLE_TOKENS
            if chunk.is_table
            else config.CHUNK_TOKENS * 115 // 100
        )
        if chunk.token_count > limit:
            errors.append(f"chunk={chunk.chunk_index}:token_limit_exceeded")
        if not chunk.is_table:
            groups[chunk.segment_key].append(chunk)
    for key, group in groups.items():
        for chunk in group:
            body_count = counter.count(chunk.body)
            if body_count >= config.MIN_CHUNK_TOKENS:
                continue
            reason = chunk.sub_min_reason
            valid = reason == "unit_indivisible" and len(group) > 1
            valid = valid or reason == "sole_chunk_of_segment" and len(group) == 1
            exceptions.append(
                {
                    "chunk_index": chunk.chunk_index,
                    "page": chunk.page,
                    "section": chunk.section,
                    "segment_ordinal": key[2],
                    "body_tokens": body_count,
                    "reason": reason,
                    "valid": valid,
                }
            )
            if not valid:
                errors.append(f"chunk={chunk.chunk_index}:unjustified_subminimum")
    return errors, exceptions


class EvidenceMapError(ValueError):
    """An evidence map cannot be accepted as a complete, source-bound oracle."""

    def __init__(
        self, issues: Sequence[str], missing_pairs: Sequence[tuple[str, str]] = ()
    ) -> None:
        self.issues = sorted(set(issues))
        self.missing_pairs = sorted(set(missing_pairs))
        super().__init__("; ".join(self.issues))


def expected_related_pairs(facts: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """All declared relations, including those on single/table facts (A-6)."""
    return sorted(
        {
            (str(fact["id"]), Path(str(name)).stem)
            for fact in facts
            for name in fact.get("related_documents", [])
        }
    )


def anchor_matches_snippet(anchor: str, snippet: str) -> bool:
    """Match a normalized anchor without accepting a substring of a number."""
    normalized_anchor = str(_FACTS.normalize(anchor))
    normalized_snippet = str(_FACTS.normalize(snippet))
    if not normalized_anchor:
        return False
    left_boundary = r"(?<![\d,.])" if normalized_anchor[0].isdigit() else ""
    right_boundary = r"(?![\d,])" if normalized_anchor[-1].isdigit() else ""
    pattern = re.compile(
        f"{left_boundary}{re.escape(normalized_anchor)}{right_boundary}"
    )
    return pattern.search(normalized_snippet) is not None


def validate_evidence_map(
    raw: object,
    facts: Sequence[Mapping[str, Any]],
    *,
    key_facts_sha256: str,
    source_sha256: Mapping[str, str],
    require_complete: bool = True,
) -> list[dict[str, Any]]:
    """Strict integrity validation; semantic support still requires Claude review.

    require_complete=False is used ONLY to collect diagnostics for a map already
    marked invalid for missing pairs. It never changes the final acceptance gate.
    """
    if not isinstance(raw, dict):
        raise EvidenceMapError(["map_not_object"])
    issues: list[str] = []
    if type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1:
        issues.append("unsupported_schema_version")
    version = raw.get("map_version")
    if not isinstance(version, str) or not re.fullmatch(
        r"1\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version
    ):
        issues.append("invalid_or_unsupported_map_version")
    if raw.get("key_facts_sha256") != key_facts_sha256:
        issues.append("stale_key_facts_sha256")
    declared = raw.get("source_sha256")
    if not isinstance(declared, dict) or declared != dict(source_sha256):
        issues.append("stale_or_incomplete_source_sha256")
    if set(raw) - {
        "schema_version",
        "map_version",
        "key_facts_sha256",
        "source_sha256",
        "entries",
    }:
        issues.append("unknown_top_level_fields")
    rows = raw.get("entries")
    if not isinstance(rows, list):
        issues.append("entries_not_array")
        rows = []
    expected = set(expected_related_pairs(facts))
    known_facts = {str(fact["id"]) for fact in facts}
    known_docs = set(source_sha256)
    seen: set[tuple[str, str]] = set()
    entries: list[dict[str, Any]] = []
    required_fields = {
        "fact_id",
        "document",
        "evidence_type",
        "snippets",
        "anchor",
        "note",
    }
    for index, row in enumerate(rows):
        label = f"entry_{index}"
        if not isinstance(row, dict):
            issues.append(f"{label}:not_object")
            continue
        if set(row) != required_fields:
            issues.append(f"{label}:invalid_fields")
        fact_id, doc = row.get("fact_id"), row.get("document")
        if not isinstance(fact_id, str) or fact_id not in known_facts:
            issues.append(f"{label}:unknown_fact_id")
        if not isinstance(doc, str) or doc not in known_docs:
            issues.append(f"{label}:unknown_document")
        if not isinstance(fact_id, str) or not isinstance(doc, str):
            continue
        pair = (fact_id, doc)
        if pair in seen:
            issues.append(f"duplicate_pair:{fact_id}:{doc}")
        seen.add(pair)
        if pair not in expected:
            issues.append(f"extra_pair:{fact_id}:{doc}")
        kind = row.get("evidence_type")
        if not isinstance(kind, str) or kind not in {"prose", "table_row"}:
            issues.append(f"{label}:invalid_evidence_type")
        snippets = row.get("snippets")
        valid_snippets = (
            isinstance(snippets, list)
            and bool(snippets)
            and all(
                isinstance(value, str) and bool(_FACTS.normalize(value))
                for value in snippets
            )
        )
        if not valid_snippets:
            issues.append(f"{label}:invalid_snippets")
        anchor = row.get("anchor")
        if not isinstance(anchor, str) or not _FACTS.normalize(anchor):
            issues.append(f"{label}:empty_anchor")
        elif valid_snippets and not any(
            anchor_matches_snippet(anchor, value) for value in snippets
        ):
            issues.append(f"{label}:anchor_missing_from_snippets")
        note = row.get("note")
        if (
            not isinstance(note, str)
            or not note.strip()
            or "\n" in note
            or "\r" in note
        ):
            issues.append(f"{label}:invalid_note")
        if (
            kind == "table_row"
            and valid_snippets
            and any(
                len(value.splitlines()) != 1
                or not value.strip().startswith("|")
                or value.count("|") < 2
                or not re.search(r"[\w]", value)
                for value in snippets
            )
        ):
            issues.append(f"{label}:table_evidence_not_one_data_row")
        entries.append(dict(row))
    missing = sorted(expected - seen)
    if require_complete:
        issues.extend(f"missing_pair:{fact_id}:{doc}" for fact_id, doc in missing)
    if issues:
        raise EvidenceMapError(issues, missing)
    return sorted(entries, key=lambda row: (row["fact_id"], row["document"]))


def _table_set_matches(text: str, snippets: Sequence[str]) -> bool:
    """ALL table snippets must match ONE original row; no row/chunk joins."""
    return bool(snippets) and any(
        all(bool(_FACTS.ordered_table_row_match(line, snippet)) for snippet in snippets)
        for line in text.splitlines()
        if line.lstrip().startswith("|")
    )


def check_related_entry(
    entry: Mapping[str, Any],
    source_units: Sequence[str],
    chunk_bodies: Sequence[str],
) -> dict[str, Any]:
    """Check source existence (a) independently from actual-chunk retention (b)."""
    snippets = entry["snippets"]
    if entry["evidence_type"] == "table_row":
        source_present = any(
            _table_set_matches(unit, snippets) for unit in source_units
        )
        one_chunk_present = any(
            _table_set_matches(body, snippets) for body in chunk_bodies
        )
    else:
        # Joining cleaned SOURCE units is permitted for source-existence only.
        # Chunk bodies are NEVER joined, including for diagnostics.
        source_present = matches_one_chunk(["\n\n".join(source_units)], snippets)
        one_chunk_present = matches_one_chunk(chunk_bodies, snippets)
    failed = []
    if not source_present:
        failed.append("a_source")
    if not one_chunk_present:
        failed.append("b_one_chunk")
    return {
        "source_present": source_present,
        "one_chunk_present": one_chunk_present,
        "matched": source_present and one_chunk_present,
        "failing_checks": failed,
    }


def evaluate_related(
    entries: Sequence[Mapping[str, Any]],
    expected: Sequence[tuple[str, str]],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One result per declared pair; every available source variant must pass."""
    index = {(str(row["fact_id"]), str(row["document"])): row for row in entries}
    output: list[dict[str, Any]] = []
    for pair in sorted(set(expected)):
        fact_id, doc = pair
        row: dict[str, Any] = {"fact_id": fact_id, "document": doc}
        if pair not in index:
            output.append(
                {
                    **row,
                    "source_present": None,
                    "one_chunk_present": None,
                    "matched": False,
                    "failing_checks": ["map_missing"],
                    "artifacts": [],
                }
            )
            continue
        names = sorted(name for name in artifacts if Path(name).stem == doc)
        if not names:
            output.append(
                {
                    **row,
                    "source_present": False,
                    "one_chunk_present": False,
                    "matched": False,
                    "failing_checks": ["document_missing"],
                    "artifacts": [],
                }
            )
            continue
        checks = [
            {
                "artifact": name,
                **check_related_entry(
                    index[pair], artifacts[name]["source"], artifacts[name]["bodies"]
                ),
            }
            for name in names
        ]
        source_present = all(check["source_present"] for check in checks)
        one_chunk_present = all(check["one_chunk_present"] for check in checks)
        output.append(
            {
                **row,
                "source_present": source_present,
                "one_chunk_present": one_chunk_present,
                "matched": source_present and one_chunk_present,
                "failing_checks": sorted(
                    {failure for check in checks for failure in check["failing_checks"]}
                ),
                "artifacts": checks,
            }
        )
    return output


def golden_unmapped_sources(
    questions: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Document coverage information for Phase 7, never a gate."""
    mapped = {str(entry["document"]) for entry in entries}
    result: list[dict[str, Any]] = []
    for question in sorted(questions, key=lambda row: str(row.get("id", ""))):
        if question.get("type") != "multi_doc":
            continue
        missing = sorted(
            {
                Path(str(source["document"])).stem
                for source in question.get("expected_sources", [])
            }
            - mapped
        )
        if missing:
            result.append({"id": str(question["id"]), "documents": missing})
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceMapError([f"duplicate_json_key:{key}"])
        result[key] = value
    return result


def _read_map(path: Path) -> object:
    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
    )


def _write_report(path: Path | None, payload: Mapping[str, Any]) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _unsupported_pairs(
    missing: Sequence[tuple[str, str]],
    facts: Sequence[Mapping[str, Any]],
    source_text: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Search original source only; absence of a map is NOT semantic proof."""
    lookup = {str(fact["id"]): fact for fact in facts}
    output: list[dict[str, Any]] = []
    pattern = re.compile(
        r"[\w.+-]+@[\w.-]+|\+92(?:[ ]\d+){2,4}|HCR-[A-Z]{3}-\d{4}|\b\d{1,2} [A-Z][a-z]+ \d{4}|\b\d+(?:\.\d+)?%|[A-Z][a-z]+ [A-Z][a-z]+"
    )
    for fact_id, doc in sorted(missing):
        fact = lookup[fact_id]
        terms = sorted(
            {*fact.get("snippets", []), *pattern.findall(str(fact.get("fact", "")))}
        )
        normalized_source = str(_FACTS.normalize(source_text.get(doc, "")))
        output.append(
            {
                "fact_id": fact_id,
                "document": doc,
                "reason": "no_approved_source_specific_evidence; semantic/data review required",
                "searches_tried": [
                    {
                        "query": term,
                        "found": str(_FACTS.normalize(term)) in normalized_source,
                    }
                    for term in terms
                ],
            }
        )
    return output


def verify(
    *,
    docs_dir: Path,
    facts_path: Path,
    report_path: Path | None = None,
    counter: TokenCounter | None = None,
    config: Settings | None = None,
    evidence_path: Path = DEFAULT_EVIDENCE,
    source_dir: Path = DEFAULT_SOURCES,
    golden_path: Path = DEFAULT_GOLDEN,
    expected_map_sha256: str | None = None,
) -> bool:
    paths = sorted(
        path
        for path in docs_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".txt", ".md"}
    )
    if not paths:
        print("FAIL: no sample documents found")
        return False
    facts: list[dict[str, Any]] = _FACTS.load_facts(facts_path)
    source_paths = sorted(source_dir.glob("*.md"))
    hashes = {
        path.stem: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_paths
    }
    source_text = {path.stem: path.read_text(encoding="utf-8") for path in source_paths}
    pairs = expected_related_pairs(facts)
    map_errors: list[str] = []
    missing_pairs: list[tuple[str, str]] = []
    try:
        raw_map = _read_map(evidence_path)
        map_digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        if expected_map_sha256 is not None and map_digest != expected_map_sha256:
            raise EvidenceMapError(["frozen_evidence_map_sha256_mismatch"])
        try:
            entries = validate_evidence_map(
                raw_map,
                facts,
                key_facts_sha256=hashlib.sha256(facts_path.read_bytes()).hexdigest(),
                source_sha256=hashes,
            )
        except EvidenceMapError as exc:
            if not exc.missing_pairs or any(
                not issue.startswith("missing_pair:") for issue in exc.issues
            ):
                raise
            # Strict validation has FAILED. Audit the valid entries for useful
            # diagnostics, but missing pairs still fail the final gate.
            map_errors = exc.issues
            missing_pairs = exc.missing_pairs
            entries = validate_evidence_map(
                raw_map,
                facts,
                key_facts_sha256=hashlib.sha256(facts_path.read_bytes()).hexdigest(),
                source_sha256=hashes,
                require_complete=False,
            )
    except (EvidenceMapError, OSError, ValueError) as exc:
        errors = (
            exc.issues if isinstance(exc, EvidenceMapError) else [type(exc).__name__]
        )
        for issue in errors:
            print(f"EVIDENCE MAP FAIL: {issue}")
        _write_report(
            report_path,
            {
                "passed": False,
                "evidence_map_valid": False,
                "map_validation_errors": errors,
                "primary": {"matched": None, "total": len(facts), "status": "not_run"},
                "related": {"matched": None, "total": len(pairs), "status": "not_run"},
            },
        )
        return False
    counter = counter or BgeM3TokenCounter()
    config = config or get_settings()
    artifacts: dict[str, dict[str, Any]] = {}
    metrics: list[dict[str, Any]] = []
    failures: list[str] = []
    subminimum: list[dict[str, object]] = []
    for path in paths:
        name = str(path.relative_to(docs_dir))
        source_units: list[str] = []
        try:
            extracted = extract_document(path)
            cleaned = clean_document(extracted)
            source_units = [unit.markdown for unit in cleaned.units]
            chunks = chunk_document(
                cleaned, company_name=COMPANY, token_counter=counter, settings=config
            )
        except AppError as exc:
            print(f"PIPELINE FAIL: {name}: {exc.code} {exc.details}")
            failures.append(f"{name}:{exc.code}")
            artifacts[name] = {"source": source_units, "bodies": []}
            continue
        problems, exceptions = check_invariants(chunks, config, counter)
        failures.extend(f"{name}:{problem}" for problem in problems)
        subminimum.extend({"document": name, **item} for item in exceptions)
        artifacts[name] = {
            "source": source_units,
            "bodies": [chunk.body for chunk in chunks],
        }
        counts = sorted(chunk.token_count for chunk in chunks)
        metrics.append(
            {
                "document": name,
                "pages": extracted.page_count,
                "chunks": len(chunks),
                "token_min": min(counts, default=0),
                "token_median": statistics.median(counts) if counts else 0,
                "token_p95": counts[max(0, math.ceil(len(counts) * 0.95) - 1)]
                if counts
                else 0,
                "token_max": max(counts, default=0),
                "non_table_max": max(
                    (chunk.token_count for chunk in chunks if not chunk.is_table),
                    default=0,
                ),
                "tables_split": sum(
                    bool(
                        chunk.is_table
                        and chunk.table_part
                        and chunk.table_part.startswith("1/")
                    )
                    for chunk in chunks
                ),
            }
        )
    # The real gate requires primary PDFs, as well as checking available variants.
    for doc in sorted({Path(str(fact["document"])).stem for fact in facts}):
        if f"{doc}.pdf" not in artifacts:
            failures.append(f"missing_primary_pdf:{doc}")
    for variant in (
        "variants/FAQs.md",
        "variants/FAQs.txt",
        "variants/Services_and_Fees.docx",
    ):
        if variant not in artifacts:
            failures.append(f"missing_required_variant:{variant}")
    primary_rows: list[dict[str, Any]] = []
    primary_by_artifact: dict[str, list[bool]] = defaultdict(list)
    for fact in sorted(facts, key=lambda row: str(row["id"])):
        doc = Path(str(fact["document"])).stem
        names = sorted(name for name in artifacts if Path(name).stem == doc)
        checks = [
            {
                "artifact": name,
                "matched": matches_one_chunk(
                    artifacts[name]["bodies"], fact["snippets"]
                ),
            }
            for name in names
        ]
        matched = (
            bool(names)
            and f"{doc}.pdf" in artifacts
            and all(check["matched"] for check in checks)
        )
        primary_rows.append(
            {
                "fact_id": str(fact["id"]),
                "document": doc,
                "matched": matched,
                "artifacts": checks,
            }
        )
        for check in checks:
            primary_by_artifact[check["artifact"]].append(check["matched"])
    related_rows = evaluate_related(entries, pairs, artifacts)
    related_matched = sum(row["matched"] for row in related_rows)
    primary_matched = sum(row["matched"] for row in primary_rows)
    unsupported = _unsupported_pairs(missing_pairs, facts, source_text)
    questions = (
        json.loads(golden_path.read_text(encoding="utf-8"))
        if golden_path.is_file()
        else []
    )
    golden_info = golden_unmapped_sources(questions, entries)
    passed = (
        not map_errors
        and not unsupported
        and not failures
        and primary_matched == len(facts)
        and related_matched == len(pairs)
    )
    print(
        "PRIMARY TABLE: artifact | matched/required | chunks | tokens min/median/p95/max | tables split"
    )
    for row in metrics:
        name = row["document"]
        checks = primary_by_artifact[name]
        print(
            f"{name} | {sum(checks)}/{len(checks)} | {row['chunks']} | "
            f"{row['token_min']}/{row['token_median']}/{row['token_p95']}/{row['token_max']} | {row['tables_split']}"
        )
    print(
        "RELATED TABLE: document | source (a) | one chunk (b) | both | required pairs"
    )
    for doc in sorted({row["document"] for row in related_rows}):
        rows = [row for row in related_rows if row["document"] == doc]
        print(
            f"{doc} | {sum(row['source_present'] is True for row in rows)} | "
            f"{sum(row['one_chunk_present'] is True for row in rows)} | {sum(row['matched'] for row in rows)} | {len(rows)}"
        )
    for row in primary_rows:
        if not row["matched"]:
            print(f"PRIMARY FAIL: {json.dumps(row, sort_keys=True)}")
    for row in related_rows:
        if not row["matched"]:
            print(f"RELATED FAIL: {json.dumps(row, sort_keys=True)}")
    for item in subminimum:
        print(f"SUB-MIN: {json.dumps(item, ensure_ascii=False, sort_keys=True)}")
    for issue in sorted(map_errors):
        print(f"EVIDENCE MAP FAIL: {issue}")
    for pair in unsupported:
        print(
            f"UNSUPPORTED_PAIR: {json.dumps(pair, ensure_ascii=False, sort_keys=True)}"
        )
    for issue in sorted(failures):
        print(f"PIPELINE/INVARIANT FAIL: {issue}")
    print(
        f"INFO golden multi_doc questions with unmapped source documents: {json.dumps(golden_info, sort_keys=True)}"
    )
    print(f"Primary: {primary_matched}/{len(facts)}")
    print(f"Related: {related_matched}/{len(pairs)}")
    print(f"Evidence map SHA-256: {map_digest}")
    print(f"Evidence map valid: {not map_errors}")
    print("PASS" if passed else "FAIL")
    _write_report(
        report_path,
        {
            "passed": passed,
            "evidence_map_valid": not map_errors,
            "evidence_map_sha256": map_digest,
            "map_version": raw_map["map_version"],
            "map_validation_errors": sorted(map_errors),
            "primary": {
                "matched": primary_matched,
                "total": len(facts),
                "rows": primary_rows,
            },
            "related": {
                "matched": related_matched,
                "total": len(pairs),
                "rows": related_rows,
            },
            "unsupported_pairs": unsupported,
            "documents": metrics,
            "subminimum": subminimum,
            "invariant_failures": sorted(failures),
            "golden_unmapped_sources_info": golden_info,
        },
    )
    return passed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS)
    parser.add_argument("--facts", type=Path, default=DEFAULT_FACTS)
    parser.add_argument("--evidence-map", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--expected-map-sha256")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        return (
            0
            if verify(
                docs_dir=args.docs_dir,
                facts_path=args.facts,
                report_path=args.report,
                evidence_path=args.evidence_map,
                source_dir=args.source_dir,
                golden_path=args.golden_set,
                expected_map_sha256=args.expected_map_sha256,
            )
            else 1
        )
    except Exception as exc:  # noqa: BLE001 -- redact CLI failure details
        print(f"Verification could not complete: {type(exc).__name__}; inspect locally")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
