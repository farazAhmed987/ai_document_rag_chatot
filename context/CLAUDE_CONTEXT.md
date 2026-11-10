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
- Phase 6 Main Merge CI URL: https://github.com/farazAhmed987/ai_document_rag_chatot/actions/runs/42796649207.
- Tag Sequence: `v0.0-skeleton`, `v0.0-tooling`, `v0.1-sample-data`, `v0.2-database`, `v0.2.1-db-hardening` (`8c186d0`), `v0.3-extraction` (`65c4d8c`), `v0.3.1-extraction-hardening` (`92f4ad5`), `v0.4-chunking` (`c861425`), `v0.5-embedding` (`5e2bf49`), `v0.6-ingestion` (`0ac88d0`).

## Phase 5 Status & Embedding Contract
- Snapshot: Phase 5 passed. Merged into main at `5e2bf49b32af8ba20b4692870eaf314ef0f644db` (`5e2bf49`) and tagged `v0.5-embedding`.
- Option A dependency alignment (A-9): `torch==2.15.0+cpu`, `sentence-transformers==6.2.0`, `transformers==5.22.2`, `tokenizers==0.23.3`, `huggingface-hub==2.4.0`.
- Locked decisions: A-3 (chunking budgets/segments), A-6 (cross-document evidence map v1.0.1, number boundaries), A-7 (CPU-only, normalized 1024-d BAAI/bge-m3 pinned to revision 5617a9f61b028005a4858fdac845db406aefb181, no prefix, max length 2048 with typed error on excess; fake embedder for core/CI without torch; benchmark under 10m), A-8 (default embedding batch size 16 selected from CPU benchmark), A-9 (Option A dependency alignment).

## Phase 6 Status & Ingestion Contract (A-11 / D1 Option A)
- Snapshot: Phase 6 passed. User authorized release on 2026-11-10 ("Phase 6 passed") following Claude PASS. Merged into main at `0ac88d0194c15bf55f6163e2c40adbd8c232235b` (`0ac88d0`) and tagged `v0.6-ingestion`. Tested code commit: `dcfc9d501323bfc9d45f7dfb931f19e30464d875`. Main CI passed: https://github.com/farazAhmed987/ai_document_rag_chatot/actions/runs/42796649207. Fresh GitHub clone: 305 passed, 1 skipped, 89.18% cov. DB suite 27 passed on runtime rag_app login (A-11). Real ingestion: 5/5 indexed, 126 chunks, 588.56 s (ceiling 900 s). Smoke (informational, any-expected-source Hit@5): 5/6; Q023 retrieved 1 of 2 expected documents.
- Current step: Phase 7 pre-flight. Phase 7 NOT yet authorized. New process from Phase 7: single-stage implementation with tests, full gate once per phase.
- Data note: company "Himalaya Crest Realty (Pvt.) Ltd." and its 5 indexed documents / 126 chunks are kept in the ops DB for Phase 7. Do not delete or reprocess without Claude approval.
- Known issues: Q003/Q023 retrieval misses (Phase 7); runtime variance on this CPU; deadline unconfirmed.
- Direct `rag_app` connection: Service connects directly via `RUNTIME_DATABASE_URL` under `rag_app` (`rolsuper=false`, `rolbypassrls=false`). RLS is enforced continuously without `SET LOCAL ROLE` privilege-escalation risk. Administrative tasks use `admin_session` via `DATABASE_URL`.
- ChunkDraft->chunks mapping (no migration): persist `chunk_index`, `page`, `section`, `content` (header+body), `token_count`; `embedding` populated by embedder; `tsv` generated by PostgreSQL expression, never written; transient chunker metadata dropped.
- Ingestion error handling: errors stored in `documents.error_message` formatted as `"[CODE] safe message"` (<=500 chars, no raw document content); failures emit structured logs and typed `StatusPersistFailedError` if failure recording fails.
- Tokenizer & embedder hardening (Prep 6A): tokenizer revision pinned to `EMBEDDING_MODEL_REVISION` and module-level `prepare_model` alias removed; chunk fingerprint `9e3c4ee12f868ad3df7ea9f7fdb6d79f829beb2eb42756fbe607c88063a6b809` remains unchanged.
- Reprocess lifecycle: idempotent chunk replacement accepting `indexed`, `failed`, or `processing` status.
- Transaction boundaries: embedding occurs strictly outside database transactions; chunk replacement is executed in one short atomic tenant transaction; failed document status is written in a new separate transaction after rollback.

