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
- `68dedd4` (`68dedd4665d73059212def5bd56b51ebb06eed5a`) is the Phase 4 code-merge commit only.
- Code Merge CI URL: https://github.com/farazAhmed987/ai_document_rag_chatot/actions/runs/42385811834.
- Final tagged-commit CI: https://github.com/farazAhmed987/ai_document_rag_chatot/actions/runs/42386247733.
- Tag Sequence: `v0.0-skeleton`, `v0.0-tooling`, `v0.1-sample-data`, `v0.2-database`, `v0.2.1-db-hardening` (`8c186d0`), `v0.3-extraction` (`65c4d8c`), `v0.3.1-extraction-hardening` (`92f4ad5`), `v0.4-chunking`.

## Phase 5 Status & Embedding Contract
- Snapshot: Phase 5 embedding implemented on branch `phase-5-embedding`. Implementation commit is `bc0bce1` (`bc0bce156916be0c5793d7428df690256c9ea209`); `deeda0f` is the pre-review context commit, not the post-update branch HEAD. Core/offline suite passed (218 passed, 1 skipped, 23 deselected, 89.79% cov), all embedder modules >=85% (bge_m3 93.88%, fake 93.75%, base 100%, factory 100%). Six model tests passed. Benchmark: 221.27s (<600s), 0.58 chunks/s, peak RSS 3.05 GB. Informational retrieval smoke: Hit@1 63.16%, Hit@5 94.74%, MRR 0.774. Phase 4 regression: 170/170 primary, 176/176 related.
- Phase 5 is on HOLD for Claude code inspection and clean-environment validation.
- Phase 5 is NOT passed, merged, or tagged. Phase 6 is NOT authorized. No Phase 6 schema migration is authorized.
- Locked decisions: A-3 (chunking budgets/segments), A-6 (cross-document evidence map v1.0.1, number boundaries), A-7 (CPU-only, normalized 1024-d BAAI/bge-m3 pinned to revision 5617a9f61b028005a4858fdac845db406aefb181, no prefix, max length 2048 with typed error on excess; fake embedder for core/CI without torch; benchmark under 10m), A-8 (default embedding batch size 16 selected from CPU benchmark).
- Non-blocking notes: N1–N7 recorded in `context/PROJECT_STATUS.md`.
- Governance: Phase 5 requires Claude's review and the user's explicit "Phase 5 passed" before merge/tag. Phase 6 is NOT authorized.
