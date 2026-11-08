"""Prepare or benchmark pinned BGE-M3 over Phase 4 sample chunks (IN-6).

Normal execution is offline. The optional in-memory cosine smoke test is
informational only and does not implement the production retrieval layer.
"""

from __future__ import annotations

import argparse
import json
import math
import resource
import time
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:
    from app.adapters.embedders.base import Embedder
    from app.adapters.embedders.bge_m3 import BgeM3Embedder
    from app.adapters.embedders.factory import create_embedder
except ImportError:
    Embedder = Any  # type: ignore[misc,assignment]
    BgeM3Embedder = Any  # type: ignore[misc,assignment]
    create_embedder = None  # type: ignore[assignment]
from app.adapters.extractors.factory import extract_document
from app.adapters.tokenizers.bge_m3 import BgeM3TokenCounter
from app.core.config import REPO_ROOT, Settings, get_settings
from app.services.chunking import chunk_document
from app.services.cleaning import clean_document

COMPANY = "Himalaya Crest Realty (Pvt.) Ltd."
DEFAULT_DOCS = REPO_ROOT / "data/sample_docs"
DEFAULT_GOLDEN = REPO_ROOT / "eval/golden_set_draft.json"
SMOKE_TYPES = frozenset({"direct", "table", "multi_part", "multi_doc", "roman_urdu"})


def cosine(left: list[float], right: list[float]) -> float:
    """Cosine equals dot product because adapters guarantee unit vectors."""
    return math.fsum(a * b for a, b in zip(left, right, strict=True))


