"""Golden-set retrieval evaluation and HNSW exactness verification.

SRD Section 8.2; A-12.
Evaluates the 54 questions in eval/golden_set_draft.json across retrieval modes:
dense, keyword, hybrid without cap, hybrid with cap, and optional hybrid+rerank.
Evaluates gate thresholds on answerable questions (excluding follow_up):
- Document Hit@5 >= 0.90
- Document MRR >= dense-only MRR
- Multi-doc all-docs-hit >= dense-only value
- HNSW vs Exact Cosine top-20 check on all 54 queries.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.adapters.embedders.factory import create_embedder
from app.adapters.rerankers.factory import create_reranker
from app.core.config import Settings, get_settings
from app.models.chunk import Chunk
from app.models.document import Document
from app.repositories.base import admin_session, tenant_session
from app.repositories.company import CompanyRepository
from app.schemas.retrieval import RetrievedChunk
from app.services.retrieval import RetrievalService

DEFAULT_GOLDEN_PATH = REPO_ROOT / "eval" / "golden_set_draft.json"
DEFAULT_COMPANY = "Himalaya Crest Realty (Pvt.) Ltd."
ANSWERABLE_TYPES = frozenset(
    {"direct", "table", "multi_part", "multi_doc", "roman_urdu"}
)


def normalize_doc_stem(doc_name: str) -> str:
    """Normalize file name by stripping extension and whitespace."""
    return Path(doc_name).stem.strip().lower()


def load_golden_set(path: Path) -> list[dict[str, Any]]:
    """Load and validate the 54-question golden set."""
    if not path.exists():
        raise FileNotFoundError(f"Golden dataset file not found: {path}")
    data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    if len(data) != 54:
        raise ValueError(f"Expected exactly 54 golden set questions, found {len(data)}")
    return data


def evaluate_question(
    question: dict[str, Any],
    retrieved_chunks: list[RetrievedChunk],
) -> dict[str, Any]:
    """Calculate retrieval metrics for a single golden question."""
    expected_sources = question.get("expected_sources", [])
    expected_stems = {
        normalize_doc_stem(s["document"]) for s in expected_sources if "document" in s
    }
    retrieved_stems = [normalize_doc_stem(c.document_name) for c in retrieved_chunks]

    # Document Hit@1 and Hit@5
    hit_at_1 = bool(retrieved_stems and retrieved_stems[0] in expected_stems)
    hit_at_5 = bool(any(stem in expected_stems for stem in retrieved_stems[:5]))

    # Mean Reciprocal Rank (MRR)
    mrr = 0.0
    for rank_idx, stem in enumerate(retrieved_stems[:5], start=1):
        if stem in expected_stems:
            mrr = 1.0 / rank_idx
            break

    # Multi-doc metrics
    multi_doc_recall = 0.0
    all_docs_hit = 0.0
    if expected_stems:
        found_in_top5 = expected_stems & set(retrieved_stems[:5])
        multi_doc_recall = len(found_in_top5) / len(expected_stems)
        all_docs_hit = 1.0 if expected_stems.issubset(set(retrieved_stems[:5])) else 0.0

    # Section-level agreement (informational only)
    section_matches = 0
    total_expected_sections = sum(1 for s in expected_sources if s.get("section"))
    if total_expected_sections > 0:
        for chunk in retrieved_chunks[:5]:
            c_doc = normalize_doc_stem(chunk.document_name)
            c_sec = (chunk.section or "").strip().lower()
            for exp in expected_sources:
                e_doc = normalize_doc_stem(exp.get("document", ""))
                e_sec = (exp.get("section") or "").strip().lower()
                if (
                    c_doc == e_doc
                    and e_sec
                    and (c_sec == e_sec or c_sec.endswith(e_sec) or e_sec in c_sec)
                ):
                    section_matches += 1
                    break
        section_hit_at_5 = bool(section_matches > 0)
    else:
        section_hit_at_5 = False

    return {
        "question_id": question.get("id"),
        "type": question.get("type"),
        "language": question.get("language"),
        "expected_documents": sorted(expected_stems),
        "retrieved_documents": retrieved_stems[:5],
        "hit_at_1": hit_at_1,
        "hit_at_5": hit_at_5,
        "mrr": mrr,
        "multi_doc_recall": multi_doc_recall,
        "all_docs_hit": all_docs_hit,
        "section_hit_at_5": section_hit_at_5,
    }


def compute_aggregate_metrics(eval_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate benchmark metrics across a collection of question evaluations."""
    total = len(eval_rows)
    if total == 0:
        return {
            "questions": 0,
            "hit_at_1": 0.0,
            "hit_at_5": 0.0,
            "mrr": 0.0,
            "multi_doc_recall": 0.0,
            "all_docs_hit": 0.0,
            "section_hit_at_5": 0.0,
        }

    # Filter for multi_doc questions
    multi_doc_rows = [r for r in eval_rows if r["type"] == "multi_doc"]
    multi_count = len(multi_doc_rows)

    return {
        "questions": total,
        "hit_at_1": sum(1 for r in eval_rows if r["hit_at_1"]) / total,
        "hit_at_5": sum(1 for r in eval_rows if r["hit_at_5"]) / total,
        "mrr": sum(r["mrr"] for r in eval_rows) / total,
        "multi_doc_recall": (
            sum(r["multi_doc_recall"] for r in multi_doc_rows) / multi_count
            if multi_count > 0
            else 0.0
        ),
        "all_docs_hit": (
            sum(r["all_docs_hit"] for r in multi_doc_rows) / multi_count
            if multi_count > 0
            else 0.0
        ),
        "section_hit_at_5": sum(1 for r in eval_rows if r["section_hit_at_5"]) / total,
    }


