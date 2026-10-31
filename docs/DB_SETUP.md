# Phase 2 — PostgreSQL setup and migration guide

SRD §§7, 9, 13–15; DM-5, DM-7, MT-1, MT-3, MT-5.

## 1. Prepare two PostgreSQL databases

Create two **separate** PostgreSQL databases (for Supabase free tier, use
**two Supabase projects**). The operational database needs the pgvector
extension. The analytics database does not use operational foreign keys.

In each Supabase project, select **Connect → Session pooler**, and copy its
provided URL. The direct connection requires IPv6 unless the IPv4 add-on is
available; the session pooler supports IPv4. **Do not use the transaction
pooler**: prepared statements, `SET LOCAL` and database migrations need
session semantics. A Supabase session-pooler URL normally uses port 5432;
copy the exact hostname and username from Supabase rather than guessing.

In the repository-root `.env`:

```dotenv
DATABASE_URL=postgresql://OPERATIONAL_USER:PASSWORD@OPERATIONAL_HOST:5432/postgres
ANALYTICS_DATABASE_URL=postgresql://ANALYTICS_USER:PASSWORD@ANALYTICS_HOST:5432/postgres
```

Do not commit `.env`. Percent-encode URL-reserved characters in passwords.
The application automatically normalizes `postgres://` and `postgresql://`
to the SQLAlchemy `postgresql+psycopg://` driver.

The development/migration connections must permit table creation. If the
operational role is not permitted to create the `vector` extension, enable
it in Supabase's **Database → Extensions** first. `vector` may live in
`extensions`, so connections to Supabase must make both `public` and
`extensions` visible in `search_path`.

## 2. Check connectivity without printing credentials

From repository root with `.venv` activated:

```bash
python - <<'PY'
from sqlalchemy import create_engine, text
from app.core.config import get_settings

settings = get_settings()
for analytics in (False, True):
    label = 'analytics' if analytics else 'operational'
    url = settings.require_database_url(analytics=analytics)
    engine = create_engine(url, connect_args={'connect_timeout': 10})
    try:
        with engine.connect() as connection:
            print(f'{label}: PostgreSQL {connection.scalar(text("SHOW server_version"))}')
    finally:
        engine.dispose()
PY
```

## 3. Run independent Alembic migrations

```bash
alembic -c backend/alembic_ops.ini upgrade head
alembic -c backend/alembic_analytics.ini upgrade head
python scripts/db_check.py
```

The two databases use independent Alembic version tables:
`alembic_version_ops` and `alembic_version_analytics`.

For **development databases only**, verify reversibility:

```bash
alembic -c backend/alembic_ops.ini downgrade base
alembic -c backend/alembic_analytics.ini downgrade base
alembic -c backend/alembic_ops.ini upgrade head
alembic -c backend/alembic_analytics.ini upgrade head
python scripts/db_check.py
```

**Warning:** `downgrade base` destroys the tables and their data. Never run
it on a database containing important data.

## 4. Tenant isolation and the `rag_app` role

The operational migration enables and **forces RLS** on documents, chunks,
chat_sessions and messages. Policies compare company ownership with the
transaction-local `app.company_id` setting; messages inherit tenancy through
their chat session. Every repository requires an explicit company UUID.

The migration attempts to create a `NOLOGIN NOBYPASSRLS` group role called
`rag_app`, grant it minimal data-table privileges, and let the migrating
role assume it for tests. Some managed hosts restrict `CREATE ROLE` or
membership grants; if that occurs, the RLS impersonation test **skips with
a reason** and the report must flag it. Do not call the database isolation
requirement verified if that test was skipped.

For the eventual running app, create/use a dedicated **non-table-owner,
non-BYPASSRLS login role** with necessary grants. A privileged migration
connection must not be reused as the application connection. Never ship
migration credentials or a Supabase service-role key in the frontend.

The tenant session helper sets `app.company_id` within a transaction using
`set_config(..., true)` (equivalent to `SET LOCAL`). That setting does not
leak into later transactions.

## 5. Run rollback-safe integration tests

```bash
cd backend
pytest -m db -v
pytest
ruff check .
ruff format --check .
mypy app
cd ..
ruff check scripts/
ruff format --check scripts/
pre-commit run --all-files
```

Integration tests use an outer transaction that always rolls back. Their
Session objects use nested savepoints, so they do not leave test data in the
shared development database. The separate database connections still require
valid credentials and successfully applied migrations.

## 6. Diagnostics

```bash
python scripts/db_check.py
```

The checker prints server version, operational pgvector version, available
tables, forced RLS status, tenant policy status, revision heads and whether
`rag_app` exists. It does **not** display passwords or connection strings.

If a database is reachable but migration fails, inspect the error locally;
share only redacted logs. Phase 2 does not modify `.env.example`,
`README.md`, anything in `context/`, or canonical sample data.
