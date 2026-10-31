"""Compare page references between raw and cleaned fact maps (IN-5, B7).

Usage:
    python scripts/compare_page_maps.py data/facts/page_map.json data/extracted/phase3_pipeline_page_map.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def compare(raw: dict[str, Any], pipeline: dict[str, Any]) -> list[str]:
    """Return difference descriptions without changing either input map."""
    rows: list[str] = []
    for fact_id in sorted(set(raw) | set(pipeline)):
        left = raw.get(fact_id, {})
        right = pipeline.get(fact_id, {})
        a = sorted(left.get("pages", []))
        b = sorted(right.get("pages", []))
        if a == b and fact_id in raw and fact_id in pipeline:
            continue
        snippets = left.get("snippets", []) or right.get("snippets", [])
        snippet = "; ".join(str(item.get("snippet", "")) for item in snippets)
        rows.append(f"{fact_id}: raw={a}, pipeline={b}; snippet={snippet}")
    return rows


def main() -> None:
    """Print all differing fact IDs and their original evidence."""
    parser = argparse.ArgumentParser(description="Compare raw and pipeline page sets")
    parser.add_argument("raw", type=Path)
    parser.add_argument("pipeline", type=Path)
    args = parser.parse_args()
    left = json.loads(args.raw.read_text(encoding="utf-8"))
    right = json.loads(args.pipeline.read_text(encoding="utf-8"))
    differences = compare(left, right)
    for row in differences:
        print(row)
    print(f"Differing facts: {len(differences)}")


if __name__ == "__main__":
    main()
