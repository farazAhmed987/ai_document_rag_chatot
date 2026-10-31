# Historical Preflight Context Archive (8735072)

These files are historical snapshots extracted byte-for-byte from parent commit `8735072a4c48c0c0ae5ad2f2659cc1f05b8addd7`:
- `PROJECT_STATUS.md`
- `CLAUDE_CONTEXT.md`

### Note on Superseded Claims
The stale Git alignment statements (claiming alignment with the release tag) and the references to `app.current_company_id` and `NOINHERIT` in these historical snapshots are superseded by live read-only database queries and Git verification performed during the Phase 4 preflight:
- Live RLS policies on all five tenant tables strictly enforce `app.company_id` (matching SRD §9).
- Role `rag_app` has `rolbypassrls=False` and `rolinherit=True` (role-membership inheritance audit limitation noted).
- Current `main` is ahead of `origin/main` by documentation checkpoints while the release tag `v0.3.1-extraction-hardening` sits at `92f4ad56f9fa1068396c5cdfe6b6387396c519fa`.
