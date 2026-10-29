# Project Status
Last updated: 2026-10-28 | Updated by: Antigravity | Current phase: Phase 0 (skeleton done, tooling pending)

Rules for this file: no secrets or connection strings; keep it under about 150 lines; when the logs grow, move old entries to context/archive/status-YYYY-MM.md.

## Environment
- OS: Fedora Linux | Python: 3.12.16 | venv: .venv at repo root | IDE: Antigravity
- Repo: https://github.com/farazAhmed987/ai_document_rag_chatot
- Operational database: not created yet (Supabase project, name to be recorded here, never credentials)
- Analytics database: not created yet
- LLM provider: not configured yet (Gemini planned)

## Phase tracker
| Phase | Name | Status | Tag | Gate passed on | Notes |
|---|---|---|---|---|---|
| 0 | Setup: skeleton, tooling (ruff, mypy, pytest, pre-commit, CI) | In progress (skeleton done) | v0.0-skeleton | | |
| 1 | Sample data: facts sheet, 5 PDFs, golden set draft | In progress (source files placed, PDFs not built yet) | | | 5 docs, 170 key facts, 54 golden questions; sanity checks resolved |
| 2 | Database schema and migrations (ops and analytics) | Not started | | | |
| 3 | Extraction and cleaning | Not started | | | |
| 4 | Chunking | Not started | | | |
| 5 | Embedding module (BGE-M3) | Not started | | | |
| 6 | Ingestion service end to end (CLI) | Not started | | | |
| 7 | Retrieval (dense, keyword, RRF, rerank) and golden-set evaluation | Not started | | | |
| 8 | LLM answer, threshold gate, citations, Q&A logging | Not started | | | |
| 9 | FastAPI endpoints | Not started | | | |
| 10 | Frontend | Not started | | | |
| 11 | Hardening, README, diagram, demo video, LinkedIn post | Not started | | | |

## Current step
- Working on: Phase 1 preparation
- Last result: 9 sample-data files placed, sanity-checked and pushed (6d59196)
- Blocked by: nothing

## Next actions
1. Claude reviews the sanity report and decides fixes
2. Finish Phase 0 tooling through ChatGPT if not done
3. Phase 1 through ChatGPT: build the sample PDFs and verify facts
4. Create the two hosted Postgres databases with pgvector before Phase 2

## Gate log (newest first, append only)
| Date | Phase | Commands run | Result | Commit or tag |
|---|---|---|---|---|

## Decisions log (newest first)
| Date | Decision | Reason |
|---|---|---|
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
- 2026-10-28 | sample data warnings resolved | Antigravity
- 2026-10-28 | 9 sample-data files placed, sanity-checked and pushed | Antigravity
- 2026-10-28 | Planning done; SRD v1 written; prompts for sample data and Phases 0 to 3 prepared | Claude
