# Approved SRD amendments

The byte-exact SRD v1.0 is kept in `docs/SRD.md`. Claude approved A-1 through A-5
on 2026-10-31 and A-6 on 2026-11-03. This file records amendments without
rewriting the SRD.

## A-1 — RLS session setting

The authoritative source and operational policies use `app.company_id`, as in
SRD section 9. Earlier context references to `app.current_company_id` were wrong.
The supplied preflight report observed `rag_app.rolbypassrls=False` and
`rolinherit=True`; no role/policy/migration change is authorized in Phase 4.

## A-2 — Reply language

RG-8 governs future answer generation: follow the user's language. This
supersedes the contradictory "always English" sentence in section 8.3.

## A-3 — Phase 4 chunking budgets and segments

- A prose segment is a maximal contiguous non-table run sharing page and
  section. Tables and heading occurrences are segment barriers. Keep original
  PDF page numbers; other formats have no synthetic page number.
- Priority: page/section boundaries, then the content ceiling, then minimum
  body size. Context values collapse whitespace to keep a three-line header.
- `CHUNK_TOKENS=500` is a content target; ceiling is `floor(target * 1.15)`.
  Content includes the context header. `MIN_CHUNK_TOKENS=80` counts body tokens.
  `CHUNK_OVERLAP=60` counts body tokens and never crosses a segment or table.
- Small trailing fragments merge when within the ceiling, otherwise rebalance
  whole selected units. If impossible, keep the fragment and list the exception.
  Allowed reasons are `sole_chunk_of_segment` and `unit_indivisible` only.
  Empty bodies and heading-only sections produce no chunk.
- `MAX_TABLE_TOKENS=1500` includes the context header, any attached lead-in,
  repeated table header row and separator. Whole rows are indivisible; parts
  repeat table headers and carry contiguous `i/n` labels. Lead-in attachment is
  optional and cannot cause overflow.
- A required table row exceeding the table limit raises `ChunkingBudgetError`
  (`CHUNK_BUDGET_EXCEEDED`). A context header above 25% of `CHUNK_TOKENS` raises
  that error with sub-reason `HEADER_TOO_LARGE`. No silent truncation.
- Diagnostics contain only code, sub-reason, page, unit index, observed tokens
  and limit. Chunking returns a complete list or raises; no partial result.
- Plain prose falls back through paragraph, line, sentence and word boundaries;
  overlong runs use deterministic lossless windows and `hard_split` debug logs.
- The pure result `ChunkDraft` carries transient segment/exception metadata;
  the persistence schema is unchanged.

## A-4 — Project phase numbering

The execution plan uses: 0 tooling, 1 sample data, 2 database, 3 extraction,
4 chunking, 5 embedding, 6 ingestion CLI, 7 retrieval, 8 grounded answer service,
9 API, 10 frontend, 11 hardening/documentation. This differs from SRD section 3.
Every phase remains subject to the user's explicit "Phase N passed" transition.
Claude's later workflow amendment requires technical GREEN before branch push,
and explicit phase approval before merge to main and release tagging.

## A-5 — Deadline

The mentor deadline remains unconfirmed. Do not infer a replacement deadline
from the historical date in the SRD.

## A-6 — Cross-document verification contract (2026-11-03)

Cross-document verification contract: the primary snippets are checked in the
primary document; each related document is checked against a versioned,
source-specific evidence map. Original facts and sample documents are unchanged.

- Primary gate: all 170 primary snippet sets occur in ONE actual primary-source
  chunk. Related gate: every declared `(fact_id, related document)` pair has an
  approved source-specific snippet set in ONE actual chunk of that document.
  Both gates must pass independently; no combined percentage or partial credit.
- Every relation in `key_facts.json` counts, including relations attached to
  `single` and `table` facts. Current unmodified data declares 183 unique pairs.
- `data/facts/cross_doc_evidence.json` uses schema version 1, a versioned map,
  SHA-256 binding to the original facts and all five source Markdown documents,
  exact source quotations, evidence type, anchor and semantic-support note.
