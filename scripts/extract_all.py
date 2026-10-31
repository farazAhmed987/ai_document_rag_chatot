"""Summarize extraction results for a directory (IN-1, IN-2; SRD §8.1).

Usage: python scripts/extract_all.py data/sample_docs
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.adapters.extractors.factory import SUPPORTED, extract_document
from app.services.cleaning import clean_document


def main() -> None:
    """Extract supported documents recursively and show a result table."""
    parser = argparse.ArgumentParser(description="Extract a directory of documents")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error("directory does not exist")

    paths = sorted(
        path
        for path in args.directory.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED
    )
    if not paths:
        parser.error("no supported documents found")
    print(f"{'File':<43} {'Type':<6} {'Pages':>6} {'Chars':>8} {'Warn':>5} {'H/F':>6}")
    print("-" * 82)
    failures = 0
    for path in paths:
        try:
            extracted = extract_document(path)
            cleaned = clean_document(extracted)
            report = cleaned.report
            print(
                f"{path.relative_to(args.directory)!s:<43} "
                f"{extracted.file_type:<6} {extracted.page_count or '-'!s:>6} "
                f"{report.characters_after:>8} {len(report.warnings):>5} "
                f"{report.header_lines_removed + report.footer_lines_removed:>6}"
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {path.name}: {type(exc).__name__}: {exc}")
    print(f"Documents: {len(paths)}; errors: {failures}")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
