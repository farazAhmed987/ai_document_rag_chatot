"""Informational pgvector cosine smoke search preview for Phase 6 (IN-1..IN-8).

Executes a deterministic six-question retrieval preview across ingested documents
stored in PostgreSQL using pinned BGE-M3 query embeddings and pgvector cosine distance.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

# Authoritative repository root and default paths
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN_PATH = REPO_ROOT / "eval" / "golden_set_draft.json"
DEFAULT_COMPANY = "Himalaya Crest Realty (Pvt.) Ltd."

# Exactly six fixed, explicitly named question IDs satisfying category distribution:
# - 3 English direct questions (Q001, Q002, Q003)
# - 1 table question (Q011)
# - 1 multi-document question (Q023)
# - 1 Roman Urdu question (Q045)
SELECTED_QUESTION_IDS: tuple[str, ...] = (
    "Q001",
    "Q002",
    "Q003",
    "Q011",
    "Q023",
    "Q045",
)

_URL_PATTERN = re.compile(r"postgres(ql)?://[^\s@]+@[^\s/]+/[^\s]+")
_PASS_PATTERN = re.compile(r":[^:@\s]+@")


def mask_sensitive_text(text_val: str) -> str:
    """Mask credentials and connection strings from error or output text."""
    masked = _URL_PATTERN.sub("postgresql://***:***@***/***", text_val)
    masked = _PASS_PATTERN.sub(":***@", masked)
    return masked


def select_smoke_questions(golden_path: Path) -> list[dict[str, Any]]:
    """Deterministically select and validate the 6 required smoke questions from the golden set."""
    if not golden_path.exists():
        raise FileNotFoundError(f"Golden dataset file not found: {golden_path}")

    raw_data: list[dict[str, Any]] = json.loads(golden_path.read_text(encoding="utf-8"))
    questions_by_id = {str(q["id"]): q for q in raw_data if "id" in q}

    selected: list[dict[str, Any]] = []
    for qid in SELECTED_QUESTION_IDS:
        if qid not in questions_by_id:
            raise ValueError(
                f"Required smoke question ID '{qid}' missing from golden set."
            )
        selected.append(questions_by_id[qid])

    # Validate category allocation:
    direct_en = [
        q for q in selected if q.get("type") == "direct" and q.get("language") == "en"
    ]
    if len(direct_en) != 3:
        raise ValueError(
            f"Expected exactly 3 English direct questions, found {len(direct_en)}"
        )

    table_qs = [q for q in selected if q.get("type") == "table"]
    if len(table_qs) != 1:
        raise ValueError(f"Expected exactly 1 table question, found {len(table_qs)}")

    multi_docs = [q for q in selected if q.get("type") in ("multi_doc", "multi_part")]
    if len(multi_docs) != 1:
        raise ValueError(
            f"Expected exactly 1 multi-document question, found {len(multi_docs)}"
        )

    roman_urdu = [
        q
        for q in selected
        if q.get("type") == "roman_urdu"
        or q.get("language") in ("ur-Latn", "roman_urdu")
    ]
    if len(roman_urdu) != 1:
        raise ValueError(
            f"Expected exactly 1 Roman Urdu question, found {len(roman_urdu)}"
        )

    return selected


def normalize_doc_stem(doc_name: str) -> str:
    """Normalize file name by stripping extension and whitespace."""
    return Path(doc_name).stem.strip().lower()


def evaluate_hit(
    expected_sources: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> bool:
    """Evaluate whether any expected source (and optional page) appears in candidates."""
    if not expected_sources:
        return False

    for exp in expected_sources:
        exp_doc = exp.get("document", "")
        exp_stem = normalize_doc_stem(exp_doc)
        exp_page = exp.get("page")

        for cand in candidates:
            cand_stem = normalize_doc_stem(cand.get("filename", ""))
            if exp_stem == cand_stem:
                if exp_page is not None:
                    if cand.get("page") == exp_page:
                        return True
                else:
                    return True
    return False


def build_result_row(
    question: dict[str, Any], top_candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    """Construct a structured summary dictionary for one question evaluation."""
    expected_sources = question.get("expected_sources", [])
    expected_desc = (
        ", ".join(
            f"{s.get('document', '')}"
            + (f" (p.{s['page']})" if s.get("page") is not None else "")
            for s in expected_sources
        )
        or "None"
    )

    ranked_desc = [
        f"{c.get('filename', '')}"
        + (f" (p.{c['page']})" if c.get("page") is not None else "")
        for c in top_candidates
    ]

    hit = evaluate_hit(expected_sources, top_candidates)
    q_type = question.get("type", "unknown")
    lang = question.get("language", "en")
    category = f"{q_type} ({lang})"

    return {
        "question_id": str(question.get("id", "UNKNOWN")),
        "category": category,
        "expected": expected_desc,
        "hit_at_5": hit,
        "ranked_sources": ranked_desc,
    }


def format_smoke_table(rows: list[dict[str, Any]]) -> str:
    """Format evaluation results into a deterministic, readable summary table."""
    lines: list[str] = []
    header = f"{'Question ID':<12} | {'Category':<18} | {'Expected Source/Page':<35} | {'Hit@5':<7} | {'Top-5 Returned Sources (doc, page)'}"
    lines.append(header)
    lines.append("-" * len(header))

    matched_count = 0
    for r in rows:
        hit_str = "HIT" if r["hit_at_5"] else "MISS"
        if r["hit_at_5"]:
            matched_count += 1
        ranked_str = ", ".join(r["ranked_sources"][:5])
        lines.append(
            f"{r['question_id']:<12} | {r['category']:<18} | {r['expected']:<35} | {hit_str:<7} | {ranked_str}"
        )

    lines.append("-" * len(header))
    lines.append(
        f"Informational matched: {matched_count}/{len(rows)} ({matched_count / len(rows) * 100:.1f}%)"
    )
    return "\n".join(lines)


def query_top_chunks(
    session: Session,
    company_id: UUID,
    query_vector: list[float],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Execute pgvector cosine distance query for chunks belonging to tenant."""
    from app.models.chunk import Chunk
    from app.models.document import Document

    stmt = (
        select(
            Document.filename,
            Chunk.page,
            Chunk.chunk_index,
            Chunk.embedding.cosine_distance(query_vector).label("distance"),
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.company_id == company_id)
        .order_by(text("distance ASC"))
        .limit(top_k)
    )

    results = session.execute(stmt).all()
    candidates: list[dict[str, Any]] = []
    for row in results:
        candidates.append(
            {
                "filename": row.filename,
                "page": row.page,
                "chunk_index": row.chunk_index,
                "distance": float(row.distance) if row.distance is not None else 1.0,
            }
        )
    return candidates