- Check (a), existence in the cleaned source before chunking, is independent of
  check (b), occurrence in one actual chunk. Table evidence must occupy one row.
  Joining chunks is forbidden. Existing primary normalization/matching is retained.
- Missing/extra/duplicate pairs, unknown identities, stale hashes, unsupported
  versions, invalid evidence types, empty snippets or missing anchors fail map
  validation. An incomplete map may be audited for diagnostics but never passes.
- Candidate evidence is authored from source documents only, not chunk output.
  Exact-text tests do not prove semantic support; Claude must review the full map.
- Unsupported pairs remain explicit failures with source-search evidence.
  Only Claude may authorize original-fact/source corrections in a separate,
  explicitly tagged change. Do not regenerate sample PDFs during Phase 4.
- Once approved, the map is frozen. Any change after the first Antigravity run
  requires a new amendment. If approved evidence exists in cleaned source but
  fails one-chunk retention, fix the chunker, never the map to obtain a pass.
- Golden-set multi-document source coverage is informational only for Phase 7.
- The previous `118/170` result used a wrong contract and is retired. It is not
  the acceptance metric for A-6. Phase 4 still requires explicit user approval;
  Phase 5 and Phase 6 remain unauthorized.

## A-7 — Phase 5 embedding runtime (2026-11-07)

- The Phase 5 execution contract uses CPU-only, dense, normalized 1024-d
  `BAAI/bge-m3` embeddings with no query or document instruction prefix.
- The model is pinned to Hugging Face revision
  `5617a9f61b028005a4858fdac845db406aefb181`, loaded lazily once per process,
  and operates offline after an explicit preparation command.
- The default embedding batch size is 16, superseding the batch-of-32 example
  in SRD section 8.1 for the measured CPU environment.
- `EMBEDDING_MAX_LENGTH` defaults to 2048. Inputs above that token limit fail
  with a typed error instead of being silently truncated.
- Heavy ML dependencies live outside the core/CI installation. Model tests are
  local-only under the registered `model` marker; CI uses the deterministic
  fake embedder and must not install torch.
- The Phase 5 cosine smoke search is an informational in-memory measurement.
  It does not implement or tune the Phase 7 retrieval service.

## A-8 — Phase 5 CPU embedding batch default (2026-11-07)

- **Title:** Phase 5 CPU embedding batch default
- **Decision:** The Phase 5 BGE-M3 default embedding batch size is 16 rather than the original 32 specified in the initial implementation prompt.
- **Reason:** The value is based on the measured CPU memory/latency benchmark and keeps the runtime within the approved ten-minute limit.
- **Scope:** This changes only the embedding batch default. It does not alter chunking, embedding dimension, normalization, model revision, retrieval behavior or database schema.
- **Status:** Approved for Phase 5 review by Claude on 2026-11-07.

## A-9 — Phase 5 dependency alignment (2026-11-07)

- **Title:** Phase 5 dependency alignment
- **Decision:** `sentence-transformers==6.2.0` is retained. Core and ML dependencies are aligned to `transformers==5.22.2`, `tokenizers==0.23.3`, and `huggingface-hub==2.4.0` (with `torch==2.15.0+cpu` in ML extra and requirements).
- **Proof:**
  - Baseline chunk fingerprint (transformers 4.57.6): `9e3c4ee12f868ad3df7ea9f7fdb6d79f829beb2eb42756fbe607c88063a6b809`
  - Candidate chunk fingerprint (transformers 5.22.2): `9e3c4ee12f868ad3df7ea9f7fdb6d79f829beb2eb42756fbe607c88063a6b809`
  - Identical chunk counts across all 5 PDFs: Company_Overview.pdf (32), FAQs.pdf (16), Policies_and_Terms.pdf (24), Property_Listings.pdf (19), Services_and_Fees.pdf (35); Total = 126.
  - Both verification runs confirmed Primary: 170/170 and Related: 176/176 (`verify_chunks.py` stable diff empty, PASS).
- **Scope:** No chunk content, order, indices, pages, sections or counts changed. No schema migration was created. Database pgvector column remains `vector(1024)`.