def verify_hnsw_exactness(
    session: Session,
    company_id: UUID,
    queries: list[str],
    query_vectors: list[list[float]],
    top_k: int = 20,
) -> tuple[float, list[dict[str, Any]]]:
    """Compare top-20 chunk IDs retrieved via HNSW index vs exact brute-force cosine distance."""
    discrepancies: list[dict[str, Any]] = []
    total_recall = 0.0

    for idx, (query_text, vec) in enumerate(zip(queries, query_vectors, strict=True)):
        stmt = (
            select(Chunk.id)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.company_id == company_id,
                Document.company_id == company_id,
                Document.status == "indexed",
            )
            .order_by(Chunk.embedding.cosine_distance(vec).asc(), Chunk.id.asc())
            .limit(top_k)
        )

        # 1. HNSW query
        hnsw_ids = [r[0] for r in session.execute(stmt).all()]

        # 2. Exact brute force scan
        session.execute(text("SET LOCAL enable_indexscan = off;"))
        session.execute(text("SET LOCAL enable_bitmapscan = off;"))
        exact_ids = [r[0] for r in session.execute(stmt).all()]
        session.execute(text("SET LOCAL enable_indexscan = on;"))
        session.execute(text("SET LOCAL enable_bitmapscan = on;"))

        overlap = len(set(hnsw_ids) & set(exact_ids))
        recall = overlap / top_k if top_k > 0 else 1.0
        total_recall += recall

        if recall < 1.0:
            discrepancies.append(
                {
                    "query_index": idx,
                    "query": query_text[:50],
                    "overlap": overlap,
                    "recall": recall,
                }
            )

    mean_recall = total_recall / len(queries) if queries else 1.0
    return mean_recall, discrepancies


