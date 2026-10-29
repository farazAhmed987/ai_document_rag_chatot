"""Shared pytest configuration.

SRD Section 15.
Requirements: DM-2, IN-1, IN-2 and MT-5.

Database and sample-data tests are automatically skipped when
their required external resources are unavailable.
"""

from __future__ import annotations

import pytest

from app.core.config import REPO_ROOT, get_settings


def pytest_collection_modifyitems(
    items: list[pytest.Item],
) -> None:
    """Apply resource-dependent test skipping rules.

    Args:
        items: All collected pytest test items.
    """
    settings = get_settings()

    database_available = bool(settings.DATABASE_URL)

    sample_dir = REPO_ROOT / "data" / "sample_docs"

    sample_available = sample_dir.is_dir() and any(sample_dir.glob("*.pdf"))

    for item in items:
        if item.get_closest_marker("db"):
            if not database_available:
                item.add_marker(
                    pytest.mark.skip(reason=("DATABASE_URL is not configured"))
                )

        if item.get_closest_marker("sample"):
            if not sample_available:
                item.add_marker(
                    pytest.mark.skip(reason=("Generated sample PDFs are unavailable"))
                )
