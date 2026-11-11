# Claude Context (read this first)
Purpose: lets a fresh Claude session continue this project without prior chat history. Reading order: (1) this file, (2) context/PROJECT_STATUS.md, (3) docs/SRD.md, only the sections relevant to the current phase, (4) other repo files only when needed. Keep this file under about 100 lines. No secrets.

## Roles and workflow
- The user coordinates. Claude is the project manager: architecture decisions, prompts for ChatGPT, review of reports and code, and upkeep of the context files.
- ChatGPT writes code from Claude-approved prompts. Antigravity (IDE agent) executes: creates files, installs, runs tests, debugs, commits, pushes.
- Loop per phase: ChatGPT delivers code, then Antigravity runs RUN and GATE commands and reports back to ChatGPT, then ChatGPT checks evidence and prepares REPORT TO CLAUDE for the user to paste, then Claude reviews and replies with a verdict plus a CONTEXT UPDATE block, then Antigravity commits that update, then the user tells ChatGPT "Phase N passed".

## Project in brief
Multi-company RAG chatbot dashboard for the DevSynt AI Internship (Task 5). Companies upload PDF, DOCX, TXT or Markdown files; the system extracts, cleans, chunks, embeds and stores them; users chat and get grounded answers with sources and a clear "not found" when information is missing. Sample data: fictional real-estate company with 5 consistent documents (Himalaya Crest Realty (Pvt.) Ltd. in data/sample_docs_src/). Requirements: docs/SRD.md.

## Implemented Architecture & Security (Phases 0–3 + Hardening A & B)
- Database & Security (Phase 2 + Hardening Part A): Hosted dual PostgreSQL 17.11 on Supabase (operational and analytics). Relational models (`companies`, `documents`, `chunks`, `chat_sessions`, `messages`) with foreign keys (`ON DELETE CASCADE`), `company_id` multi-tenancy, and `FORCE ROW LEVEL SECURITY` on all tables. Dedicated unprivileged `rag_app` role (`rolbypassrls=False`, `rolinherit=True`; role-membership inheritance not audited in this preflight). RLS setting `app.company_id` verified live on `pg_policies` across all 5 tenant tables (SRD 9). Earlier context naming `app.current_company_id` was incorrect. Alembic migrations: ops at `0002_ops_hardening`, analytics at `0001_analytics_initial`.
- Extraction & Cleaning (Phase 3 + Hardening Part B): Format-aware factory:
  - PDF: `pymupdf4llm` page-by-page extraction with reading-order block fallback, scanned-page heuristics, and offline OCR handling.
  - DOCX: `python-docx` XML traversal for paragraphs, tables, nested tables, horizontally merged cells, SDT, and tracked changes.
  - Text & Markdown: BOM and alternating-NUL UTF-16 detection, strict decoding, and `charset-normalizer` fallback.
  - Safety Limits: Pre-extraction file-size, zip bomb decompression ratio, page limits, and extracted character caps.
  - Cleaning: NFKC normalization with Arabic/Urdu ZWNJ/ZWJ preservation, dehyphenation, boundary-only running headers/footers stripping, format-barrier-preserving hard-wrap rejoining (PDF/TXT only), and table cell `<br>` token/word unwrapping.
  - Verification: Strict pipeline fact verifier requiring single-line `table_row` matches; 170/170 canonical facts verified.

## Git Baseline & Release Tags
- Phase 4 `main` and `v0.4-chunking` target are `c861425` (`c8614259002399f165e4bf3f183ec37d25deeb18`).
- Phase 5 merge SHA: `5e2bf49` (`5e2bf49b32af8ba20b4692870eaf314ef0f644db`); tagged `v0.5-embedding`.
- Phase 6 tested code SHA: `dcfc9d5` (`dcfc9d501323bfc9d45f7dfb931f19e30464d875`).
- Phase 6 merge SHA: `0ac88d0` (`0ac88d0194c15bf55f6163e2c40adbd8c232235b`).
- `v0.6-ingestion` release tag target: `0ac88d0194c15bf55f6163e2c40adbd8c232235b`.
- Authorization-record correction commit: `881ab922b5a50bd9c8b4fb314bae94ed403a611b`.
- Ratification docs commit: `cbc02a7b28991f0c2fb02a407b942e2ecbda78d3`.
- Phase 6 Main Merge CI URL: https://github.com/farazAhmed987/ai_document_rag_chatot/actions/runs/42796649207.
- Tag Sequence: `v0.0-skeleton`, `v0.0-tooling`, `v0.1-sample-data`, `v0.2-database`, `v0.2.1-db-hardening` (`8c186d0`), `v0.3-extraction` (`65c4d8c`), `v0.3.1-extraction-hardening` (`92f4ad5`), `v0.4-chunking` (`c861425`), `v0.5-embedding` (`5e2bf49`), `v0.6-ingestion` (`0ac88d0`).

## Phase 5 Status & Embedding Contract
- Snapshot: Phase 5 passed. Merged into main at `5e2bf49b32af8ba20b4692870eaf314ef0f644db` (`5e2bf49`) and tagged `v0.5-embedding`.
- Option A dependency alignment (A-9): `torch==2.15.0+cpu`, `sentence-transformers==6.2.0`, `transformers==5.22.2`, `tokenizers==0.23.3`, `huggingface-hub==2.4.0`.
- Locked decisions: A-3 (chunking budgets/segments), A-6 (cross-document evidence map v1.0.1, number boundaries), A-7 (CPU-only, normalized 1024-d BAAI/bge-m3 pinned to revision 5617a9f61b028005a4858fdac845db406aefb181, no prefix, max length 2048 with typed error on excess; fake embedder for core/CI without torch; benchmark under 10m), A-8 (default embedding batch size 16 selected from CPU benchmark), A-9 (Option A dependency alignment).

