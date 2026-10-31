# Claude Context (read this first)
Purpose: lets a fresh Claude session continue this project without prior chat history. Reading order: (1) this file, (2) context/PROJECT_STATUS.md, (3) docs/SRD.md, only the sections relevant to the current phase, (4) other repo files only when needed. Keep this file under about 100 lines. No secrets.

## Roles and workflow
- The user coordinates. Claude is the project manager: architecture decisions, prompts for ChatGPT, review of reports and code, and upkeep of the context files.
- ChatGPT writes code from Claude-approved prompts. Antigravity (IDE agent) executes: creates files, installs, runs tests, debugs, commits, pushes.
- Loop per phase: ChatGPT delivers code, then Antigravity runs the RUN and GATE commands, then the user pastes REPORT BACK to Claude, then Claude reviews and replies with a verdict plus a CONTEXT UPDATE block, then Antigravity commits that update, then the user tells ChatGPT "Phase N passed".

## Project in brief
Multi-company RAG chatbot dashboard for the DevSynt AI Internship (Task 5). Companies upload PDF, DOCX, TXT or Markdown files; the system extracts, cleans, chunks, embeds and stores them; users chat and get grounded answers with sources and a clear "not found" when information is missing. Sample data: a fictional real-estate company with 5 consistent documents (Himalaya Crest Realty (Pvt.) Ltd. in data/sample_docs_src/). Requirements: docs/SRD.md.

## Implemented Architecture & Security (Phases 0–3 + Hardening A & B)
- Database & Security (Phase 2 + Hardening Part A): Hosted dual PostgreSQL 17.11 on Supabase (operational and analytics). Relational models (`companies`, `documents`, `chunks`, `chat_sessions`, `messages`) with foreign keys (`ON DELETE CASCADE`), `company_id` multi-tenancy, and `FORCE ROW LEVEL SECURITY` on all tables. Dedicated unprivileged `rag_app` role (`BYPASSRLS` false, `NOINHERIT`) with session configuration `app.current_company_id`. Alembic dual migrations: ops at `0002_ops_hardening`, analytics at `0001_analytics_initial`.
- Extraction & Cleaning (Phase 3 + Hardening Part B): Format-aware factory:
  - PDF: `pymupdf4llm` page-by-page extraction with reading-order block fallback, scanned-page image coverage heuristics, and null/offline OCR handling.
  - DOCX: `python-docx` XML traversal handling paragraphs, tables, nested tables, horizontally merged cells, SDT content controls, and tracked deletions.
  - Text & Markdown: BOM and alternating-NUL UTF-16 detection, strict decoding, and `charset-normalizer` fallback.
  - Limits: Pre-extraction file-size, decompression ratio bomb checks, page-count limits, and extracted character caps.
  - Cleaning: NFKC unicode normalization with Arabic/Urdu ZWNJ/ZWJ preservation, dehyphenation, boundary-only running headers/footers stripping, format-barrier-preserving hard-wrap rejoining (PDF/TXT only), and table cell `<br>` token/word unwrapping.
  - Verification: Strict pipeline fact verifier requiring single-line `table_row` matches; 170/170 canonical facts verified.

## Git Baseline & Release Tags
- Latest Commit: `92f4ad5` (full SHA: `92f4ad56f9fa1068396c5cdfe6b6387396c519fa`).
- Latest Release Tag: `v0.3.1-extraction-hardening`.
- Status: Local `main`, `origin/main`, and `v0.3.1-extraction-hardening` point to `92f4ad5`.
- Release Sequence: `v0.0-skeleton`, `v0.0-tooling`, `v0.1-sample-data`, `v0.2-database`, `v0.2.1-db-hardening` (commit `8c186d0`), `v0.3-extraction` (commit `65c4d8c`), `v0.3.1-extraction-hardening` (commit `92f4ad5`).

## Hardening Verification & Review Considerations
- Both hardening stages passed test-first RED/GREEN gates: Part A passed 13 RED failures; Part B passed 24 RED failures.
- Previous Part B Validation: 99 passed, 1 skipped (`No optional external samples`), 0 failed. Overall coverage 90.75% (req >= 88.0%); PDF 91.10% (req >= 85.0%); OCR 88.89% (req >= 80.0%). Fresh clone gate passed cleanly.
- Page Map Differences (8): KF-016 (running footer removed), KF-027, KF-028, KF-029 (personnel directory isolated to p7), KF-053, KF-078, KF-079, KF-098 (strict `table_row` matching isolates exact schedule/fee rows).
- Module Coverage Note: `app/core/logging.py` currently shows 0% coverage (stand-alone structlog configuration utility not yet exercised by API tests).
- Read-Only Git Check: Antigravity ran remote verification confirming GitHub alignment with zero code modifications.

## Current State & Next Steps
- Current Status: Phases 0–3 and Hardening Parts A and B are COMPLETE. Claude provisionally accepted Phase 3 & Hardening Part B and authorized planning for Phases 4-6.
- Phase 4 Status: Planning underway; pre-flight interface capture and checks executed for ChatGPT prompt. Implementation pending interface approval.
- Next Planned Work: Phase 4 chunking (structure-aware, ~500 tokens, ~12% overlap, tables preserved whole, context header prepended), followed by Phase 5 (BGE-M3 embeddings, torch in optional ml extra) and Phase 6 (ingestion CLI).
