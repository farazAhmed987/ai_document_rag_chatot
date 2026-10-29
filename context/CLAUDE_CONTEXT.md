# Claude Context (read this first)
Purpose: lets a fresh Claude session continue this project without prior chat history. Reading order: (1) this file, (2) context/PROJECT_STATUS.md, (3) docs/SRD.md, only the sections relevant to the current phase, (4) other repo files only when needed. Keep this file under about 100 lines. No secrets.

## Roles and workflow
- The user coordinates. Claude is the project manager: architecture decisions, prompts for ChatGPT, review of reports and code, and upkeep of the context files.
- ChatGPT writes code from Claude-approved prompts. Antigravity (IDE agent) executes: creates files, installs, runs tests, debugs, commits, pushes.
- Loop per phase: ChatGPT delivers code, then Antigravity runs the RUN and GATE commands, then the user pastes REPORT BACK to Claude, then Claude reviews and replies with a verdict plus a CONTEXT UPDATE block, then Antigravity commits that update, then the user tells ChatGPT "Phase N passed".

## Project in brief
Multi-company RAG chatbot dashboard for the DevSynt AI Internship (Task 5). Companies upload PDF, DOCX, TXT or Markdown files; the system extracts, cleans, chunks, embeds and stores them; users chat and get grounded answers with sources and a clear "not found" when information is missing. Sample data: a fictional real-estate company with 5 consistent documents. Users may ask in English or Roman Urdu. Full requirements: docs/SRD.md. The fictional company is Himalaya Crest Realty (Pvt.) Ltd.; the 5 source documents are Company_Overview.md, Property_Listings.md, Services_and_Fees.md, FAQs.md and Policies_and_Terms.md in data/sample_docs_src/.

## Locked decisions
- Backend FastAPI (Python 3.11 or 3.12, .venv, no Docker); frontend React, TypeScript, Vite, Tailwind.
- Database: hosted Postgres with pgvector (Supabase). Shared tables with company_id plus row-level security; a separate analytics database stores all Q&A events for future ML (SRD 9.1).
- Embeddings: BAAI/bge-m3, 1024 dimensions, local. Chunks about 500 tokens, about 12 percent overlap, structure-aware, tables kept whole, context header prepended.
- Retrieval: dense plus Postgres full-text (simple config), merged with RRF, reranked with bge-reranker-v2-m3, relevance threshold gate before the LLM.
- LLM: external provider (Gemini planned) for query rewrite and answer; strict grounded prompt; citations validated. Tiered local rewrite is a deferred option.
- Quality: golden set (50 or more questions), pytest, ruff, mypy, pre-commit with gitleaks, GitHub Actions CI.
- ML improvement is future work: supervised methods and evaluation first, RL last (SRD section 20).

## Architecture summary
Ingestion: upload, validate, store, extract, clean, chunk, embed, store in pgvector, status indexed or failed.
Query: question and history, rewrite, embed, dense and keyword search for one company, RRF, rerank, threshold gate, prompt and LLM, answer with sources, log Q&A event.

## Repo map
    backend/app/{api,services,repositories,adapters,models,schemas,core}   application code
    backend/migrations/{ops,analytics}                                       Alembic environments
    backend/tests/{unit,integration,fixtures}
    frontend/   scripts/   eval/   data/{sample_docs_src,sample_docs,facts}
    docs/SRD.md   docs/architecture/   context/   context/archive/

## Conventions
Phases 0 to 11 are in PROJECT_STATUS.md. Branch phase-N-name, conventional commits, merge to main after the gate passes, tag v0.N-name. Every phase has a GATE; do not start the next phase until it passes.

## How Claude must respond
- Concise and step by step; give exact commands for Antigravity; validate each stage before the next; never invent library APIs; say when unsure.
- After any step that changes project state, end the reply with a CONTEXT UPDATE block: the new session-log line, rows to add to the tracker, gate and decision logs, and any changed sections of this file, as ready-to-paste text. Claude cannot write to GitHub; Antigravity commits the update.
- Treat the repo files as the source of truth if they conflict with older chat memory.

## Current state snapshot (Claude updates every step)
- Phase: 1 (build sample PDFs and verify facts). Last gate: Phase 0 passed (tag v0.0-skeleton, 2026-10-29). Next action: see PROJECT_STATUS.md.

## Open questions
- Confirm the real submission deadline with the mentor (brief says 12 September 2026, which has passed).
