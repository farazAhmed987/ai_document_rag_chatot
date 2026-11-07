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
- Inspected Remote Baseline: `origin/main` at `68dedd4` (`68dedd4665d73059212def5bd56b51ebb06eed5a`).
- Code Merge SHA: `68dedd4665d73059212def5bd56b51ebb06eed5a`.
- Code CI URL: https://github.com/farazAhmed987/ai_document_rag_chatot/actions/runs/42385811834.
- Release Tag Target: `v0.4-chunking` (approved release tag).
- Tag Sequence: `v0.0-skeleton`, `v0.0-tooling`, `v0.1-sample-data`, `v0.2-database`, `v0.2.1-db-hardening` (`8c186d0`), `v0.3-extraction` (`65c4d8c`), `v0.3.1-extraction-hardening` (`92f4ad5`), `v0.4-chunking`.

## Phase 4 Status & Chunking Contract
- Snapshot: Phase 4 chunking passed, merged into main and tagged v0.4-chunking. Primary verification is 170/170 and related-document evidence verification is 176/176 using evidence map v1.0.1. Code merge SHA: 68dedd4665d73059212def5bd56b51ebb06eed5a; CI URL: https://github.com/farazAhmed987/ai_document_rag_chatot/actions/runs/42385811834. Phase 5 embedding is next but has not started.
- Locked decisions: Cross-document key facts list a related document only when it holds a needed component (see data/facts/key_facts.amendments.md). Evidence anchors match with number boundaries. Gitleaks checksum false positives allowlisted in .gitleaks.toml with compound condition.
- Contract: Contiguous non-table segments sharing page and section; chunks never cross page or section boundaries.
- Cross-doc facts are verified per related document through `data/facts/cross_doc_evidence.json` (A-6).
- Sizing: `MIN_CHUNK_TOKENS=80` (body); `overlap=60` (body); ceiling `CHUNK_TOKENS * 1.15` (incl header); `MAX_TABLE_TOKENS=1500` (incl header, lead-in, repeated headers). Subminimum allowed only for `sole_chunk_of_segment` and `unit_indivisible`. Small tails merge or rebalance.
- Errors: Oversized rows or headers raise `ChunkingBudgetError` (code `CHUNK_BUDGET_EXCEEDED`). Header cap is 25% of `CHUNK_TOKENS`.
- Privacy & Splitting: Atomic chunking; diagnostics omit content, headings, filename. Overlong plain-text runs use deterministic hard splitting.
- Non-blocking notes: N1–N7 recorded in `context/PROJECT_STATUS.md` (header budget, cross-page tables, transient metadata, unpinned tokenizer, CI test scope, minimal CI permissions, external samples).
- Governance: Phase 4 merged and released under `v0.4-chunking`. Phase 5 embedding is NOT started and requires explicit user instructions.
