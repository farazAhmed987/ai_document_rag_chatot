"""Unit tests for hybrid retrieval service, RRF fusion, and diversification.

SRD Sections 8.2, 9; DM-3, IN-5, MT-1, MT-3; A-12.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.core.errors import (
    RerankerConfigurationError,
    RetrievalInputError,
)
from app.repositories.retrieval import (
    build_or_tsquery_expression,
    extract_search_tokens,
)
from app.schemas.retrieval import RetrievedChunk
from app.services.retrieval import RetrievalService


class _DummyTokenCounter:
    """Configurable token counter for testing budget limits."""

    def __init__(self, count_val: int = 5) -> None:
        self.count_val = count_val

    def count(self, text: str) -> int:
        del text
        return self.count_val


class _DummyEmbedder:
    """Stub embedder returning unit vectors."""

    dimension = 1024

    def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0] + [0.0] * 1023

    def embed_documents(
        self, texts: list[str], batch_size: int = 16
    ) -> list[list[float]]:
        del batch_size
        return [[1.0] + [0.0] * 1023 for _ in texts]


def _make_chunk(
    doc_id: UUID,
    chunk_index: int,
    doc_name: str = "DocA.pdf",
    similarity: float = 0.8,
    keyword_score: float = 0.5,
    chunk_id: UUID | None = None,
) -> RetrievedChunk:
    """Helper to build RetrievedChunk test double."""
    cid = chunk_id or uuid4()
    return RetrievedChunk(
        rank=1,
        chunk_id=cid,
        document_id=doc_id,
        document_name=doc_name,
        chunk_index=chunk_index,
        page=chunk_index + 1,
        section=f"Section {chunk_index}",
        content=f"Company: C\nDocument: {doc_name}\nSection: S\n\nContent for chunk {chunk_index}",
        body=f"Content for chunk {chunk_index}",
        token_count=50,
        score=similarity,
        dense_rank=1,
        dense_similarity=similarity,
        keyword_rank=1,
        keyword_score=keyword_score,
    )


# ---------------------------------------------------------------------------
# Query Normalization & Token Extraction
# ---------------------------------------------------------------------------


def test_normalize_query_collapses_whitespace_and_nfkc() -> None:
    """Normalizes ligature symbols and excess whitespace."""
    mock_factory = MagicMock()
    service = RetrievalService(
        session_factory=mock_factory,
        embedder=_DummyEmbedder(),
        token_counter=_DummyTokenCounter(count_val=5),
    )

    raw_query = "   What   is   the   \t  \n  deposit?   "
    cleaned = service.normalize_query(raw_query)
    assert cleaned == "What is the deposit?"

    # NFKC compatibility check (ligature 'fi')
    ligature_query = "What is the speciﬁc fee?"
    cleaned_ligature = service.normalize_query(ligature_query)
    assert cleaned_ligature == "What is the specific fee?"


def test_normalize_query_rejects_empty_and_whitespace() -> None:
    """Empty or whitespace-only query raises RetrievalInputError."""
    service = RetrievalService(
        session_factory=MagicMock(),
        embedder=_DummyEmbedder(),
        token_counter=_DummyTokenCounter(count_val=0),
    )

    with pytest.raises(RetrievalInputError) as exc_info:
        service.normalize_query("   \n\t  ")
    assert exc_info.value.code == "RETRIEVAL_INPUT_ERROR"

    with pytest.raises(RetrievalInputError) as exc_info:
        service.normalize_query("")
    assert exc_info.value.code == "RETRIEVAL_INPUT_ERROR"


def test_normalize_query_rejects_non_string() -> None:
    """Non-string query raises RetrievalInputError."""
    service = RetrievalService(
        session_factory=MagicMock(),
        embedder=_DummyEmbedder(),
        token_counter=_DummyTokenCounter(count_val=0),
    )
    with pytest.raises(RetrievalInputError):
        service.normalize_query(None)  # type: ignore[arg-type]


def test_normalize_query_enforces_token_limit() -> None:
    """Overlong query exceeding maximum allowed tokens raises RetrievalInputError."""
    service = RetrievalService(
        session_factory=MagicMock(),
        embedder=_DummyEmbedder(),
        token_counter=_DummyTokenCounter(count_val=600),
        max_query_tokens=512,
    )

    with pytest.raises(RetrievalInputError) as exc_info:
        service.normalize_query("A long query exceeding token budget")
    assert exc_info.value.code == "RETRIEVAL_INPUT_ERROR"
    assert "exceeds maximum token length" in str(exc_info.value)


def test_extract_search_tokens_and_build_tsquery() -> None:
    """Extracts alphanumeric tokens and constructs safe OR tsquery."""
    tokens = extract_search_tokens("Residential rent & security-deposit 100%?")
    assert "residential" in tokens
    assert "rent" in tokens
    assert "security" in tokens
    assert "deposit" in tokens
    assert "100" in tokens

    tsquery = build_or_tsquery_expression(tokens)
    assert tsquery is not None
    assert "'residential'" in tsquery
    assert " | " in tsquery

    # Stopword filtering and entity preservation
    filtered_tokens = extract_search_tokens(
        "what is the rate for HCR-ISB-0102 on 2026-11-10?"
    )
    assert "what" not in filtered_tokens
    assert "is" not in filtered_tokens
    assert "the" not in filtered_tokens
    assert "for" not in filtered_tokens
    assert "rate" in filtered_tokens
    assert "hcr-isb-0102" in filtered_tokens
    assert "2026-11-10" in filtered_tokens

    # Empty noise query returns None
    noise_tokens = extract_search_tokens("!@#$%^&*()_+=-")
    assert noise_tokens == []
    assert build_or_tsquery_expression(noise_tokens) is None


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion (RRF)
# ---------------------------------------------------------------------------


def test_fuse_rrf_combines_dense_and_keyword() -> None:
    """RRF correctly scores dense-only, keyword-only, and overlapping candidates."""
    doc_id = uuid4()
    c1 = _make_chunk(doc_id, 1, doc_name="Doc1.pdf", similarity=0.9, keyword_score=0.0)
    c2 = _make_chunk(doc_id, 2, doc_name="Doc1.pdf", similarity=0.8, keyword_score=0.0)
    c3 = _make_chunk(doc_id, 3, doc_name="Doc1.pdf", similarity=0.0, keyword_score=0.7)

    # c1 is rank 1 in dense, rank 1 in keyword
    # c2 is rank 2 in dense only
    # c3 is rank 2 in keyword only
    dense_list = [c1, c2]
    keyword_list = [c1, c3]

    service = RetrievalService(session_factory=MagicMock(), embedder=_DummyEmbedder())
    fused = service.fuse_rrf(dense_list, keyword_list, k=60)

    assert len(fused) == 3
    # c1 has both: 1/(60+1) + 1/(60+1) = 2/61 ≈ 0.032787
    expected_c1 = (1.0 / 61.0) + (1.0 / 61.0)
    assert pytest.approx(fused[0].score, 1e-6) == expected_c1
    assert fused[0].chunk_id == c1.chunk_id
    assert fused[0].rank == 1

    # c2 has 1/(60+2) = 1/62 ≈ 0.016129
    # c3 has 1/(60+2) = 1/62 ≈ 0.016129
    expected_c2 = 1.0 / 62.0
    assert pytest.approx(fused[1].score, 1e-6) == expected_c2
    assert pytest.approx(fused[2].score, 1e-6) == expected_c2


def test_fuse_rrf_deterministic_tie_breaks() -> None:
    """Tie-breaking sorts by score DESC, document_name ASC, chunk_index ASC, chunk_id ASC."""
    doc_a = uuid4()
    doc_b = uuid4()

    # Same score (dense rank 1 each), different doc names
    c_b = _make_chunk(
        doc_b,
        1,
        doc_name="B_Doc.pdf",
        chunk_id=UUID("00000000-0000-0000-0000-000000000002"),
    )
    c_a = _make_chunk(
        doc_a,
        1,
        doc_name="A_Doc.pdf",
        chunk_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    service = RetrievalService(session_factory=MagicMock(), embedder=_DummyEmbedder())
    fused = service.fuse_rrf([c_b], [c_a], k=60)

    # Both have score 1/61. A_Doc.pdf must be sorted before B_Doc.pdf
    assert fused[0].document_name == "A_Doc.pdf"
    assert fused[1].document_name == "B_Doc.pdf"


def test_fuse_rrf_requires_positive_k() -> None:
    """RRF constant k <= 0 raises ValueError."""
    service = RetrievalService(session_factory=MagicMock(), embedder=_DummyEmbedder())
    with pytest.raises(ValueError, match="k must be positive"):
        service.fuse_rrf([], [], k=0)


# ---------------------------------------------------------------------------
# Diversification & Backfill
# ---------------------------------------------------------------------------


def test_diversify_caps_at_max_per_doc() -> None:
    """Restricts results to max_per_doc when candidates from other documents exist."""
    doc_a = uuid4()
    doc_b = uuid4()

    # 4 chunks from doc_a, 2 chunks from doc_b
    chunks_a = [_make_chunk(doc_a, i, "DocA.pdf") for i in range(1, 5)]
    chunks_b = [_make_chunk(doc_b, i, "DocB.pdf") for i in range(1, 3)]
    candidates = chunks_a + chunks_b  # 4 from A, then 2 from B

    service = RetrievalService(session_factory=MagicMock(), embedder=_DummyEmbedder())
    diversified = service.diversify(candidates, max_per_doc=3, top_n=5)

    assert len(diversified) == 5
    # First 3 from DocA, next 2 from DocB (chunk 4 from DocA was suppressed)
    doc_counts = {doc_a: 0, doc_b: 0}
    for c in diversified:
        doc_counts[c.document_id] += 1
    assert doc_counts[doc_a] == 3
    assert doc_counts[doc_b] == 2
    assert [c.rank for c in diversified] == [1, 2, 3, 4, 5]


def test_diversify_backfills_when_under_top_n() -> None:
    """Backfills suppressed chunks from the same document if candidate pool lacks diversity."""
    doc_a = uuid4()
    # 5 chunks all from doc_a
    candidates = [_make_chunk(doc_a, i, "DocA.pdf") for i in range(1, 6)]

    service = RetrievalService(session_factory=MagicMock(), embedder=_DummyEmbedder())
    # top_n=5, max_per_doc=3: pass 1 selects 3, pass 2 backfills the remaining 2
    diversified = service.diversify(candidates, max_per_doc=3, top_n=5)

    assert len(diversified) == 5
    assert all(c.document_id == doc_a for c in diversified)
    assert [c.chunk_index for c in diversified] == [1, 2, 3, 4, 5]


def test_diversify_handles_pool_smaller_than_top_n() -> None:
    """Returns all available candidates when pool size < top_n without error."""
    doc_a = uuid4()
    candidates = [_make_chunk(doc_a, 1, "DocA.pdf"), _make_chunk(doc_a, 2, "DocA.pdf")]

    service = RetrievalService(session_factory=MagicMock(), embedder=_DummyEmbedder())
    diversified = service.diversify(candidates, max_per_doc=3, top_n=5)

    assert len(diversified) == 2
    assert [c.rank for c in diversified] == [1, 2]


# ---------------------------------------------------------------------------
# Reranking Logic
# ---------------------------------------------------------------------------


def test_rerank_updates_scores_and_preserves_tail() -> None:
    """Reranks top RERANK_CANDIDATES and appends remaining candidates in order."""

    class _StubReranker:
        model_name = "stub"
        model_revision = "rev1"
        max_length = 1024

        def predict(
            self, pairs: list[tuple[str, str]], batch_size: int = 32
        ) -> list[float]:
            del batch_size
            # Invert order of first 2 pairs: give second higher score
            return [1.5, 4.2]

    doc_id = uuid4()
    c1 = _make_chunk(doc_id, 1, "Doc1.pdf", similarity=0.9)
    c2 = _make_chunk(doc_id, 2, "Doc1.pdf", similarity=0.8)
    c3 = _make_chunk(doc_id, 3, "Doc1.pdf", similarity=0.7)

    service = RetrievalService(
        session_factory=MagicMock(),
        embedder=_DummyEmbedder(),
        reranker=_StubReranker(),
    )

    reranked = service.rerank(
        query="deposit query",
        candidates=[c1, c2, c3],
        top_k=2,  # Rerank first 2 only
    )

    assert len(reranked) == 3
    # c2 received score 4.2, c1 received score 1.5; c3 remained un-reranked at tail
    assert reranked[0].chunk_id == c2.chunk_id
    assert reranked[0].rerank_score == 4.2
    assert reranked[1].chunk_id == c1.chunk_id
    assert reranked[1].rerank_score == 1.5
    assert reranked[2].chunk_id == c3.chunk_id
    assert reranked[2].rerank_score is None


# ---------------------------------------------------------------------------
# Service Search Validation & Edge Cases
# ---------------------------------------------------------------------------


def test_search_rejects_invalid_inputs() -> None:
    """Validates company_id, top_n bounds, and mode requirements."""
    service = RetrievalService(
        session_factory=MagicMock(),
        embedder=_DummyEmbedder(),
        token_counter=_DummyTokenCounter(count_val=5),
    )

    # 1. Non-UUID company_id raises TypeError
    with pytest.raises(TypeError, match="company_id must be a uuid.UUID"):
        service.search(company_id="not-a-uuid", query="hello")  # type: ignore[arg-type]

    cid = uuid4()
    # 2. top_n <= 0 raises RetrievalInputError
    with pytest.raises(RetrievalInputError, match="top_n must be positive"):
        service.search(company_id=cid, query="hello", top_n=0)

    # 3. top_n > RETRIEVAL_MAX_TOP_N raises RetrievalInputError
    with pytest.raises(RetrievalInputError, match="cannot exceed RETRIEVAL_MAX_TOP_N"):
        service.search(company_id=cid, query="hello", top_n=25)

    # 4. Invalid mode raises RetrievalInputError
    with pytest.raises(RetrievalInputError, match="Unsupported retrieval mode"):
        service.search(company_id=cid, query="hello", mode="unknown_mode")  # type: ignore[arg-type]

    # 5. hybrid+rerank requested when reranker is None raises RerankerConfigurationError
    with pytest.raises(
        RerankerConfigurationError, match="no reranker backend is configured"
    ):
        service.search(company_id=cid, query="hello", mode="hybrid+rerank")


# ---------------------------------------------------------------------------
# Settings & Deprecated Configuration Validation
# ---------------------------------------------------------------------------


def test_settings_retrieval_validation() -> None:
    """Verifies bounds and deprecated RERANK_TOP_N matching logic in Settings."""
    # 1. Valid settings with deprecated RERANK_TOP_N matching RETRIEVAL_TOP_N
    s_valid = Settings(
        _env_file=None,
        RETRIEVAL_TOP_N=5,
        RERANK_TOP_N=5,
        RETRIEVAL_MAX_TOP_N=10,
        RERANK_CANDIDATES=20,
    )
    assert s_valid.RETRIEVAL_TOP_N == 5

    # 2. RERANK_TOP_N != RETRIEVAL_TOP_N raises ValueError
    with pytest.raises(ValueError, match="RERANK_TOP_N .* must match RETRIEVAL_TOP_N"):
        Settings(
            _env_file=None,
            RETRIEVAL_TOP_N=5,
            RERANK_TOP_N=7,
        )

    # 3. RETRIEVAL_TOP_N > RETRIEVAL_MAX_TOP_N raises ValueError
    with pytest.raises(ValueError, match="cannot exceed RETRIEVAL_MAX_TOP_N"):
        Settings(
            _env_file=None,
            RETRIEVAL_TOP_N=15,
            RETRIEVAL_MAX_TOP_N=10,
        )

    # 4. RERANK_CANDIDATES < RETRIEVAL_TOP_N raises ValueError
    with pytest.raises(
        ValueError, match="RERANK_CANDIDATES .* cannot be less than RETRIEVAL_TOP_N"
    ):
        Settings(
            _env_file=None,
            RETRIEVAL_TOP_N=5,
            RERANK_CANDIDATES=3,
        )

    # 5. RERANKER_BACKEND == 'bge-m3' requires 40-char SHA revision
    with pytest.raises(
        ValueError, match="RERANKER_MODEL_REVISION must be a 40-character SHA"
    ):
        Settings(
            _env_file=None,
            RERANKER_BACKEND="bge-m3",
            RERANKER_MODEL_REVISION=None,
        )

    with pytest.raises(
        ValueError, match="RERANKER_MODEL_REVISION must be a 40-character SHA"
    ):
        Settings(
            _env_file=None,
            RERANKER_BACKEND="bge-m3",
            RERANKER_MODEL_REVISION="not_a_valid_40_char_hex_revision",
        )


# ---------------------------------------------------------------------------
# RetrievalService.search End-to-End Orchestration Tests
# ---------------------------------------------------------------------------


def test_service_search_all_modes() -> None:
    """Verifies search orchestration across hybrid, dense, keyword, and hybrid+rerank."""
    mock_session = MagicMock()
    mock_factory = MagicMock()
    mock_factory.return_value.__enter__.return_value = mock_session

    doc_id = uuid4()
    c1 = _make_chunk(doc_id, 1, "DocA.pdf", similarity=0.9, keyword_score=0.8)
    c2 = _make_chunk(doc_id, 2, "DocA.pdf", similarity=0.7, keyword_score=0.6)

    class _StubReranker:
        model_name = "stub"
        model_revision = "rev1"
        max_length = 1024

        def predict(
            self, pairs: list[tuple[str, str]], batch_size: int = 32
        ) -> list[float]:
            del batch_size
            return [2.5] * len(pairs)

    service = RetrievalService(
        session_factory=mock_factory,
        embedder=_DummyEmbedder(),
        reranker=_StubReranker(),
        token_counter=_DummyTokenCounter(count_val=5),
    )

    cid = uuid4()

    with (
        patch("app.services.retrieval.tenant_session") as mock_tenant_ctx,
        patch("app.services.retrieval.RetrievalRepository") as mock_repo_cls,
    ):
        mock_tenant_ctx.return_value.__enter__.return_value = mock_session
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.dense_search.return_value = [c1, c2]
        mock_repo.keyword_search.return_value = [c1, c2]

        # 1. Hybrid search
        res_hybrid = service.search(
            company_id=cid, query="deposit policy", mode="hybrid"
        )
        assert res_hybrid.mode == "hybrid"
        assert len(res_hybrid.chunks) == 2
        assert res_hybrid.embed_ms >= 0.0
        assert res_hybrid.dense_ms >= 0.0
        assert res_hybrid.keyword_ms >= 0.0
        assert res_hybrid.fuse_ms >= 0.0
        assert res_hybrid.rerank_ms == 0.0

        # 2. Dense search
        res_dense = service.search(company_id=cid, query="deposit policy", mode="dense")
        assert res_dense.mode == "dense"
        assert len(res_dense.chunks) == 2

        # 3. Keyword search
        res_kw = service.search(company_id=cid, query="deposit policy", mode="keyword")
        assert res_kw.mode == "keyword"
        assert len(res_kw.chunks) == 2

        # 4. Hybrid+rerank search
        res_rerank = service.search(
            company_id=cid, query="deposit policy", mode="hybrid+rerank"
        )
        assert res_rerank.mode == "hybrid+rerank"
        assert len(res_rerank.chunks) == 2
        assert res_rerank.rerank_ms >= 0.0


# ---------------------------------------------------------------------------
# RetrievalRepository Unit Tests
# ---------------------------------------------------------------------------


def test_repository_contracts_with_mock_session() -> None:
    """Verifies repository query construction and boundary conditions."""
    from app.repositories.retrieval import RetrievalRepository

    mock_session = MagicMock()
    cid = uuid4()

    # 1. Type validation
    with pytest.raises(TypeError, match="company_id must be a uuid.UUID"):
        RetrievalRepository(mock_session, "not-a-uuid")  # type: ignore[arg-type]

    repo = RetrievalRepository(mock_session, cid)

    # 2. Limit <= 0 returns empty immediately
    assert repo.dense_search([1.0] * 1024, limit=0) == []
    assert repo.keyword_search("query text", limit=0) == []

    # 3. Noise query returns empty immediately
    assert repo.keyword_search("!@#$%^&*()_+=-", limit=10) == []

    # 4. Dense search executes query and formats RetrievedChunk
    mock_row = MagicMock()
    mock_row.id = uuid4()
    mock_row.document_id = uuid4()
    mock_row.document_name = "Sample.pdf"
    mock_row.chunk_index = 0
    mock_row.page = 1
    mock_row.section = "Introduction"
    mock_row.content = "Company: C\nDocument: D\nSection: S\n\nBody text"
    mock_row.token_count = 25
    mock_row.distance = 0.15

    mock_session.execute.return_value.all.return_value = [mock_row]
    dense_out = repo.dense_search([1.0] * 1024, limit=5)
    assert len(dense_out) == 1
    assert dense_out[0].document_name == "Sample.pdf"
    assert pytest.approx(dense_out[0].dense_similarity, 1e-4) == 0.85
    assert dense_out[0].body == "Body text"

    # 5. Keyword search executes query and formats RetrievedChunk
    mock_kw_row = MagicMock()
    mock_kw_row.id = uuid4()
    mock_kw_row.document_id = uuid4()
    mock_kw_row.document_name = "Sample.pdf"
    mock_kw_row.chunk_index = 0
    mock_kw_row.page = 1
    mock_kw_row.section = "Introduction"
    mock_kw_row.content = "Company: C\nDocument: D\nSection: S\n\nBody text"
    mock_kw_row.token_count = 25
    mock_kw_row.rank_score = 0.42

    mock_session.execute.return_value.all.return_value = [mock_kw_row]
    kw_out = repo.keyword_search("sample text", limit=5)
    assert len(kw_out) == 1
    assert kw_out[0].document_name == "Sample.pdf"
    assert kw_out[0].keyword_score == 0.42


# ---------------------------------------------------------------------------
# CLI Search Command Unit Tests
# ---------------------------------------------------------------------------


def test_cli_search_command_table_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Verifies CLI search subcommand dispatches, formats output, and exits cleanly."""
    from app.cli import main as cli_main

    cid = uuid4()
    c1 = _make_chunk(cid, 1, "DocA.pdf", similarity=0.9)

    mock_result = MagicMock()
    mock_result.query = "deposit query"
    mock_result.mode = "hybrid"
    mock_result.chunks = [c1]
    mock_result.embed_ms = 1.2
    mock_result.dense_ms = 3.4
    mock_result.keyword_ms = 2.1
    mock_result.fuse_ms = 0.5
    mock_result.rerank_ms = 0.0
    mock_result.total_ms = 7.2

    mock_comp = MagicMock()
    mock_comp.id = cid
    mock_comp.name = "Himalaya Crest Realty (Pvt.) Ltd."

    with (
        patch("app.cli.get_session_factory"),
        patch("app.cli.admin_session"),
        patch("app.cli.CompanyRepository") as mock_repo_cls,
        patch("app.cli.create_embedder"),
        patch("app.cli.create_reranker"),
        patch("app.cli.RetrievalService") as mock_service_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = mock_comp
        mock_repo_cls.return_value = mock_repo

        mock_service = MagicMock()
        mock_service_cls.return_value = mock_service
        mock_service.search.return_value = mock_result

        # 1. Table format with exact company name and --query flag
        code = cli_main(
            [
                "search",
                "--company",
                "Himalaya Crest Realty (Pvt.) Ltd.",
                "--query",
                "deposit query",
            ]
        )
        assert code == 0
        captured = capsys.readouterr()
        assert "Rank" in captured.out
        assert "DocA.pdf" in captured.out
        assert "Timings:" in captured.out
        mock_repo.get_by_name.assert_called_with("Himalaya Crest Realty (Pvt.) Ltd.")

        # 2. JSON format with exact schema keys
        code_json = cli_main(
            [
                "search",
                "--company",
                "Himalaya Crest Realty (Pvt.) Ltd.",
                "--query",
                "deposit query",
                "--format",
                "json",
            ]
        )
        assert code_json == 0
        captured_json = capsys.readouterr()
        assert '"query": "deposit query"' in captured_json.out
        assert '"mode": "hybrid"' in captured_json.out
        assert '"content":' in captured_json.out
        assert '"token_count":' in captured_json.out


def test_retrieved_chunk_frozen_schema_and_contiguous_rank() -> None:
    """Verifies exact frozen public schema field list, contiguous rank, and metadata pass-through."""
    import dataclasses

    expected_fields = [
        "rank",
        "chunk_id",
        "document_id",
        "document_name",
        "chunk_index",
        "page",
        "section",
        "content",
        "body",
        "token_count",
        "dense_rank",
        "dense_similarity",
        "keyword_rank",
        "keyword_score",
        "rrf_score",
        "rerank_score",
        "score",
    ]
    actual_fields = [f.name for f in dataclasses.fields(RetrievedChunk)]
    assert actual_fields == expected_fields, (
        f"Field list mismatch: {actual_fields} != {expected_fields}"
    )

    # Verify pass-through and contiguous ranking via diversify
    service = RetrievalService(
        session_factory=MagicMock(),
        embedder=_DummyEmbedder(),
        token_counter=_DummyTokenCounter(count_val=5),
    )
    doc_id = uuid4()
    candidates = [
        _make_chunk(doc_id, idx, f"Doc_{idx}.pdf", similarity=0.9 - idx * 0.1)
        for idx in range(5)
    ]
    diversified = service.diversify(candidates, top_n=5)
    assert [c.rank for c in diversified] == [1, 2, 3, 4, 5]
    for idx, c in enumerate(diversified):
        assert c.page == idx + 1
        assert "Content for chunk" in c.content
        assert c.body == f"Content for chunk {idx}"
        assert c.token_count == 50


def test_keyword_sql_plainto_tsquery_and_no_websearch() -> None:
    """Verifies plainto_tsquery usage, absence of websearch_to_tsquery, parameterized query, and OR semantics."""
    from pathlib import Path

    from app.repositories.retrieval import build_plainto_or_tsquery

    # 1. Absence of websearch_to_tsquery in retrieval repo source
    repo_file = (
        Path(__file__).resolve().parents[2] / "app" / "repositories" / "retrieval.py"
    )
    repo_text = repo_file.read_text(encoding="utf-8")
    assert "websearch_to_tsquery" not in repo_text

    # 2. plainto_tsquery and OR semantics via replace in compiled SQL
    expr = build_plainto_or_tsquery("residential rent 100")
    compiled_sql = str(expr.compile())
    assert "plainto_tsquery" in compiled_sql
    assert "replace" in compiled_sql
    assert "to_tsquery" in compiled_sql

    # 3. Parameterized binding: query text is bound, not concatenated raw
    params = expr.compile().params
    assert any("residential rent 100" in str(v) for v in params.values())

    # 4. Domain entity and number preservation in token extraction
    tokens = extract_search_tokens(
        "HCR-ISB-0102 list price Rs. 15,000,000 rate 7.5% on 2026-11-10"
    )
    assert "hcr-isb-0102" in tokens
    assert any("15000000" in t or "15" in t for t in tokens)
    assert "7" in tokens
    assert "5" in tokens
    assert "2026-11-10" in tokens


def _load_eval_retrieval():
    import importlib.util
    from pathlib import Path

    eval_script = Path(__file__).resolve().parents[3] / "scripts" / "eval_retrieval.py"
    spec = importlib.util.spec_from_file_location("eval_retrieval", eval_script)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {eval_script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_eval_retrieval_hnsw_exactness_logic() -> None:
    """Verifies ordered HNSW equality passes when identical and fails on reordering."""
    eval_mod = _load_eval_retrieval()

    mock_session = MagicMock()
    cid = uuid4()
    u1, u2, u3 = uuid4(), uuid4(), uuid4()

    res_a = MagicMock()
    res_a.all.return_value = [(u1,), (u2,), (u3,)]
    res_b = MagicMock()
    res_b.all.return_value = [(u2,), (u1,), (u3,)]

    # Case A: Identical ordered lists -> mismatch == 0
    mock_session.execute.side_effect = [
        res_a,  # HNSW
        None,
        None,  # SET LOCAL
        res_a,  # Exact
        None,
        None,  # SET LOCAL
    ]
    mismatches, discrepancies = eval_mod.verify_hnsw_exactness(
        session=mock_session,
        company_id=cid,
        queries=["test query"],
        query_vectors=[[1.0] * 1024],
        top_k=3,
    )
    assert mismatches == 0
    assert discrepancies == []

    # Case B: Same set in different order -> mismatch_count == 1 (ordered failure)
    mock_session.execute.side_effect = [
        res_a,  # HNSW: u1, u2, u3
        None,
        None,
        res_b,  # Exact: u2, u1, u3
        None,
        None,
    ]
    mismatches_reordered, disc_reordered = eval_mod.verify_hnsw_exactness(
        session=mock_session,
        company_id=cid,
        queries=["test query"],
        query_vectors=[[1.0] * 1024],
        top_k=3,
    )
    assert mismatches_reordered == 1
    assert len(disc_reordered) == 1


def test_section_matching_exact_and_suffix() -> None:
    """Verifies exact case-insensitive and heading-path suffix matching with delimiter ' > '."""
    eval_mod = _load_eval_retrieval()

    # Exact normalized match
    assert eval_mod.match_section("Overview", "overview") is True
    assert eval_mod.match_section("  Property Listings  ", "property listings") is True

    # Suffix match with delimiter ' > '
    assert (
        eval_mod.match_section(
            "Himalaya Crest > Services and Fees > Commission Rates",
            "Commission Rates",
        )
        is True
    )
    assert eval_mod.match_section("Level 1 > Level 2 > Target", "target") is True

    # Non-suffix rejection
    assert eval_mod.match_section("Level 1 > Level 2 > Target", "Level 2") is False
    assert eval_mod.match_section("Level 1 > Level 2 > Target", "Level 1") is False

    # Disparate strings rejection
    assert eval_mod.match_section("FAQs", "About Us") is False
    assert eval_mod.match_section(None, "Overview") is False
    assert eval_mod.match_section("Overview", None) is False
