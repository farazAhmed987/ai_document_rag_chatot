"""Phase 4 Stage 1 contracts approved by Claude (IN-4, IN-5; SRD 8.1).

The loader fixture defers imports until each test's call phase. Missing
chunking functionality must fail named tests, not collection or fixture setup.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from copy import deepcopy
from itertools import pairwise
from types import ModuleType
from typing import TYPE_CHECKING, Literal

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.schemas.extraction import CleanedDocument, CleaningReport, ExtractedUnit

if TYPE_CHECKING:
    from app.schemas.chunking import ChunkDraft

Loader = Callable[[], ModuleType]
FileType = Literal["pdf", "docx", "txt", "md"]
COMPANY = "Example Realty"


class WordCounter:
    """Deterministic offline whitespace-token counter."""

    def count(self, text: str) -> int:
        return len(text.split())


class CharacterCounter:
    """Offline counter making a long single word exceed a token budget."""

    def count(self, text: str) -> int:
        return len(text)


@pytest.fixture
def load_chunking(monkeypatch: pytest.MonkeyPatch) -> Loader:
    """Return an import thunk; import failures occur in the test call phase."""
    for name in (
        "CHUNK_TOKENS",
        "CHUNK_OVERLAP",
        "MIN_CHUNK_TOKENS",
        "MAX_TABLE_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    def load() -> ModuleType:
        return importlib.import_module("app.services.chunking")

    return load


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "CHUNK_TOKENS": 500,
        "CHUNK_OVERLAP": 60,
        "MIN_CHUNK_TOKENS": 80,
        "MAX_TABLE_TOKENS": 1500,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def words(prefix: str, count: int) -> str:
    return " ".join(f"{prefix}{index:04d}" for index in range(count))


def unit(text: str, page: int | None = None) -> ExtractedUnit:
    return ExtractedUnit(
        kind="page" if page is not None else "section",
        index=page - 1 if page is not None else 0,
        page_number=page,
        heading_path=["Terms"],
        markdown=text,
    )


def document(units: list[ExtractedUnit], file_type: FileType = "md") -> CleanedDocument:
    return CleanedDocument(
        filename=f"sample.{file_type}",
        file_type=file_type,
        units=units,
        page_count=max((item.page_number or 0 for item in units), default=0)
        if file_type == "pdf"
        else None,
        metadata={},
        warnings=[],
        report=CleaningReport(),
    )


def run(
    api: ModuleType,
    units: list[ExtractedUnit],
    *,
    file_type: FileType = "md",
    overlap: int = 60,
    counter: WordCounter | CharacterCounter | None = None,
) -> list[ChunkDraft]:
    result = api.chunk_document(
        document(units, file_type),
        company_name=COMPANY,
        token_counter=counter or WordCounter(),
        settings=settings(CHUNK_OVERLAP=overlap),
    )
    assert isinstance(result, list), (
        "The public service returns a list, never a generator"
    )
    return result


def table(rows: int, width: int = 4) -> tuple[str, list[str]]:
    data = [f"| row{i:03d} | {words(f'v{i}_', width)} |" for i in range(rows)]
    return "\n".join(["| ID | Details |", "| --- | --- |", *data]), data


def assert_safe_error(error: AppError, sentinel: str) -> None:
    assert error.code == "CHUNK_BUDGET_EXCEEDED"
    assert set(error.details) <= {
        "sub_reason",
        "page",
        "unit_index",
        "observed_tokens",
        "limit",
    }
    assert {"sub_reason", "page", "unit_index", "observed_tokens", "limit"} <= set(
        error.details
    )
    for rendered in (
        str(error),
        repr(error),
        repr(error.details),
        repr(error.as_dict()),
    ):
        assert sentinel not in rendered
    assert error.details["observed_tokens"] > error.details["limit"]


def test_counter_is_protocol_and_accepts_offline_fake(load_chunking: Loader) -> None:
    api = load_chunking()
    assert api.TokenCounter.__dict__.get("_is_protocol") is True
    assert WordCounter().count("alpha beta") == 2
    assert len(run(api, [unit("Complete small body.")])) == 1


def test_non_table_limits_and_counts_include_header(load_chunking: Loader) -> None:
    chunks = run(load_chunking(), [unit(words("word", 1600))])
    assert len(chunks) > 1
    for chunk in chunks:
        assert not chunk.is_table
        assert chunk.token_count == WordCounter().count(chunk.content)
        assert chunk.token_count <= 575
        assert chunk.token_count > WordCounter().count(chunk.body)


def test_overlap_is_sixty_body_tokens_with_no_initial_prefix(
    load_chunking: Loader,
) -> None:
    text = words("word", 1600)
    chunks = run(load_chunking(), [unit(text)])
    assert len(chunks) > 1
    assert chunks[0].body.split()[0] == "word0000"
    reconstructed = chunks[0].body.split()
    for previous, current in pairwise(chunks):
        assert previous.body.split()[-60:] == current.body.split()[:60]
        assert len(current.body.split()) > 60
        reconstructed.extend(current.body.split()[60:])
    assert reconstructed == text.split()


def test_zero_overlap_preserves_exact_word_order(load_chunking: Loader) -> None:
    text = words("word", 1600)
    chunks = run(load_chunking(), [unit(text)], overlap=0)
    assert len(chunks) > 1
    assert [token for chunk in chunks for token in chunk.body.split()] == text.split()


def test_pdf_chunks_never_cross_or_overlap_pages(load_chunking: Loader) -> None:
    chunks = run(
        load_chunking(),
        [unit(words("left", 700), 2), unit(words("right", 700), 4)],
        file_type="pdf",
    )
    assert {chunk.page for chunk in chunks} == {2, 4}
    for chunk in chunks:
        expected, forbidden = (
            ("left", "right") if chunk.page == 2 else ("right", "left")
        )
        assert expected in chunk.body and forbidden not in chunk.body


def test_headings_inside_one_unit_are_hard_boundaries(load_chunking: Loader) -> None:
    text = (
        f"# Terms\n## Payments\n{words('pay', 700)}\n## Refunds\n{words('refund', 700)}"
    )
    chunks = run(load_chunking(), [unit(text)])
    assert {chunk.section for chunk in chunks} == {
        "Terms > Payments",
        "Terms > Refunds",
    }
    for chunk in chunks:
        assert not ("pay0000" in chunk.body and "refund0000" in chunk.body)
        assert (
            "refund" not in chunk.body
            if chunk.section.endswith("Payments")
            else "pay" not in chunk.body
        )


def test_pdf_continuation_uses_inherited_heading_path(load_chunking: Loader) -> None:
    page = unit("Payment conditions continue.", 7)
    page.heading_path = ["Terms", "Payments"]
    chunks = run(load_chunking(), [page], file_type="pdf")
    assert len(chunks) == 1
    assert chunks[0].page == 7
    assert chunks[0].section == "Terms > Payments"


def test_short_page_segments_in_same_section_stay_separate(
    load_chunking: Loader,
) -> None:
    chunks = run(
        load_chunking(), [unit("PageAlpha.", 1), unit("PageBeta.", 2)], file_type="pdf"
    )
    assert len(chunks) == 2
    assert [chunk.page for chunk in chunks] == [1, 2]
    assert all(chunk.sub_min_reason == "sole_chunk_of_segment" for chunk in chunks)
    assert "PageBeta" not in chunks[0].body and "PageAlpha" not in chunks[1].body
    assert chunks[0].segment_key != chunks[1].segment_key


def test_small_tail_merges_when_combined_content_fits(load_chunking: Loader) -> None:
    text = words("word", 520)
    chunks = run(load_chunking(), [unit(text)], overlap=0)
    assert len(chunks) == 1
    assert chunks[0].body.split() == text.split()
    assert chunks[0].token_count <= 575


def test_small_tail_rebalances_when_merge_exceeds_ceiling(
    load_chunking: Loader,
) -> None:
    text = words("word", 570)
    chunks = run(load_chunking(), [unit(text)], overlap=0)
    assert len(chunks) == 2
    assert WordCounter().count(chunks[0].header + "\n\n" + text) > 575
    assert all(WordCounter().count(chunk.body) >= 80 for chunk in chunks)
    assert all(
        chunk.token_count <= 575 and chunk.sub_min_reason is None for chunk in chunks
    )
    assert [token for chunk in chunks for token in chunk.body.split()] == text.split()


def test_indivisible_paragraph_tail_is_kept_with_reason(load_chunking: Loader) -> None:
    text = words("large", 490) + "\n\n" + words("small", 79)
    chunks = run(load_chunking(), [unit(text)], overlap=0)
    assert len(chunks) == 2
    assert [WordCounter().count(chunk.body) for chunk in chunks] == [490, 79]
    assert chunks[1].sub_min_reason == "unit_indivisible"
    assert chunks[0].segment_key == chunks[1].segment_key
    assert all(chunk.page is None and chunk.section == "Terms" for chunk in chunks)
    assert all(chunk.token_count <= 575 for chunk in chunks)


def test_minimum_is_body_count_even_when_content_exceeds_minimum(
    load_chunking: Loader,
) -> None:
    chunks = run(load_chunking(), [unit(words("short", 75))])
    assert len(chunks) == 1
    assert WordCounter().count(chunks[0].body) == 75
    assert chunks[0].token_count >= 80
    assert chunks[0].sub_min_reason == "sole_chunk_of_segment"


@pytest.mark.parametrize("text", ["", " ", "\n\t\n", "# Terms\n\n## Empty"])
def test_empty_and_heading_only_segments_produce_nothing(
    load_chunking: Loader, text: str
) -> None:
    assert run(load_chunking(), [unit(text)]) == []


def test_empty_document_produces_nothing(load_chunking: Loader) -> None:
    assert run(load_chunking(), []) == []


def test_table_above_prose_budget_below_table_limit_stays_whole(
    load_chunking: Loader,
) -> None:
    text, rows = table(20, 30)
    chunks = run(load_chunking(), [unit(text)])
    assert len(chunks) == 1
    assert chunks[0].is_table and chunks[0].table_part is None
    assert 575 < chunks[0].token_count <= 1500
    assert all(row in chunks[0].body.splitlines() for row in rows)


def test_table_parts_repeat_headers_and_preserve_rows_once(
    load_chunking: Loader,
) -> None:
    text, rows = table(55, 40)
    chunks = run(load_chunking(), [unit(text)])
    assert len(chunks) > 1
    found: list[str] = []
    for index, chunk in enumerate(chunks, 1):
        assert chunk.is_table and chunk.table_part == f"{index}/{len(chunks)}"
        assert chunk.token_count == WordCounter().count(chunk.content)
        assert chunk.token_count <= 1500
        assert chunk.body.splitlines()[:2] == ["| ID | Details |", "| --- | --- |"]
        found.extend(chunk.body.splitlines()[2:])
    assert found == rows


def test_escaped_pipe_does_not_split_a_table_row(load_chunking: Loader) -> None:
    row = r"| HCR-001 | East \| West | 12500000 |"
    text = "| ID | Location | Price |\n| --- | --- | --- |\n" + row
    chunks = run(load_chunking(), [unit(text)])
    assert len(chunks) == 1 and chunks[0].is_table
    assert row in chunks[0].body.splitlines()


def test_lead_in_is_separate_when_full_table_would_overflow(
    load_chunking: Loader,
) -> None:
    caption = words("caption", 500)
    markdown, rows = table(29, 30)
    chunks = run(load_chunking(), [unit(caption + "\n\n" + markdown)])
    tables = [chunk for chunk in chunks if chunk.is_table]
    assert len(tables) == 1
    assert (
        WordCounter().count(tables[0].header + "\n\n" + caption + "\n\n" + markdown)
        > 1500
    )
    assert "caption" not in tables[0].body
    assert tables[0].token_count <= 1500
    assert [
        token for chunk in chunks if not chunk.is_table for token in chunk.body.split()
    ] == caption.split()
    assert all(row in tables[0].body.splitlines() for row in rows)


def test_overlap_never_enters_or_leaves_table_chunks(load_chunking: Loader) -> None:
    markdown, rows = table(40, 30)
    text = words("before", 700) + "\n\n" + markdown + "\n\n" + words("after", 700)
    chunks = run(load_chunking(), [unit(text)])
    tables = [chunk for chunk in chunks if chunk.is_table]
    assert len(tables) == 1
    assert "before" not in tables[0].body and "after" not in tables[0].body
    for chunk in chunks:
        if not chunk.is_table:
            assert not any(row in chunk.body for row in rows)
            assert not ("before" in chunk.body and "after" in chunk.body)
    after = next(chunk for chunk in chunks if "after0000" in chunk.body)
    assert after.body.split()[0] == "after0000"


def test_oversized_row_raises_atomically_with_safe_diagnostics(
    load_chunking: Loader,
) -> None:
    api = load_chunking()
    from app.core.errors import ChunkingBudgetError

    assert issubclass(ChunkingBudgetError, AppError)
    sentinel = "PRIVATE_SENTINEL_91"
    row = "| " + words(sentinel, 1600) + " |"
    source = document(
        [unit(f"# {sentinel}\nEarlier safe text.\n\n| ID |\n| --- |\n{row}")]
    )
    source.filename = sentinel + ".md"
    original = deepcopy(source)
    with pytest.raises(ChunkingBudgetError) as caught:
        api.chunk_document(
            source,
            company_name=COMPANY,
            token_counter=WordCounter(),
            settings=settings(),
        )
    assert_safe_error(caught.value, sentinel)
    assert caught.value.details["limit"] == 1500
    assert caught.value.details["unit_index"] == 0
    assert source == original


def test_header_over_quarter_budget_raises_safe_error(load_chunking: Loader) -> None:
    api = load_chunking()
    from app.core.errors import ChunkingBudgetError

    sentinel = "PRIVATE_SENTINEL_92"
    source = document([unit(f"# {sentinel}\n{sentinel}")])
    source.filename = sentinel + ".md"
    with pytest.raises(ChunkingBudgetError) as caught:
        api.chunk_document(
            source,
            company_name=words(sentinel, 130),
            token_counter=WordCounter(),
            settings=settings(),
        )
    assert_safe_error(caught.value, sentinel)
    assert caught.value.details["sub_reason"] == "HEADER_TOO_LARGE"
    assert caught.value.details["limit"] == 125


def test_long_single_word_hard_splits_without_loss_deterministically(
    load_chunking: Loader,
) -> None:
    api = load_chunking()
    text = "ABCD" * 400
    counter = CharacterCounter()
    first = run(api, [unit(text)], counter=counter, overlap=0)
    second = run(api, [unit(text)], counter=counter, overlap=0)
    assert len(first) > 1 and first == second
    assert "".join(chunk.body for chunk in first) == text
    assert all(
        chunk.token_count == counter.count(chunk.content) <= 575 for chunk in first
    )


def test_context_header_format_content_and_heading_path(load_chunking: Loader) -> None:
    chunks = run(
        load_chunking(), [unit("# Terms\n## Payments\nDeposit payable today.")]
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.section == "Terms > Payments"
    assert (
        chunk.header
        == "Company: Example Realty\nDocument: sample.md\nSection: Terms > Payments"
    )
    assert chunk.content == chunk.header + "\n\n" + chunk.body
    assert chunk.token_count == WordCounter().count(chunk.content)
    assert chunk.segment_key[:2] == (None, chunk.section)


def test_header_values_collapse_whitespace_and_newlines(load_chunking: Loader) -> None:
    api = load_chunking()
    item = unit("Body stays intact.")
    item.heading_path = [" Sales\n Terms ", " Payment\tRules "]
    source = document([item])
    source.filename = " report\n  final\t.md "
    chunks = api.chunk_document(
        source,
        company_name=" Acme\n  Realty\t Ltd ",
        token_counter=WordCounter(),
        settings=settings(),
    )
    assert len(chunks) == 1
    assert (
        chunks[0].header
        == "Company: Acme Realty Ltd\nDocument: report final .md\nSection: Sales Terms > Payment Rules"
    )
    assert len(chunks[0].header.splitlines()) == 3


def test_strip_header_round_trips_and_preserves_body_labels(
    load_chunking: Loader,
) -> None:
    api = load_chunking()
    text = "Body text.\n\nCompany: quoted value\nDocument: still body"
    chunk = run(api, [unit(text)])[0]
    assert api.strip_context_header(chunk.content) == chunk.body == text


@pytest.mark.parametrize(
    "text",
    [
        "Ordinary body.",
        "Company: quoted value\n\nStill body.",
        "Company: C\nSection: S\nDocument: D\n\nBody.",
        "Company: C\nDocument: D\nSection: S\nBody without blank line.",
        "Preface\nCompany: C\nDocument: D\nSection: S\n\nBody.",
    ],
)
def test_strip_header_leaves_non_headers_unchanged(
    load_chunking: Loader, text: str
) -> None:
    assert load_chunking().strip_context_header(text) == text


def test_settings_defaults_extend_existing_settings(load_chunking: Loader) -> None:
    load_chunking()
    config = Settings(_env_file=None)
    assert config.CHUNK_TOKENS == 500 and config.CHUNK_OVERLAP == 60
    assert config.MIN_CHUNK_TOKENS == 80 and config.MAX_TABLE_TOKENS == 1500


@pytest.mark.parametrize(
    "overrides",
    [
        {"CHUNK_OVERLAP": 500},
        {"MIN_CHUNK_TOKENS": 500},
        {"MIN_CHUNK_TOKENS": 0},
        {"MAX_TABLE_TOKENS": 499},
        {"MAX_TABLE_TOKENS": 0},
        {"CHUNK_OVERLAP": -1},
    ],
)
def test_settings_reject_invalid_budgets(
    load_chunking: Loader, overrides: dict[str, int]
) -> None:
    load_chunking()
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_new_settings_load_from_environment(
    load_chunking: Loader, monkeypatch: pytest.MonkeyPatch
) -> None:
    load_chunking()
    monkeypatch.setenv("MIN_CHUNK_TOKENS", "90")
    monkeypatch.setenv("MAX_TABLE_TOKENS", "1600")
    config = Settings(_env_file=None)
    assert config.MIN_CHUNK_TOKENS == 90 and config.MAX_TABLE_TOKENS == 1600


@pytest.mark.parametrize("file_type", ["docx", "txt", "md"])
def test_non_pdf_chunks_have_no_synthetic_page(
    load_chunking: Loader, file_type: FileType
) -> None:
    chunks = run(load_chunking(), [unit("No pagination here.")], file_type=file_type)
    assert len(chunks) == 1 and chunks[0].page is None


def test_arabic_urdu_and_joiners_are_preserved(load_chunking: Loader) -> None:
    text = "یہ دستاویز محفوظ ہے۔ می\u200cخواهم\n\nالعربية لغة جميلة۔ ب\u200dسم الله"
    chunks = run(load_chunking(), [unit(text)])
    assert len(chunks) == 1 and chunks[0].body == text


def test_determinism_and_cleaned_input_immutability(load_chunking: Loader) -> None:
    api = load_chunking()
    source = document([unit(words("word", 1300))])
    original = deepcopy(source)
    first = api.chunk_document(
        source, company_name=COMPANY, token_counter=WordCounter(), settings=settings()
    )
    second = api.chunk_document(
        source, company_name=COMPANY, token_counter=WordCounter(), settings=settings()
    )
    assert first and first == second and source == original


def test_indices_are_contiguous_across_all_chunk_types(load_chunking: Loader) -> None:
    markdown, _ = table(55, 40)
    text = f"# Terms\n{words('word', 1300)}\n\n# Data\n{markdown}\n\n# End\nDone."
    chunks = run(load_chunking(), [unit(text)])
    assert len(chunks) > 3
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))


def test_empty_pdf_placeholder_preserves_original_page_number(
    load_chunking: Loader,
) -> None:
    chunks = run(
        load_chunking(), [unit("", 1), unit("Real page two body.", 2)], file_type="pdf"
    )
    assert len(chunks) == 1
    assert chunks[0].page == 2 and chunks[0].chunk_index == 0


def test_table_separates_same_page_section_into_distinct_segments(
    load_chunking: Loader,
) -> None:
    markdown, _ = table(40, 30)
    text = words("before", 700) + "\n\n" + markdown + "\n\n" + words("after", 700)
    chunks = run(load_chunking(), [unit(text)])
    before = next(chunk for chunk in chunks if "before0000" in chunk.body)
    after = next(chunk for chunk in chunks if "after0000" in chunk.body)
    assert before.section == after.section == "Terms"
    assert before.page == after.page is None
    assert before.segment_key != after.segment_key
