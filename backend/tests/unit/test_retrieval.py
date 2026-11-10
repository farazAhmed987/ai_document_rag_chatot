"""Unit tests for hybrid retrieval service, RRF fusion, and diversification.

SRD Sections 8.2, 9; DM-3, IN-5, MT-1, MT-3; A-12.
"""

from __future__ import annotations

from unittest.mock import MagicMock
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
        RETRIEVAL_TOP_N=5,
        RERANK_TOP_N=5,
        RETRIEVAL_MAX_TOP_N=10,
        RERANK_CANDIDATES=20,
    )
    assert s_valid.RETRIEVAL_TOP_N == 5

    # 2. RERANK_TOP_N != RETRIEVAL_TOP_N raises ValueError
    with pytest.raises(ValueError, match="RERANK_TOP_N .* must match RETRIEVAL_TOP_N"):
        Settings(
            RETRIEVAL_TOP_N=5,
            RERANK_TOP_N=7,
        )

    # 3. RETRIEVAL_TOP_N > RETRIEVAL_MAX_TOP_N raises ValueError
    with pytest.raises(ValueError, match="cannot exceed RETRIEVAL_MAX_TOP_N"):
        Settings(
            RETRIEVAL_TOP_N=15,
            RETRIEVAL_MAX_TOP_N=10,
        )

    # 4. RERANK_CANDIDATES < RETRIEVAL_TOP_N raises ValueError
    with pytest.raises(
        ValueError, match="RERANK_CANDIDATES .* cannot be less than RETRIEVAL_TOP_N"
    ):
        Settings(
            RETRIEVAL_TOP_N=5,
            RERANK_CANDIDATES=3,
        )

    # 5. RERANKER_BACKEND == 'bge-m3' requires 40-char SHA revision
    with pytest.raises(
        ValueError, match="RERANKER_MODEL_REVISION must be a 40-character SHA"
    ):
        Settings(
            RERANKER_BACKEND="bge-m3",
            RERANKER_MODEL_REVISION=None,
        )

    with pytest.raises(
        ValueError, match="RERANKER_MODEL_REVISION must be a 40-character SHA"
    ):
        Settings(
            RERANKER_BACKEND="bge-m3",
            RERANKER_MODEL_REVISION="not_a_valid_40_char_hex_revision",
        )
