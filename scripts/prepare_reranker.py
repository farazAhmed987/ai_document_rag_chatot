"""Prepare and verify local offline caching for the cross-encoder reranker.

SRD Section 8.2; A-12.
Resolves the 40-character commit SHA from Hugging Face Hub, downloads model weights,
and verifies offline inference on CPU without network calls.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


def resolve_commit_sha(model_name: str) -> str:
    """Query Hugging Face Hub to resolve the immutable 40-character commit SHA."""
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is required; run in .venv with [ml] dependencies"
        ) from exc

    api = HfApi()
    info = api.model_info(model_name)
    if not info.sha:
        raise ValueError(f"Could not resolve commit SHA for model '{model_name}'")
    return str(info.sha)


def verify_offline_cross_encoder(
    model_name: str,
    revision: str,
    max_length: int = 1024,
) -> float:
    """Verify that CrossEncoder loads and predicts strictly offline."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    from sentence_transformers import CrossEncoder

    model = CrossEncoder(
        model_name_or_path=model_name,
        revision=revision,
        local_files_only=True,
        trust_remote_code=False,
        max_length=max_length,
        device="cpu",
    )

    test_pairs = [
        ("What is the security deposit?", "Security deposit is two months rent.")
    ]
    scores = model.predict(
        test_pairs,
        show_progress_bar=False,
        convert_to_numpy=True,
        activation_fn=None,
    )

    score_val = float(scores[0]) if hasattr(scores, "__getitem__") else float(scores)
    return score_val


def prepare_reranker(
    model_name: str = DEFAULT_MODEL,
    revision: str | None = None,
    max_length: int = 1024,
    check_only: bool = False,
) -> str:
    """Download, cache and verify offline loading for the CrossEncoder reranker."""
    from app.core.config import get_settings

    settings = get_settings()

    if check_only:
        target_revision = revision or settings.RERANKER_MODEL_REVISION
        if not target_revision:
            raise ValueError(
                "RERANKER_MODEL_REVISION is not set in settings or provided via --revision"
            )
        score = verify_offline_cross_encoder(
            model_name=model_name,
            revision=target_revision,
            max_length=max_length,
        )
        print(f"OFFLINE_VERIFIED: True (sample raw logit score: {score:.4f})")
        return target_revision

    # Online step: resolve latest SHA
    commit_sha = revision or resolve_commit_sha(model_name)
    print(f"Resolving model '{model_name}' at revision '{commit_sha}'...")

    # Online download pass
    from sentence_transformers import CrossEncoder

    CrossEncoder(
        model_name_or_path=model_name,
        revision=commit_sha,
        local_files_only=False,
        trust_remote_code=False,
        max_length=max_length,
        device="cpu",
    )

    # Offline verification pass
    score = verify_offline_cross_encoder(
        model_name=model_name,
        revision=commit_sha,
        max_length=max_length,
    )

    print(f"MODEL_NAME: {model_name}")
    print(f"MODEL_REVISION: {commit_sha}")
    print(f"STATUS: PREPARED_AND_VERIFIED_OFFLINE (sample raw logit: {score:.4f})")
    return commit_sha


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser for prepare_reranker."""
    parser = argparse.ArgumentParser(
        description="Download and verify offline BAAI/bge-reranker-v2-m3 CrossEncoder model."
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"Model repository name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--revision",
        type=str,
        default=None,
        help="Optional specific commit SHA to prepare/check",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        default=False,
        help="Perform offline cache verification only without network access",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=1024,
        help="Maximum cross-encoder sequence length (default: 1024)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for prepare_reranker script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        prepare_reranker(
            model_name=args.model,
            revision=args.revision,
            max_length=args.max_length,
            check_only=args.check_only,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error preparing reranker: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