def run_smoke_search(
    company_name: str,
    golden_path: Path,
    top_k: int = 5,
    engine_override: Any = None,
) -> list[dict[str, Any]]:
    """Orchestrate informational smoke search and return structured evaluation rows."""
    try:
        # Import backend modules inside runner to keep CLI --help fast and isolated
        sys.path.insert(0, str(REPO_ROOT / "backend"))
        from app.adapters.embedders.factory import create_embedder
        from app.core.config import get_settings
        from app.repositories.base import admin_session, tenant_session
        from app.repositories.company import CompanyRepository
        from sqlalchemy import create_engine

        settings = get_settings()

        questions = select_smoke_questions(golden_path)

        if engine_override is not None:
            admin_engine = engine_override
            runtime_engine = engine_override
        else:
            admin_engine = create_engine(settings.require_database_url())
            runtime_engine = create_engine(settings.require_runtime_database_url())

        admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
        runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

        # 1. Resolve company identity via admin connection
        with admin_session(admin_factory) as admin_s:
            comp_repo = CompanyRepository(admin_s)
            comp = (
                comp_repo.find_by_name(company_name)
                if hasattr(comp_repo, "find_by_name")
                else comp_repo.get_by_name(company_name)
            )
            if comp is None:
                raise ValueError(f"Company '{company_name}' not found in database.")
            company_id = comp.id

        # 2. Embed queries using pinned BGE-M3 embedder
        embedder = create_embedder(settings=settings)
        queries = [str(q["question"]) for q in questions]
        query_vectors = embedder.embed_documents(
            queries, batch_size=settings.EMBEDDING_BATCH_SIZE
        )

        # 3. Query pgvector under tenant session
        rows: list[dict[str, Any]] = []
        with tenant_session(runtime_factory, company_id) as tenant_s:
            for q, vec in zip(questions, query_vectors, strict=True):
                candidates = query_top_chunks(
                    session=tenant_s,
                    company_id=company_id,
                    query_vector=vec,
                    top_k=top_k,
                )
                row = build_result_row(q, candidates)
                rows.append(row)

        return rows

    except Exception as exc:  # noqa: BLE001
        from app.core.ingest_errors import sanitize_ingestion_exception

        safe_msg = sanitize_ingestion_exception(exc, stage="smoke_search")
        print(f"Error during smoke search: {safe_msg}", file=sys.stderr)
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for smoke_search."""
    parser = argparse.ArgumentParser(
        description="Run Phase 6 informational pgvector cosine smoke search against ingested company documents."
    )
    parser.add_argument(
        "--company",
        type=str,
        default=DEFAULT_COMPANY,
        help=f"Target company name (default: '{DEFAULT_COMPANY}')",
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=DEFAULT_GOLDEN_PATH,
        help="Path to golden set json (default: eval/golden_set_draft.json)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of nearest chunks to retrieve per question (default: 5)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint for smoke_search CLI command."""
    parser = build_parser()
    args = parser.parse_args(argv)

    rows = run_smoke_search(
        company_name=args.company,
        golden_path=args.golden,
        top_k=args.top_k,
    )
    table_output = format_smoke_table(rows)
    print("\n" + table_output + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
