# Project Status
Last updated: 2026-10-31 | Updated by: Antigravity | Current phase: Phase 3 complete / Phase 4 pending Claude review

Rules for this file: no secrets or connection strings; keep it under about 150 lines; when the logs grow, move old entries to context/archive/status-YYYY-MM.md.

## Environment
- OS: Fedora Linux 44 | Python: 3.12.16 | venv: .venv at repo root | IDE: Antigravity
- Repo: https://github.com/farazAhmed987/ai_document_rag_chatot
- Latest Commit: 92f4ad5 (full SHA: 92f4ad56f9fa1068396c5cdfe6b6387396c519fa)
- Latest Tag: v0.3.1-extraction-hardening
- Git Sync: local main, origin/main, and v0.3.1-extraction-hardening are aligned at 92f4ad5
- Operational database: PostgreSQL 17.11 on Supabase (pgvector 0.8.2, session pooler, RLS forced, rag_app role restricted)
- Analytics database: PostgreSQL 17.11 on Supabase (session pooler, independent migrations)
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
| 4 | Chunking | Not started | | | Pending independent Claude validation |
| 5 | Embedding module (BGE-M3) | Not started | | | |
| 6 | Ingestion service end to end (CLI) | Not started | | | |
| 7 | Retrieval (dense, keyword, RRF, rerank) and golden-set evaluation | Not started | | | |
| 8 | LLM answer, threshold gate, citations, Q&A logging | Not started | | | |
| 9 | FastAPI endpoints | Not started | | | |
| 10 | Frontend | Not started | | | |
| 11 | Hardening, README, diagram, demo video, LinkedIn post | Not started | | | |

## Current step
- Working on: Pre-flight interface capture and planning for Phases 4-6 (authorized by Claude)
- Last result: Claude provisionally accepted Phase 3 & Hardening Part B; pre-flight checks executed
- Blocked by: nothing (Phases 4-6 planning authorized by Claude)

## Validation Summary (Part B Test & Gate Results)
- Test Suite: 100 collected items (99 passed, 1 skipped: optional external samples, 0 failed; 17 db + 82 non-db).
- Code Coverage: TOTAL 90.75% (minimum 88.0%); pdf.py 91.10% (minimum 85.0%); ocr.py 88.89% (minimum 80.0%).
- Fact Verification: 170/170 key facts matched in both raw (exact_page: 142, table_cells: 39) and pipeline (exact_page: 142, table_row: 39) modes.
- Page Map Differences (8): KF-016 (running footer removed), KF-027, KF-028, KF-029 (personnel table isolated to p7), KF-053, KF-078, KF-079, KF-098 (strict table_row matching resolves to specific schedule/fee tables).
- Build Determinism: 2 independent sample PDF builds byte-identical across all 8 artifacts (5 PDFs + 3 variants).
- Database Gate: Ops at 0002_ops_hardening, Analytics at 0001_analytics_initial, RLS forced on 5 tables, rag_app role restricted.
- Remote Fresh-Clone Gate: Successfully cloned from remote into isolated tempdir; clean pip install, ruff check, ruff format, mypy, and pytest (-m "not db") passed (82 passed, 1 skipped).
- Latest Git Verification (Read-Only): git ls-remote confirmed HEAD, main, and v0.3.1-extraction-hardening all point to 92f4ad5 on GitHub.

## Gate log (newest first, append only)
| Date | Phase | Commands run | Result | Commit or tag |
|---|---|---|---|---|
| 2026-10-31 | Git Verification | git status, git fetch, git ls-remote, git diff | PASS | 92f4ad5 (aligned with origin/main) |
| 2026-10-31 | Hardening Part B | Stage 1 RED (24 fail), Stage 2 GREEN pytest --cov, verify_facts, Gate B, fresh-clone | PASS | 92f4ad5 / v0.3.1-extraction-hardening |
| 2026-10-31 | 3 | ruff, mypy, pytest, verify_facts pipeline, Gate 3 | PASS | 65c4d8c / v0.3-extraction |
| 2026-10-31 | Hardening Part A | Stage 1 RED (13 fail), Stage 2 GREEN, alembic downgrade/upgrade, db_check | PASS | 8c186d0 / v0.2.1-db-hardening |
| 2026-10-31 | 2 | alembic upgrade ops & analytics, db_check, pytest -m db | PASS | 6d469f7 / v0.2-database |
| 2026-10-31 | 1 | build_sample_pdfs, verify_facts raw (170/170) | PASS | 6038bbe / v0.1-sample-data |
| 2026-10-29 | 0 | ruff, mypy, pytest, pre-commit | PASS | v0.0-skeleton / v0.0-tooling |

## Decisions log (newest first)
| Date | Decision | Reason |
|---|---|---|
| 2026-10-31 | Claude provisionally accepts Phase 3 & Hardening B; authorizes Phases 4-6 planning | Reported validation metrics consistent across HEAD, origin/main, and tag 92f4ad5 |
| 2026-10-31 | Torch in optional `ml` extra; CI uses fake embedder | Keeps CI fast and avoids multi-GB download per test run |
| 2026-10-31 | Reply language: RG-8 (user's language) adopted for Phase 8 | Replaces SRD 8.3 "always in English" for conversational Roman Urdu / English support |
| 2026-10-31 | Authoritative RLS session setting name is `app.current_company_id` | Code is authoritative over SRD 9 `app.company_id` |
| 2026-10-31 | Table `<br>` regex unwraps mid-token digits and short word fragments | Fixes wrapped ReportLab table cells (e.g. HCR-ISB-0101, Abbottabad) without breaking separate words |
| 2026-10-31 | Restrict `rag_app` role; enforce `FORCE ROW LEVEL SECURITY` | Prevent company data leaks and enforce session-scoped tenant isolation (MT-1, MT-3, MT-5) |
| 2026-10-31 | Strict `table_row` matching in pipeline fact verifier | Guarantees table cells are preserved on a single Markdown row (IN-1, IN-2) |
| 2026-10-28 | Use .venv, no Docker | Developer preference |
| 2026-10-28 | Support Markdown (.md) in addition to PDF, DOCX, TXT | Developer addition to SRD |
| 2026-10-28 | Keep the external LLM for query rewriting; tiered local rewrite deferred | Revisit only if latency or rate limits hurt |
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

- Accepted: FAQs 3106 words; regex false positives; KF-040 and KF-048 are 81 and 82 characters (snippet limit relaxed to 100 in verify_facts); G hits are only the omitted-topics list in facts.md
- Extra property ID in facts.md: regex matched HCR-VAL-2020 on line 111, which is the prefix of valuation licence HCR-VAL-2020-0035 (not a property ID); all 24 actual property IDs match Property_Listings.md

## Known issues and open questions
- The brief's deadline (12 September 2026) is earlier than the date of the SRD; confirm the real deadline with the mentor.

## Session log (one line per session, newest first)
- 2026-10-31 | Claude authorized Phases 4-6; pre-flight checks executed | Antigravity
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