## Phase 6 Status & Ingestion Contract (A-11 / D1 Option A)
- Snapshot: Phase 6 passed. Claude technical PASS: 2026-11-10. User ratification: verbatim “phase 6 passed”, dated 2026-11-10. Tag v0.6-ingestion targets `0ac88d0194c15bf55f6163e2c40adbd8c232235b`. Tested code commit: `dcfc9d501323bfc9d45f7dfb931f19e30464d875`. Main merge CI: https://github.com/farazAhmed987/ai_document_rag_chatot/actions/runs/42796649207. Authorization-record correction: `881ab922b5a50bd9c8b4fb314bae94ed403a611b`. Ratification docs commit: `cbc02a7b28991f0c2fb02a407b942e2ecbda78d3`. Process deviation N8 remains recorded as a resolved historical process deviation.
- Commit references: Tested code commit: `dcfc9d501323bfc9d45f7dfb931f19e30464d875`; merge commit: `0ac88d0194c15bf55f6163e2c40adbd8c232235b`; release tag: `v0.6-ingestion` pointing to `0ac88d0194c15bf55f6163e2c40adbd8c232235b`.
- Evidence: Fresh GitHub clone: 305 passed, 1 skipped, 89.18% cov. DB suite 27 passed through direct runtime rag_app login (A-11). Real ingestion: 5/5 indexed, 34 pages, 126 chunks, 588.56 s (ceiling 900 s). Main CI passed: https://github.com/farazAhmed987/ai_document_rag_chatot/actions/runs/42796649207. Smoke preview: informational any-expected-source Hit@5: 5/6; Q023 retrieved 1 of 2 expected documents.

## Phase 7 Status & Retrieval Contract (A-12)
- Current step: Phase 7 corrective gates complete; awaiting Claude validation. Phase 7 is NOT passed, merged or tagged.
- Baseline: Phase 6 Passed (user ratification “phase 6 passed” 2026-11-10; Claude PASS 2026-11-10; process deviation N8 remains historical). Main base `9dfa8f89006556f8310380d5d1537aa99952f7bc`; branch `phase-7-retrieval`. Tested code commit: `d9718971a8489e3bc9cc19d6e18b2568202253b8`.
- Gate Evidence:
  - Static: Ruff check, Ruff format, and Mypy clean (`Success: no issues found in 54 source files`).
  - Core unit suite: 333 passed, 1 skipped, 35 deselected in 59.85s; total coverage 89.98% (threshold >= 88.0%).
  - New module coverage: `schemas/retrieval.py` 100%, `repositories/retrieval.py` 99.07%, `services/retrieval.py` 95.67%, `rerankers/base.py` 100%, `rerankers/fake.py` 100%, `rerankers/factory.py` 86.36%, `rerankers/bge_reranker.py` 90.91% (all >= 85.0%).
  - DB suite: Integration test passed in 13.90s under runtime `rag_app`; `rolbypassrls=False`, read-only transaction (`SET TRANSACTION READ ONLY`), tenant isolation, and page match verified.
  - Model tests: 1 passed in 36.76s in offline mode (`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`).
  - Public Result Schema: Frozen 17 fields in exact required order with `body` stripped of context header (`strip_context_header`).
  - Keyword SQL: Parameterized `to_tsquery('simple', replace(plainto_tsquery('simple', :query)::text, ' & ', ' | '))` with zero raw string concatenation; ordered by `ts_rank_cd DESC, id ASC`.
  - CLI: `search --company "<exact name>" --query "<query>"` resolves name via admin session before running retrieval in runtime `rag_app` session.
  - Ordered HNSW vs Exact Cosine: 54/54 ordered top-20 lists matched exact cosine (0 mismatches).
  - Golden Set 5-Mode Benchmark (Answerable N=38):
    * Dense: Hit@1 63.2%, Hit@5 100.0%, MRR 0.778, Multi-doc recall 56.2%, All-docs 12.5%, p50 1331.1ms, p95 2051.8ms
    * Keyword: Hit@1 76.3%, Hit@5 100.0%, MRR 0.877, Multi-doc recall 93.8%, All-docs 87.5%, p50 1024.1ms, p95 1162.9ms
    * Hybrid (no cap): Hit@1 81.6%, Hit@5 100.0%, MRR 0.893, Multi-doc recall 75.0%, All-docs 50.0%, p50 1435.8ms, p95 2050.7ms
    * Hybrid (cap=3): Hit@1 81.6%, Hit@5 100.0%, MRR 0.893, Multi-doc recall 75.0%, All-docs 50.0%, p50 1538.4ms, p95 2051.3ms
    * Hybrid + Real Reranker: Hit@1 65.8%, Hit@5 100.0%, MRR 0.799, Multi-doc recall 87.5%, All-docs 75.0%, p50 61338.5ms, p95 97073.9ms
  - Reranker Adoption Check: Evaluated, not adopted (MRR 0.799 < 0.893 regressed, p95 97.07s > 8.0s); keeping `RERANKER_BACKEND=none`.
  - Golden Set Gate (Hybrid with cap, Answerable N=38): Document Hit@5 = 100.0% (>= 90%), MRR = 0.893 (>= dense 0.778), Multi-doc All-Docs-Hit = 50.0% (>= dense 12.5%), HNSW mismatches = 0 (ALL PASS).
  - Informational: Page-level metrics not computed: the approved golden set contains no page locators. Section agreement Hit@5 = 0.0% due to heading hierarchy naming divergence (42 unmatched sections listed).


