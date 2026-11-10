"""Deterministic chunk fingerprint harness for Phase 4 baseline validation (IN-4, IN-6).

Extracts chunks through scripts/embed_benchmark.py::sample_chunks, counts tokens
offline with BgeM3TokenCounter, and calculates SHA-256 over canonical chunk rows.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.adapters.tokenizers.bge_m3 import BgeM3TokenCounter
from app.core.config import get_settings
from embed_benchmark import DEFAULT_DOCS, sample_chunks

EXPECTED_COUNTS_IN_DEFAULT_DOC_ORDER = [32, 16, 24, 19, 35]
EXPECTED_TOTAL = 126
EXPECTED_SHA256 = "9e3c4ee12f868ad3df7ea9f7fdb6d79f829beb2eb42756fbe607c88063a6b809"


def compute_fingerprint() -> tuple[dict[str, int], list[int], int, str]:
    """Extract sample chunks and calculate deterministic fingerprint."""
    settings = get_settings()
    counter = BgeM3TokenCounter()
    records, counts = sample_chunks(DEFAULT_DOCS, settings)

    doc_paths = sorted(DEFAULT_DOCS.glob("*.pdf"))
    counts_in_order = [counts.get(p.name, 0) for p in doc_paths]

    rows = [
        [
            record["document"],
            record["page"],
            record["section"],
            record["chunk_index"],
            counter.count(str(record["content"])),
            record["content"],
        ]
        for record in records
    ]

    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()

    return counts, counts_in_order, len(rows), digest


def main(argv: Sequence[str] | None = None) -> int:
    """Run fingerprint calculation and compare against approved constants."""
    del argv
    counts, counts_in_order, total, digest = compute_fingerprint()

    print(f"COUNTS: {json.dumps(counts, sort_keys=True)}")
    print(f"COUNTS_IN_ORDER: {counts_in_order}")
    print(f"TOTAL: {total}")
    print(f"SHA256: {digest}")

    matches = (
        counts_in_order == EXPECTED_COUNTS_IN_DEFAULT_DOC_ORDER
        and total == EXPECTED_TOTAL
        and digest == EXPECTED_SHA256
    )

    if matches:
        print("RESULT: PASS")
        return 0

    print("RESULT: FAIL (mismatch with approved constants)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
