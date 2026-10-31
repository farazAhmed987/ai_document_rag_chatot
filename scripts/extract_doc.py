"""Extract and clean a file, writing inspectable Phase 3 artifacts.

SRD §8.1 steps 3–4, IN-1, IN-2, and IN-3.
Usage: python scripts/extract_doc.py data/sample_docs/FAQs.pdf
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.adapters.extractors.factory import extract_document
from app.services.cleaning import clean_document

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "extracted"


def main() -> None:
    """Produce extracted JSON, cleaned Markdown and a cleaning report."""
    parser = argparse.ArgumentParser(description="Extract and clean one document")
    parser.add_argument("path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    extracted = extract_document(args.path)
    cleaned = clean_document(extracted)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    name = args.path.stem
    json_path = args.output_dir / f"{name}.extracted.json"
    markdown_path = args.output_dir / f"{name}.cleaned.md"
    json_path.write_text(
        json.dumps(asdict(extracted), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    chunks = []
    for unit in cleaned.units:
        if unit.page_number is not None:
            chunks.append(f"<!-- page {unit.page_number} -->\n{unit.markdown}")
        else:
            chunks.append(unit.markdown)
    markdown_path.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    print(json.dumps(asdict(cleaned.report), ensure_ascii=False, indent=2))
    print(f"Extracted JSON: {json_path}")
    print(f"Cleaned Markdown: {markdown_path}")


if __name__ == "__main__":
    main()
