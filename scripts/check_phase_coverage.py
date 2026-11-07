"""Enforce module coverage without changing the measured source set."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def check(
    report: dict[str, Any], modules: Sequence[str], minimum: float = 85.0
) -> bool:
    files = report.get("files", {})
    passed = True
    for module in modules:
        record = files.get(module)
        if record is None:
            print(f"FAIL: {module}: absent from coverage report")
            passed = False
            continue
        percentage = float(record["summary"]["percent_covered"])
        good = percentage >= minimum
        print(
            f"{'PASS' if good else 'FAIL'}: {module}: {percentage:.2f}% (minimum {minimum:.2f}%)"
        )
        passed = passed and good
    return passed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--module", action="append", required=True)
    parser.add_argument("--minimum", type=float, default=85.0)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    return 0 if check(report, args.module, args.minimum) else 1


if __name__ == "__main__":
    raise SystemExit(main())
