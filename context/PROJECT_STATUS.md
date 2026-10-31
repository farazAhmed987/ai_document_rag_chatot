# Project Status
Last updated: 2026-10-31 | Updated by: Antigravity | Current phase: Phase 4 Stage 1 preflight (chunking tests authorized)

Rules for this file: no secrets or connection strings; keep it under about 150 lines; when the logs grow, move old entries to context/archive/status-YYYY-MM.md. Historical snapshots: context/archive/preflight-8735072/.

## Environment
- OS: Fedora Linux 44 | Python: 3.12.16 | venv: .venv at repo root | IDE: Antigravity
- Repo: https://github.com/farazAhmed987/ai_document_rag_chatot
- Inspected Remote Baseline: origin/main at 8735072 (8735072a4c48c0c0ae5ad2f2659cc1f05b8addd7)
- Preflight Docs Checkpoint: 26e89a0 (preflight decisions docs commit; not a code release)
- Local Main State: local main has documentation-only commits ahead of origin/main
- Release Tag Target: v0.3.1-extraction-hardening at 92f4ad5 (92f4ad56f9fa1068396c5cdfe6b6387396c519fa)
- Git Distinction: main is ahead of origin/main by documentation commits; release tag sits at 92f4ad5
- Operational database: PostgreSQL 17.11 on Supabase (pgvector 0.8.2, session pooler, RLS forced, rag_app role restricted)
- Analytics database: PostgreSQL 17.11 on Supabase (session pooler, independent migrations)
- RLS Live Verification: VERIFIED (pg_policies uses app.company_id on all 5 tables; rag_app has rolbypassrls=False, rolinherit=True; role-membership inheritance not audited in this preflight)
- LLM provider: not configured yet (Gemini planned)

## Phase tracker
| Phase | Name | Status | Tag | Gate passed on | Notes |
|---|---|---|---|---|---|
| 0 | Setup: skeleton, tooling (ruff, mypy, pytest, pre-commit, CI) | Passed | v0.0-skeleton / v0.0-tooling | 2026-10-29 | Tooling foundation established |
| 1 | Sample data: facts sheet, 5 PDFs, golden set draft | Passed | v0.1-sample-data | 2026-10-31 | 5 PDFs, 3 variants, 170 facts, determinism verified |
| 2 | Database schema and migrations (ops and analytics) | Passed | v0.2-database | 2026-10-31 | Dual migrations, pgvector, initial RLS |
| — | Hardening Part A: Database & tenant role isolation | Passed | v0.2.1-db-hardening | 2026-10-31 | Commit 8c186d0; rag_app role, forced RLS, 17 DB tests pass |
| 3 | Extraction and cleaning | Passed | v0.3-extraction | 2026-10-31 | Commit 65c4d8c; PDF, DOCX, TXT, MD extractors |
| — | Hardening Part B: Extraction, structure & CI hardening | Passed | v0.3.1-extraction-hardening | 2026-10-31 | Commit 92f4ad5; 100 items (99 pass, 1 opt skip), 90.75% cov, 170/170 facts |
| 4 | Chunking | Not started (Stage 1 tests authorized) | | | Preflight complete; no code or RED/GREEN evidence exists yet |
| 5 | Embedding module (BGE-M3) | Not started | | | NOT authorized |
| 6 | Ingestion service end to end (CLI) | Not started | | | NOT authorized |
| 7 | Retrieval (dense, keyword, RRF, rerank) and golden-set evaluation | Not started | | | |
| 8 | LLM answer, threshold gate, citations, Q&A logging | Not started | | | |
| 9 | FastAPI endpoints | Not started | | | |
| 10 | Frontend | Not started | | | |
| 11 | Hardening, README, diagram, demo video, LinkedIn post | Not started | | | |

