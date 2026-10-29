# Local Development Setup

Document RAG Chatbot Dashboard

SRD Sections 3, 7, 9, 12, 13, 14 and 15.

This guide applies to Fedora Linux and Python 3.12.
All project commands assume the repository root unless stated otherwise.

## 1. Requirements

Install or verify:

- Python 3.12
- Python 3.12 venv support
- Git
- Go (required by the gitleaks pre-commit hook)
- Internet access for the initial package installation

No Docker, local PostgreSQL server, CUDA, or ML models are required
during Phase 0.

For Fedora, install the required tools using the appropriate
packages for your release. For example:

```bash
sudo dnf install git golang python3.12 python3.12-devel
```

Verify the tools:

```bash
python3.12 --version
git --version
go version
```

## 2. Clone and enter the repository

Clone the repository using its actual GitHub URL.

```bash
git clone YOUR_REPOSITORY_URL rag-chatbot
cd rag-chatbot
```

If the repository is already cloned, open its root instead.

## 3. Create and activate the virtual environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

The existing .venv can be reused.

Confirm the Python interpreter:

```bash
python --version
python -m pip --version
```

## 4. Install dependencies

Run from the repository root:

```bash
python -m pip install --upgrade pip
python -m pip install -r backend/requirements-dev.txt
python -m pip install -e backend
```

The editable installation makes the backend package importable
as `app`, without changing PYTHONPATH or sys.path.

Do not install backend/requirements-ml.txt in Phases 0 to 3.

## 5. Configure the environment

Copy the configuration template:

```bash
cp .env.example .env
```

Leave unused credentials blank during Phase 0.

The application always reads the repository-root .env file.

Environment variables override values in that file.

Never commit .env or paste database passwords and API keys
into logs, messages, or screenshots.

Verify that .env is ignored:

```bash
git check-ignore .env
```

Expected output:

```text
.env
```

## 6. Run the Phase 0 tests

From the repository root:

```bash
cd backend
pytest
ruff check .
ruff format --check .
mypy app
cd ..
```

Expected behavior:

- All available Phase 0 unit tests pass.
- Database tests skip if DATABASE_URL is unset.
- Sample-data tests skip if generated sample PDFs are absent.
- Ruff reports no lint or formatting errors.
- Mypy reports no type errors.

## 7. Install and run pre-commit

From the repository root:

```bash
pre-commit install
pre-commit run --all-files
```

The hooks include:

- Ruff lint checks
- Ruff formatting checks
- Gitleaks secret scanning
- End-of-file normalization
- Trailing whitespace cleanup

Protected paths are excluded from text-changing hooks:

- data/
- eval/
- context/
- docs/SRD.md
- README.md

The Ruff hooks operate on Python files only.

Verify protected paths remain unchanged:

```bash
git status --short -- data eval docs/SRD.md context README.md
```

No output means these paths have not been modified.

## 8. Supabase PostgreSQL setup (Phase 2)

The application uses two independent PostgreSQL databases:

1. Operational: DATABASE_URL
2. Analytics: ANALYTICS_DATABASE_URL

Use two Supabase projects or two independent PostgreSQL databases.

Enable the pgvector extension on the operational database.

For Supabase:

1. Open the relevant project.
2. Find the database connection settings.
3. Select a session-pooler connection string.
4. Copy the connection string into the correct .env variable.
5. Repeat for the second database.

A direct database connection may require IPv6 connectivity.

If the direct connection fails on an IPv4-only network,
use Supabase's session pooler connection string.

Do not substitute the transaction pooler. Transaction pooling
can interfere with prepared statements, session-local tenant
settings, and schema migrations.

The application normalizes these PostgreSQL URL prefixes:

- postgres://
- postgresql://

Both become:

- postgresql+psycopg://

Use only SQLAlchemy, psycopg 3 and standard PostgreSQL or pgvector
features. The Supabase SDK is not required.

## 9. Alembic migrations (Phase 2)

Two independent migration environments will be provided:

- backend/migrations/ops
- backend/migrations/analytics

Do not attempt migrations until Phase 2 supplies the
Alembic environment files and migration commands.

The operational migration uses DATABASE_URL.
The analytics migration uses ANALYTICS_DATABASE_URL.

The databases must never share migration state.

## 10. Troubleshooting

### Python cannot import app

Verify that the virtual environment is active and run:

```bash
python -m pip install -e backend
```

### Package installation conflict

Do not change dependency versions silently.

Capture:

```bash
python --version
python -m pip list
python -m pip check
```

Report the conflict before making a dependency change.

### Pre-commit cannot initialize gitleaks

Verify the Go installation:

```bash
go version
```

Then retry:

```bash
pre-commit run --all-files
```

### Database URL is missing

This is expected in Phase 0.

Configure the appropriate URL when Phase 2 begins.

### Database connection fails

Check the hostname, port, credentials, SSL settings,
network connectivity, and whether the Supabase session pooler
is being used.

Never print a complete connection URL containing a password.

## 11. Phase acceptance

Phase 0 is complete only when:

- Dependencies install successfully.
- Editable package installation succeeds.
- Pytest passes.
- Ruff checks pass.
- Mypy passes in strict mode.
- Pre-commit passes.
- Protected files remain unchanged.
- .env is not tracked.
- A fresh checkout can complete the setup and test procedure.

The next phase generates sample PDFs and document variants.
