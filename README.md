## FastAPI OCR Service

A production-ready service that ingests PDF files, stores them in Azure Blob Storage, extracts content (digital and scanned/handwritten), summarizes it into a strict JSON using an LLM (Groq), and persists the result in a relational database (MySQL). It exposes a clean REST API for uploading and retrieving processed documents.

### Key Capabilities
- Upload PDFs via REST; files stored in Azure Blob.
- Detects digital vs scanned PDFs; uses multi-method extraction.
- OCR for scanned/handwritten PDFs (PaddleOCR preferred with robust fallbacks).
- LLM post-processing with strict JSON output enforcement and automatic repair.
- MySQL storage of extracted JSON + metadata with processing status tracking.
- Background processing support (return immediately while the server processes).
- Detailed logging for observability and troubleshooting.

---

## Architecture Overview

```
Client → POST /api/upload → Azure Blob (file) → PDF Processor
      → Detect PDF type → (Digital: pdfplumber/PyMuPDF) | (Scanned: PaddleOCR → fallbacks)
      → Extracted text → LLM (Groq) → Strict JSON (validated/repaired)
      → MySQL (JSON, metadata, status) → GET /api/documents/{id}
```

- Azure Blob: canonical storage for uploaded files
- PDF Processor: robust text extraction (digital + OCR)
- LLM (Groq): converts free text into structured JSON with business rules
- Mysql Database: stores `json_data` and processing `status`

---

## Project Structure & File Responsibilities

```
app/
├─ __init__.py                 # Marks package
├─ main.py                     # FastAPI app entrypoint & routers
├─ config.py                   # Environment configuration via pydantic-settings
├─ db/
│  ├─ session.py               # SQLAlchemy engine/session; SQLite fallback if MySQL absent
│  └─ models.py                # SQLAlchemy models (Document)
├─ routers/
│  └─ upload.py                # REST endpoints: upload, test-ocr, get document
├─ services/
│  ├─ azure_blob.py            # Azure Blob upload with local fallback & logging
│  └─ pdf_processor.py         # PDF type detection, extraction, OCR, LLM JSON, repair
├─ schemas.py                  # Pydantic response schemas
README.md                      # This guide
requirements.txt               # Python dependencies
.env                           # Environment configuration
```

- `app/main.py`: Creates the FastAPI app, enables CORS, registers routers, and ensures DB tables exist.
- `app/config.py`: Loads env vars (Azure, DB, Groq, etc.). Provides `effective_database_url` and `has_azure_storage` for safe defaults and fallbacks.
- `app/db/session.py`: Builds SQLAlchemy engine/session. If MySQL isn’t configured, uses `sqlite:///./app_local.db` so the app runs without credentials.
- `app/db/models.py`: Defines `Document` model: `id, filename, blob_url, json_data (JSON), status, error_message, created_at`.
- `app/services/azure_blob.py`: Uploads to Azure using connection string + container; falls back to writing under `./local_uploads/`. Adds detailed logging and ensures containers exist.
- `app/services/pdf_processor.py`:
  - Detects digital vs scanned.
  - Digital extraction: pdfplumber + PyMuPDF.
  - Scanned extraction: PaddleOCR structure (PPStructureV3/PPStructure) → if insufficient, rasterizes pages and uses `PaddleOCR` text engine; then fallbacks (PyMuPDF, pdfplumber).
  - LLM post-processing with strict JSON validation, lightweight fixes, and LLM-powered JSON repair when needed.
- `app/routers/upload.py`:
  - `POST /api/upload`: accepts multipart `file`, uploads to Blob, stores DB row with `status=processing`, processes PDF either in background or synchronously, and updates status.
  - `GET /api/documents/{id}`: returns stored JSON, status, and metadata.
  - `POST /api/test-ocr`: diagnostic endpoint to test OCR path with extracted text preview.
- `app/schemas.py`: Response models for `DocumentCreateResponse` and `DocumentDetailResponse`.

---

## Requirements

- Python 3.10+
- MySQL database (optional; SQLite fallback works without setup)
- Azure Storage account (optional; local fallback works without setup)
- Groq API key (required for LLM processing)

---

## Environment Variables (.env)

Environment:  `.env` and fill values. Minimum for full cloud flow:

```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=stgaiprocurex;AccountKey=YOUR_KEY;EndpointSuffix=core.windows.net
AZURE_STORAGE_CONTAINER=ocr-procurex-blob

DATABASE_URL=mysql+pymysql://username:password@localhost:3306/your_database

GROQ_API_KEY=your_groq_api_key
APP_ENV=prod
```

Optional:
- `OPENAI_API_KEY`, `HF_TOKEN` if you plan to add those backends.
- If `DATABASE_URL` is omitted, SQLite `app_local.db` will be used.
- If Azure vars are omitted, files are saved under `./local_uploads/`.

---

## Installation

```bash
# In project root
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env with your values
```

For PaddleOCR on macOS/CPU (already pinned in requirements):
- We use `paddlepaddle==2.6.1` and `paddleocr==2.8.1`.
- Avoid `--reload` during OCR testing to prevent multi-process import issues.

---

## Running the Service

Development (without reload for OCR stability):
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Swagger UI: `http://localhost:8000/docs`

---

## API Usage

- Upload a PDF (background processing by default):
```bash
curl -F "file=@/path/to/file.pdf" "http://localhost:8000/api/upload?run_in_background=true"
```
- Upload and wait for processing (synchronous):
```bash
curl -F "file=@/path/to/file.pdf" "http://localhost:8000/api/upload?run_in_background=false"
```
- Get a document (replace ID):
```bash
curl "http://localhost:8000/api/documents/ID"
```
- OCR diagnostic (returns preview & method used):
```bash
curl -F "file=@/path/to/file.pdf" "http://localhost:8000/api/test-ocr"
```

Responses
- `POST /api/upload` returns `{ id, filename, blob_url, status }`.
- `GET /api/documents/{id}` returns `{ id, filename, blob_url, status, json_data, error_message }`.

---

## Data Flow Details

1. Client uploads PDF → file saved to Azure Blob (or local fallback).
2. DB record created with `status=processing`.
3. PDF processing pipeline:
   - Detect digital vs scanned.
   - Extract text via digital methods or OCR (PaddleOCR with rasterized fallback).
   - Summarize to JSON via Groq LLM.
   - Validate/repair JSON to guarantee strict, parseable output.
4. DB record updated with `{ json_data, status=completed }` or `{ status=failed, error_message }`.
5. Client fetches processed JSON via `GET /api/documents/{id}`.

---

## Troubleshooting

- Azure upload not working → verify `AZURE_STORAGE_CONNECTION_STRING` and `AZURE_STORAGE_CONTAINER`; logs will show the fallback usage.
- MySQL connection errors → verify `DATABASE_URL` and DB user privileges. SQLite is automatic if MySQL is absent.
- PaddleOCR errors (e.g., PDX initialized) →
  - Run server without `--reload`.
  - Ensure you’re using the venv’s Python and Uvicorn.
  - If needed: `pip uninstall -y paddleocr paddlepaddle && pip install paddlepaddle==2.6.1 paddleocr==2.8.1`.
- LLM invalid JSON → handled by the JSON repair layer. If error persists, check `error_message` in `GET /api/documents/{id}`.
