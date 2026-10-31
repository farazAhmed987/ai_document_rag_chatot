"""Fail-first checks for reproducible Phase 1 DOCX ZIP metadata.

SRD Appendix A; DM-1 and IN-1. Uses only temporary sample documents.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

from app.core.config import REPO_ROOT

_STEMS = (
    "Company_Overview",
    "Property_Listings",
    "Services_and_Fees",
    "FAQs",
    "Policies_and_Terms",
)


def test_docx_byte_stability_and_canonical_zip(tmp_path: Path) -> None:
    """DOCX has fixed entry timestamps, sorted members and identical bytes."""
    source = tmp_path / "src"
    source.mkdir()
    for stem in _STEMS:
        (source / f"{stem}.md").write_text(
            f"# {stem}\n\nDocument title: {stem}\n\n## Fees\n\n"
            "| Service | Cost |\n| --- | --- |\n| Advice | 100 |\n",
            encoding="utf-8",
        )

    hashes: list[str] = []
    for output in (tmp_path / "run_one", tmp_path / "run_two"):
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "build_sample_pdfs.py"),
                "--source-dir",
                str(source),
                "--output-dir",
                str(output),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        docx_file = output / "variants" / "Services_and_Fees.docx"
        hashes.append(hashlib.sha256(docx_file.read_bytes()).hexdigest())
        with zipfile.ZipFile(docx_file) as archive:
            names = archive.namelist()
            assert names == sorted(names)
            assert all(
                info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
            )
            core_xml = archive.read("docProps/core.xml").decode("utf-8")
            assert "2000-01-01" in core_xml
            assert "revision" in core_xml or "<cp:revision>1</cp:revision>" in core_xml
    assert hashes[0] == hashes[1]
