# RAG Chatbot Dashboard
A multi-company document question-answering platform: each company uploads PDF, DOCX, TXT or Markdown files, which are extracted, cleaned, chunked, embedded and stored in Postgres with pgvector, and users then chat with their own documents to get grounded answers with source references. The backend is FastAPI and the frontend is React; the project is built for the DevSynt AI Internship (Task 5) and is in active development, with progress tracked in context/PROJECT_STATUS.md.

## CLI Usage (Phase 6 Ingestion & Administration)

The CLI entrypoint is `app.cli` (`python -m app.cli` from `backend` or with pythonpath configured).

### 1. Create a Company (Tenant)
```bash
python -m app.cli create-company --name "Acme Corp"
```

### 2. Ingest Documents
```bash
python -m app.cli ingest --company "Acme Corp" path/to/doc1.pdf path/to/doc2.docx
```

### 3. List Documents
```bash
python -m app.cli list --company "Acme Corp"
```

### 4. Inspect Document Details
```bash
python -m app.cli info --company "Acme Corp" --document "<DOCUMENT_UUID>"
```

### 5. Reprocess a Document
```bash
python -m app.cli reprocess --company "Acme Corp" --document "<DOCUMENT_UUID>"
```

### 6. Delete a Document
```bash
python -m app.cli delete --company "Acme Corp" --document "<DOCUMENT_UUID>" --yes
```
