# FDA Global Search Project Guide

## Purpose

Local full-text search for downloadable FDA OII public-record PDFs across
record types, with official source links and OCR fallback for scanned pages.

## Run and verify

Install dependencies with `python3 -m pip install --target .deps -r requirements.txt`.
Set `PYTHONPATH=.deps:.` for all commands.

- Index or resume: `python3 -m fda483.indexer --workers 2`
- Roll continuously: `python3 -m fda483.indexer --workers 2 --interval 43200`
- Serve locally: `python3 -m fda483.server`
- Test: `python3 -m unittest`
- Runtime status: `curl http://127.0.0.1:8080/api/status`

## Stack and layout

- Python standard-library HTTP server and crawler orchestration
- SQLite FTS5 in `fda483/database.py`
- PDF extraction/OCR in `fda483/indexer.py`
- Search contract in `fda483/search.py`
- Browser UI in `fda483/static/`
- Generated dependencies and index data in ignored `.deps/` and `data/`

## Invariants

- Fetch only FDA URLs and retain official download links; do not persist PDFs.
- Search uses case-insensitive token-prefix matching with `AND` between all
  query terms. Do not add approximate spelling unless explicitly requested.
- Preserve redaction markers and never infer redacted content.
- Increment `EXTRACTION_VERSION` when extraction completeness rules change.
- Keep malformed PDF and OCR failures isolated to one document.

## Current state

The local resumable global backfill is running with one locked indexer and the
local search service. Use `/api/status` as the live authority. There is no
hosted production deployment yet; GitHub Issue #2 is the approved Tencent Cloud
Hong Kong Lighthouse deployment specification.
