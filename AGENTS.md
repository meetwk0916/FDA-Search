# FDA OpenRecords Search Project Guide

## Purpose

Full-text search for downloadable FDA OII public-record PDFs across record
types, with official source links and OCR fallback for scanned pages.

## Run and verify

Install dependencies with `python3 -m pip install --target .deps -r requirements.txt`.
Set `PYTHONPATH=.deps:src` for all commands.

- Index or resume: `python3 -m fda_search.indexer --workers 2`
- Roll continuously: `python3 -m fda_search.indexer --workers 2 --interval 43200`
- Serve locally: `python3 -m fda_search.server`
- Test: `python3 -m unittest`
- Runtime status: `curl http://127.0.0.1:8080/api/status`

## Stack and layout

- Python standard-library HTTP server and crawler orchestration
- Application package in `src/fda_search/`
- SQLite FTS5 in `src/fda_search/database.py`
- PDF extraction/OCR in `src/fda_search/indexer.py`
- Search contract in `src/fda_search/search.py`
- Browser UI in `src/fda_search/static/`
- Generated dependencies and index data in ignored `.deps/` and `data/`

## Invariants

- Fetch only FDA URLs and retain official download links; do not persist PDFs.
- Search uses case-insensitive token-prefix matching with `AND` between all
  query terms. Do not add approximate spelling unless explicitly requested.
- Preserve redaction markers and never infer redacted content.
- Increment `EXTRACTION_VERSION` when extraction completeness rules change.
- Keep malformed PDF and OCR failures isolated to one document.

## Current state

- The local historical backfill completed on 2026-08-28 with all 2,956 unique
  downloadable FDA PDFs stored. The ignored SQLite database is not a Git
  artifact.
- Do not infer runtime availability from persisted sync state. Verify the
  process list and `/api/status`; the local Web and indexer processes may be
  stopped.