def run_evaluation(
    golden_path: Path,
    company_name: str = DEFAULT_COMPANY,
    settings: Settings | None = None,
    output_path: Path | None = None,
) -> bool:
    """Run full golden set evaluation across retrieval modes and verify gate requirements."""
    cfg = settings or get_settings()
    questions = load_golden_set(golden_path)

    admin_engine = create_engine(cfg.require_database_url())
    runtime_engine = create_engine(cfg.require_runtime_database_url())
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    # 1. Resolve company ID
    with admin_session(admin_factory) as admin_s:
        comp_repo = CompanyRepository(admin_s)
        comp = comp_repo.get_by_name(company_name)
        if comp is None:
            raise ValueError(f"Company '{company_name}' not found in database")
        company_id = comp.id

    # 2. Build service and embedder
    embedder = create_embedder(settings=cfg)
    reranker = create_reranker(settings=cfg)
    service = RetrievalService(
        session_factory=runtime_factory,
        embedder=embedder,
        reranker=reranker,
        settings=cfg,
    )

    queries = [str(q["question"]) for q in questions]
    print(f"Pre-computing query embeddings for {len(queries)} questions...")
    t0_embed = time.perf_counter()
    query_vectors = embedder.embed_documents(
        queries, batch_size=cfg.EMBEDDING_BATCH_SIZE
    )
    embed_duration = time.perf_counter() - t0_embed
    print(f"Computed {len(query_vectors)} embeddings in {embed_duration:.2f}s.")

    # 3. HNSW vs Exact Cosine Check across all 54 queries
    print("\nRunning HNSW vs Exact Cosine top-20 check on all 54 queries...")
    with tenant_session(runtime_factory, company_id) as ts:
        hnsw_recall, discrepancies = verify_hnsw_exactness(
            session=ts,
            company_id=company_id,
            queries=queries,
            query_vectors=query_vectors,
            top_k=20,
        )
    print(f"HNSW vs Exact Cosine mean recall@20: {hnsw_recall * 100:.2f}%")
    if discrepancies:
        print(f"Minor recall differences observed on {len(discrepancies)} queries.")
    else:
        print(
            "HNSW index produced 100% identical top-20 candidate sets to exact brute-force."
        )

    # 4. Evaluate modes
    modes_to_test = [
        ("dense", "Dense only (k=20, top_n=5)", "dense", 3),
        ("keyword", "Keyword only (k=20, top_n=5)", "keyword", 3),
        ("hybrid_nocap", "Hybrid without cap (k=20, top_n=5)", "hybrid", 10),
        ("hybrid_cap", "Hybrid with cap (k=20, cap=3, top_n=5)", "hybrid", 3),
    ]
    if reranker is not None:
        modes_to_test.append(
            ("hybrid_rerank", "Hybrid with cap + rerank (top_n=5)", "hybrid+rerank", 3)
        )

    mode_results: dict[str, Any] = {}

    for mode_key, mode_label, s_mode, per_doc_cap in modes_to_test:
        print(f"\nEvaluating mode: {mode_label}...")
        service.settings.RETRIEVAL_MAX_PER_DOC = per_doc_cap

        q_evals: list[dict[str, Any]] = []
        total_latencies: list[float] = []

        for q in questions:
            res = service.search(
                company_id=company_id,
                query=q["question"],
                top_n=5,
                mode=s_mode,
            )
            total_latencies.append(res.total_ms)
            eval_data = evaluate_question(q, res.chunks)
            q_evals.append(eval_data)

        # Separate answerable evaluations (excluding follow_up)
        answerable_evals = [e for e in q_evals if e["type"] in ANSWERABLE_TYPES]
        agg_answerable = compute_aggregate_metrics(answerable_evals)
        agg_all = compute_aggregate_metrics(q_evals)
        avg_latency = (
            sum(total_latencies) / len(total_latencies) if total_latencies else 0.0
        )

        # Per category breakdown
        by_category: dict[str, Any] = {}
        for cat in sorted(ANSWERABLE_TYPES):
            cat_rows = [e for e in answerable_evals if e["type"] == cat]
            by_category[cat] = compute_aggregate_metrics(cat_rows)

        mode_results[mode_key] = {
            "label": mode_label,
            "answerable": agg_answerable,
            "overall": agg_all,
            "by_category": by_category,
            "avg_latency_ms": avg_latency,
            "eval_rows": q_evals,
        }

    # Reset default cap
    service.settings.RETRIEVAL_MAX_PER_DOC = cfg.RETRIEVAL_MAX_PER_DOC

    # 5. Print Comparison Table
    print("\n" + "=" * 95)
    print(
        "PHASE 7 RETRIEVAL BENCHMARK SUMMARY (Answerable Questions excluding follow_up, N=38)"
    )
    print("=" * 95)
    header = (
        f"{'Mode':<36} | {'Hit@1':<7} | {'Hit@5':<7} | {'MRR':<7} | "
        f"{'Multi-Rec':<9} | {'All-Docs':<8} | {'Avg Latency'}"
    )
    print(header)
    print("-" * len(header))

    for m_info in mode_results.values():
        ans = m_info["answerable"]
        lat = m_info["avg_latency_ms"]
        print(
            f"{m_info['label']:<36} | "
            f"{ans['hit_at_1'] * 100:>6.1f}% | "
            f"{ans['hit_at_5'] * 100:>6.1f}% | "
            f"{ans['mrr']:>7.3f} | "
            f"{ans['multi_doc_recall'] * 100:>8.1f}% | "
            f"{ans['all_docs_hit'] * 100:>7.1f}% | "
            f"{lat:>7.1f} ms"
        )
    print("-" * len(header))

    # Print Category Breakdown for primary gate mode (hybrid with cap)
    primary_mode = mode_results["hybrid_cap"]
    print("\nPER-CATEGORY BREAKDOWN: Hybrid with cap (3 per doc, top_n=5):")
    print(
        f"{'Category':<15} | {'Questions':<10} | {'Hit@1':<8} | {'Hit@5':<8} | {'MRR':<8} | {'All-Docs-Hit'}"
    )
    print("-" * 65)
    for cat, metrics in primary_mode["by_category"].items():
        q_count = metrics["questions"]
        adh_str = f"{metrics['all_docs_hit'] * 100:.1f}%" if cat == "multi_doc" else "-"
        print(
            f"{cat:<15} | {q_count:<10} | "
            f"{metrics['hit_at_1'] * 100:>6.1f}%  | "
            f"{metrics['hit_at_5'] * 100:>6.1f}%  | "
            f"{metrics['mrr']:>8.3f} | {adh_str}"
        )
    print("-" * 65)

    # Informational section agreement and exact required page disclaimer
    sec_hit = primary_mode["answerable"]["section_hit_at_5"]
    print(f"\nSection agreement Hit@5 (informational): {sec_hit * 100:.1f}%")
    print(
        "Page-level metrics not computed: the approved golden set contains no page locators."
    )

    # 6. Verify Gate Thresholds
    dense_metrics = mode_results["dense"]["answerable"]
    gate_metrics = primary_mode["answerable"]

    passed_hit5 = gate_metrics["hit_at_5"] >= 0.90
    passed_mrr = gate_metrics["mrr"] >= dense_metrics["mrr"]
    passed_all_docs = gate_metrics["all_docs_hit"] >= dense_metrics["all_docs_hit"]
    passed_hnsw = hnsw_recall >= 0.95

    print("\n" + "=" * 65)
    print("PHASE 7 RETRIEVAL GATE VERIFICATION:")
    print("=" * 65)
    print(
        f"1. Document Hit@5 >= 0.90:         {gate_metrics['hit_at_5'] * 100:.2f}% "
        f"({'PASS' if passed_hit5 else 'FAIL'})"
    )
    print(
        f"2. MRR >= dense-only ({dense_metrics['mrr']:.3f}):     {gate_metrics['mrr']:.3f} "
        f"({'PASS' if passed_mrr else 'FAIL'})"
    )
    print(
        f"3. All-Docs >= dense ({dense_metrics['all_docs_hit'] * 100:.1f}%):       "
        f"{gate_metrics['all_docs_hit'] * 100:.1f}% "
        f"({'PASS' if passed_all_docs else 'FAIL'})"
    )
    print(
        f"4. HNSW vs Exact Cosine (>= 0.95):  {hnsw_recall * 100:.2f}% "
        f"({'PASS' if passed_hnsw else 'FAIL'})"
    )
    print("=" * 65)

    gate_passed = bool(passed_hit5 and passed_mrr and passed_all_docs and passed_hnsw)
    print(f"OVERALL GATE VERDICT: {'PASS' if gate_passed else 'FAIL'}\n")

    if output_path is not None:
        report_data = {
            "timestamp": time.time(),
            "company": company_name,
            "gate_passed": gate_passed,
            "hnsw_vs_exact_recall_20": hnsw_recall,
            "modes": {
                k: {
                    "label": v["label"],
                    "answerable": v["answerable"],
                    "overall": v["overall"],
                    "avg_latency_ms": v["avg_latency_ms"],
                }
                for k, v in mode_results.items()
            },
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
        print(f"Evaluation report written to {output_path}")

    return gate_passed


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for eval_retrieval."""
    parser = argparse.ArgumentParser(
        description="Phase 7 golden set retrieval evaluation and gate verification."
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=DEFAULT_GOLDEN_PATH,
        help=f"Path to golden set json (default: {DEFAULT_GOLDEN_PATH})",
    )
    parser.add_argument(
        "--company",
        type=str,
        default=DEFAULT_COMPANY,
        help=f"Target company name (default: {DEFAULT_COMPANY})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON evaluation report",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for eval_retrieval."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        passed = run_evaluation(
            golden_path=args.golden,
            company_name=args.company,
            output_path=args.output,
        )
        return 0 if passed else 1
    except Exception as exc:  # noqa: BLE001
        print(f"Error during retrieval evaluation: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