def sample_chunks(
    docs_dir: Path, settings: Settings
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Run the real extraction-cleaning-chunking pipeline over five PDFs."""
    counter = BgeM3TokenCounter()
    records: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    paths = sorted(docs_dir.glob("*.pdf"))
    if len(paths) != 5:
        raise RuntimeError(f"Expected exactly 5 sample PDFs, found {len(paths)}")
    for path in paths:
        extracted = extract_document(path)
        cleaned = clean_document(extracted)
        chunks = chunk_document(
            cleaned,
            company_name=COMPANY,
            token_counter=counter,
            settings=settings,
        )
        counts[path.name] = len(chunks)
        records.extend(
            {
                "document": path.name,
                "page": chunk.page,
                "section": chunk.section,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
            }
            for chunk in chunks
        )
    return records, counts


def smoke_search(
    *,
    embedder: Embedder,
    records: list[dict[str, object]],
    vectors: list[list[float]],
    golden_path: Path,
    batch_size: int,
) -> dict[str, object]:
    """Report direct cosine Hit@1, Hit@5 and MRR without a pass threshold."""
    questions: list[dict[str, Any]] = json.loads(
        golden_path.read_text(encoding="utf-8")
    )
    selected = [row for row in questions if row.get("type") in SMOKE_TYPES]
    query_vectors = embedder.embed_documents(
        [str(row["question"]) for row in selected], batch_size=batch_size
    )
    rows: list[dict[str, object]] = []
    by_type: dict[str, list[dict[str, object]]] = defaultdict(list)
    for question, query_vector in zip(selected, query_vectors, strict=True):
        ranked = sorted(
            zip(records, vectors, strict=True),
            key=lambda item: cosine(query_vector, item[1]),
            reverse=True,
        )
        expected = {
            Path(str(source["document"])).stem
            for source in question.get("expected_sources", [])
        }
        first_rank = next(
            (
                rank
                for rank, (record, _) in enumerate(ranked, start=1)
                if Path(str(record["document"])).stem in expected
            ),
            None,
        )
        result: dict[str, object] = {
            "id": str(question["id"]),
            "type": str(question["type"]),
            "first_expected_rank": first_rank,
            "hit_at_1": first_rank == 1,
            "hit_at_5": first_rank is not None and first_rank <= 5,
            "reciprocal_rank": 0.0 if first_rank is None else 1.0 / first_rank,
            "top_document": str(ranked[0][0]["document"]),
            "top_page": ranked[0][0]["page"],
        }
        rows.append(result)
        by_type[str(question["type"])].append(result)

    def metrics(items: list[dict[str, object]]) -> dict[str, float | int]:
        total = len(items)
        return {
            "questions": total,
            "hit_at_1": sum(bool(row["hit_at_1"]) for row in items) / total,
            "hit_at_5": sum(bool(row["hit_at_5"]) for row in items) / total,
            "mrr": sum(float(row["reciprocal_rank"]) for row in items) / total,
        }

    return {
        "informational_only": True,
        "overall": metrics(rows),
        "by_type": {kind: metrics(items) for kind, items in sorted(by_type.items())},
        "questions": rows,
    }


def prepare(settings: Settings) -> None:
    """Explicitly download the pinned model and validate its dimension."""
    BgeM3Embedder.prepare_model(
        model_name=settings.EMBEDDING_MODEL_NAME,
        model_revision=settings.EMBEDDING_MODEL_REVISION,
        dimension=settings.EMBEDDING_DIMENSION,
        max_length=settings.EMBEDDING_MAX_LENGTH,
    )
    print(
        "MODEL_PREPARED",
        settings.EMBEDDING_MODEL_NAME,
        settings.EMBEDDING_MODEL_REVISION,
    )


def benchmark(
    *,
    docs_dir: Path,
    golden_path: Path,
    report_path: Path | None,
    include_smoke: bool,
) -> dict[str, object]:
    """Measure model load and sample embedding throughput on this CPU."""
    settings = get_settings()
    if settings.EMBEDDER_BACKEND != "bge-m3":
        raise RuntimeError("Benchmark requires EMBEDDER_BACKEND=bge-m3")
    records, document_counts = sample_chunks(docs_dir, settings)
    embedder = create_embedder(settings=settings)

    load_started = time.perf_counter()
    embedder.embed_query("BGE-M3 warmup")
    load_seconds = time.perf_counter() - load_started

    contents = [str(record["content"]) for record in records]
    embed_started = time.perf_counter()
    vectors = embedder.embed_documents(
        contents, batch_size=settings.EMBEDDING_BATCH_SIZE
    )
    embed_seconds = time.perf_counter() - embed_started
    total_seconds = load_seconds + embed_seconds
    throughput = len(contents) / embed_seconds
    peak_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    result: dict[str, object] = {
        "model_name": embedder.model_name,
        "model_revision": embedder.model_revision,
        "dimension": embedder.dimension,
        "batch_size": settings.EMBEDDING_BATCH_SIZE,
        "max_length": settings.EMBEDDING_MAX_LENGTH,
        "document_chunks": document_counts,
        "total_chunks": len(contents),
        "model_load_seconds": load_seconds,
        "embedding_seconds": embed_seconds,
        "total_seconds": total_seconds,
        "chunks_per_second": throughput,
        "peak_rss_kib": peak_rss_kib,
        "under_ten_minutes": total_seconds <= 600.0,
    }
    if include_smoke:
        result["retrieval_smoke"] = smoke_search(
            embedder=embedder,
            records=records,
            vectors=vectors,
            golden_path=golden_path,
            batch_size=settings.EMBEDDING_BATCH_SIZE,
        )

    for document, count in sorted(document_counts.items()):
        print(f"DOCUMENT {document} chunks={count}")
    print(f"MODEL_LOAD_SECONDS {load_seconds:.6f}")
    print(f"EMBEDDING_SECONDS {embed_seconds:.6f}")
    print(f"TOTAL_SECONDS {total_seconds:.6f}")
    print(f"CHUNKS_PER_SECOND {throughput:.6f}")
    print(f"PEAK_RSS_KIB {peak_rss_kib}")
    print(f"UNDER_TEN_MINUTES {total_seconds <= 600.0}")
    if include_smoke:
        smoke = cast_dict(result["retrieval_smoke"])
        print("RETRIEVAL_SMOKE", json.dumps(smoke, ensure_ascii=False, sort_keys=True))
    if report_path is not None:
        report_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return result


def cast_dict(value: object) -> dict[str, object]:
    """Keep report printing typed without depending on typing.cast at runtime."""
    if not isinstance(value, dict):
        raise TypeError("Expected mapping report")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-model", action="store_true")
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args(argv)
    if args.prepare_model:
        prepare(get_settings())
        return 0
    result = benchmark(
        docs_dir=args.docs_dir,
        golden_path=args.golden,
        report_path=args.report,
        include_smoke=not args.skip_smoke,
    )
    return 0 if bool(result["under_ten_minutes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