## Current step
- Working on: Phase 4 Stage 1 (tests only authorized by Claude; awaiting revised test instructions)
- Last result: Preflight complete; live RLS verified with app.company_id; historical context archived
- Status: Phase 4 NOT passed. Phase 5 NOT authorized. No implementation or RED/GREEN evidence exists.
- Blocked by: Awaiting revised Stage 1 tests from ChatGPT

## Phase 4 Chunking Contract & Governance
- Segmentation: Contiguous non-table segments sharing page & section; chunks never cross page or section boundaries.
- Token Limits: MIN_CHUNK_TOKENS=80 (body); overlap=60 (body); non-table ceiling CHUNK_TOKENS * 1.15 (incl header).
- Subminimum Reasons: sole_chunk_of_segment and unit_indivisible only; small tails merge or rebalance.
- Tables: MAX_TABLE_TOKENS=1500 (incl header, lead-in, repeated headers).
- Errors & Limits: Oversized row or header raises ChunkingBudgetError (code CHUNK_BUDGET_EXCEEDED); header cap 25% of CHUNK_TOKENS.
- Privacy & Splitting: Atomic chunking; diagnostics omit content, headings, filename. Overlong text uses deterministic hard splitting.
- Workflow Rules: Stage 1 changes backend/tests/ only (no commit/push of RED). Phase branch pushed only after GREEN. Merge & tag require user's explicit "Phase 4 passed". Later context updates cite exact code SHA with "checks passed; awaiting Claude validation".
- Authoritative SRD: Attached SRD v1.0 authoritative; replace docs/SRD.md byte-for-byte in Stage 2 with SHA-256 evidence; amendments in docs/SRD_AMENDMENTS.md.

## Gate log (newest first, append only)
| Date | Phase | Commands run | Result | Commit or tag |
|---|---|---|---|---|
| 2026-10-31 | 4 Preflight | git status/rev-parse, pg_policies/pg_roles queries | PASS | Preflight complete; RLS verified |
| 2026-10-31 | Hardening Part B | Stage 1 RED (24 fail), Stage 2 GREEN pytest --cov, verify_facts, Gate B, fresh-clone | PASS | 92f4ad5 / v0.3.1-extraction-hardening |
| 2026-10-31 | 3 | ruff, mypy, pytest, verify_facts pipeline, Gate 3 | PASS | 65c4d8c / v0.3-extraction |
| 2026-10-31 | Hardening Part A | Stage 1 RED (13 fail), Stage 2 GREEN, alembic downgrade/upgrade, db_check | PASS | 8c186d0 / v0.2.1-db-hardening |
| 2026-10-31 | 2 | alembic upgrade ops & analytics, db_check, pytest -m db | PASS | 6d469f7 / v0.2-database |
| 2026-10-31 | 1 | build_sample_pdfs, verify_facts raw (170/170) | PASS | 6038bbe / v0.1-sample-data |
| 2026-10-29 | 0 | ruff, mypy, pytest, pre-commit | PASS | v0.0-skeleton / v0.0-tooling |

