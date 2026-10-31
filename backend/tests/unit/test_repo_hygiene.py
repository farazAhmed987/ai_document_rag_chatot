"""Verify Git ignore rules cannot hide application models or fixtures.

SRD Sections 3 and 14; MT-3.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app.core.config import REPO_ROOT


def _ignored(path: str) -> bool:
    """Check ignore rules even for paths already tracked in Git."""
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    return result.returncode == 0


@pytest.fixture(autouse=True)
def require_git_checkout() -> None:
    """Skip if Git or a checked-out repository is unavailable."""
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("Git checkout required for repository ignore tests")


def test_backend_model_sources_are_not_ignored() -> None:
    """The future ORM module must not match the root ML models/ rule."""
    assert not _ignored("backend/app/models/zz_new.py")


@pytest.mark.parametrize(
    "path",
    [
        "data/sample_docs/a.pdf",
        "data/sample_docs_src/Company_Overview.md",
        "data/facts/key_facts.json",
        "eval/x.json",
    ],
)
def test_canonical_data_is_not_ignored(path: str) -> None:
    """Source fixtures, facts, generated PDFs and eval remain trackable."""
    assert not _ignored(path)


def test_private_runtime_files_are_ignored() -> None:
    """Ensure credentials and transient QA spill files stay out of Git."""
    assert _ignored(".env")
    assert _ignored("data/qa_spill/example.jsonl")
