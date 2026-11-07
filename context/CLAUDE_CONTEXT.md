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
- Inspected Remote Baseline: `origin/main` at `8735072` (`8735072a4c48c0c0ae5ad2f2659cc1f05b8addd7`).
- Preflight Docs Checkpoint: `26e89a0` (docs commit; not a code release).
- Local Main State: local `main` has documentation-only commits ahead of `origin/main`.
- Release Tag Target: `v0.3.1-extraction-hardening` at `92f4ad5` (`92f4ad56f9fa1068396c5cdfe6b6387396c519fa`).
- Distinction: local `main` is ahead of `origin/main` by documentation commits while the release tag sits at `92f4ad5`; they are not identical. Historical snapshots archived at `context/archive/preflight-8735072/`.
- Tag Sequence: `v0.0-skeleton`, `v0.0-tooling`, `v0.1-sample-data`, `v0.2-database`, `v0.2.1-db-hardening` (`8c186d0`), `v0.3-extraction` (`65c4d8c`), `v0.3.1-extraction-hardening` (`92f4ad5`).

## Phase 4 Status & Chunking Contract
- Status: Phase 4 chunking implemented on branch phase-4-chunking (not merged, not tagged). Evidence map v1.0.1 (176 pairs). Awaiting Claude's review of the chunking code and the user's 'Phase 4 passed'. Phases 5 and 6 not authorized.
- Locked decisions: Cross-document key facts list a related document only when it holds a needed component (see data/facts/key_facts.amendments.md).
- Contract: Contiguous non-table segments sharing page and section; chunks never cross page or section boundaries.
- Cross-doc facts are verified per related document through `data/facts/cross_doc_evidence.json` (A-6).
- Sizing: `MIN_CHUNK_TOKENS=80` (body); `overlap=60` (body); ceiling `CHUNK_TOKENS * 1.15` (incl header); `MAX_TABLE_TOKENS=1500` (incl header, lead-in, repeated headers). Subminimum allowed only for `sole_chunk_of_segment` and `unit_indivisible`. Small tails merge or rebalance.
- Errors: Oversized rows or headers raise `ChunkingBudgetError` (code `CHUNK_BUDGET_EXCEEDED`). Header cap is 25% of `CHUNK_TOKENS`.
- Privacy & Splitting: Atomic chunking; diagnostics omit content, headings, filename. Overlong plain-text runs use deterministic hard splitting.
- Governance: Stage 1 modifies only `backend/tests/`; RED tests are not committed or pushed. Phase branch pushed only after technical GREEN. Merge and release tagging require user's explicit "Phase 4 passed". Later context updates cite exact code SHA with "checks passed; awaiting Claude validation" in a separate docs-only commit.
- SRD Authority: Attached SRD v1.0 is authoritative; replace docs/SRD.md byte-for-byte in Stage 2 with SHA-256 evidence; amendments in docs/SRD_AMENDMENTS.md.
- Review Considerations: All prior test results (99 passed, 1 skipped, 90.75% coverage, 170/170 facts) are historical validation from Phase 3 / Hardening B at tag `92f4ad5`, not newly rerun during this preflight. Full evidence preserved in `context/archive/preflight-8735072/`. External sample test skipped; `app/core/logging.py` at 0% coverage; mentor deadline unconfirmed.