## Decisions log (newest first)
| Date | Decision | Reason |
|---|---|---|
| 2026-10-31 | Phase 4 Stage 1 (tests only) authorized; Phase 5 not authorized | Strict phase gating; chunking contract formalised before code |
| 2026-10-31 | RLS session setting is `app.company_id`; live policies verified | Matches SRD 9 and code; earlier context mentioning `app.current_company_id` was erroneous |
| 2026-10-31 | Role flags: rag_app has rolbypassrls=False, rolinherit=True | Observed via pg_roles; role-membership inheritance audit limitation noted |
| 2026-10-31 | SRD v1.0 authoritative; byte-for-byte replace in Stage 2 | Authoritative requirements document; amendments tracked in docs/SRD_AMENDMENTS.md |
| 2026-10-31 | Chunking budget: ceiling 1.15x, min 80 body tokens, overlap 60, tables 1500 | Prevents context fragmentation and budget blowup; ChunkingBudgetError on overflow |
| 2026-10-31 | Stage 1 RED tests never committed/pushed; user must confirm "Phase 4 passed" before merge/tag | Strict quality control and audit trail |
| 2026-10-31 | Torch in optional `ml` extra; CI uses fake embedder | Keeps CI fast and avoids multi-GB download per test run |
| 2026-10-31 | Reply language: RG-8 (user's language) adopted for Phase 8 | Replaces SRD 8.3 "always in English" for conversational Roman Urdu / English support |
| 2026-10-31 | Restrict `rag_app` role; enforce `FORCE ROW LEVEL SECURITY` | Prevent company data leaks and enforce session-scoped tenant isolation (MT-1, MT-3, MT-5) |
| 2026-10-31 | Strict `table_row` matching in pipeline fact verifier | Guarantees table cells are preserved on a single Markdown row (IN-1, IN-2) |
| 2026-10-28 | Use .venv, no Docker | Developer preference |
| 2026-10-28 | Support Markdown (.md) in addition to PDF, DOCX, TXT | Developer addition to SRD |
| 2026-10-28 | Separate analytics database for Q&A events | Future ML work (SRD 9.1) |

## Sample data inventory
Company name: Himalaya Crest Realty (Pvt.) Ltd.

| File | Location | Words | Notes |
|---|---|---|---|
| Company_Overview.md | data/sample_docs_src | 3874 | 13 H2, 11 tables |
| Property_Listings.md | data/sample_docs_src | 3764 | 24 property IDs, 3 tables (max 7 cols) |
| Services_and_Fees.md | data/sample_docs_src | 4104 | 17 H2, 12 tables |
| FAQs.md | data/sample_docs_src | 3106 | 60 FAQ questions, 11 H2 |
| Policies_and_Terms.md | data/sample_docs_src | 3669 | 16 H2, 8 tables |
| facts.md | data/facts | — | Master facts sheet |
| key_facts.json | data/facts | — | 170 facts (78 single, 39 table, 53 cross_doc) |
| golden_set_draft.json | eval | — | 54 questions (10 direct, 6 table, 6 multi_part, 8 multi_doc, 6 unanswerable, 6 trap, 8 roman_urdu, 4 follow_up); 46 en, 8 roman_urdu |
| omitted_topics.md | eval | — | 16 items |

- Accepted: FAQs 3106 words; regex false positives; KF-040 and KF-048 are 81 and 82 characters; G hits are only omitted-topics in facts.md; all 24 property IDs match.

## Known issues and open questions
- Mentor deadline (12 September 2026 in brief) has passed; real deadline remains unconfirmed with mentor.

## Session log (one line per session, newest first)
- 2026-10-31 | Preflight context corrections and historical archive created | Antigravity
- 2026-10-31 | Phase 4 preflight executed; live RLS verified; decisions recorded | Antigravity
- 2026-10-31 | GitHub sync verified; Part B hardening complete; documentation updated | Antigravity
- 2026-10-31 | Hardening Part B passed (extraction/cleaning/verifier; 90.75% cov; 170/170 facts) | Antigravity
- 2026-10-31 | Hardening Part A passed (db security, restricted role, RLS; commit 8c186d0) | Antigravity
- 2026-10-31 | Phase 3 passed (document extraction, cleaning, pipeline verifier; tag v0.3-extraction) | Antigravity
- 2026-10-31 | Phase 2 passed (dual databases on Supabase, Alembic migrations, pgvector) | Antigravity
- 2026-10-31 | Phase 1 passed (sample PDFs generated, 170 key facts verified) | Antigravity
- 2026-10-29 | Phase 0 tooling passed | Antigravity
- 2026-10-28 | sample data warnings resolved | Antigravity
- 2026-10-28 | 9 sample-data files placed, sanity-checked and pushed | Antigravity
- 2026-10-28 | Planning done; SRD v1 written; prompts for sample data and Phases 0 to 3 prepared | Claude
