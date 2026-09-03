# FDA Global Search Project Guide

## Purpose

Full-text search for downloadable FDA OII public-record PDFs across record
types, with official source links and OCR fallback for scanned pages. This
repository is the core application and local verification source; BI EDP is
the active delivery target.

## Run and verify

Install dependencies with `python3 -m pip install --target .deps -r requirements.txt`.
Set `PYTHONPATH=.deps:src` for all commands.

- Index or resume: `python3 -m fda483.indexer --workers 2`
- Roll continuously: `python3 -m fda483.indexer --workers 2 --interval 43200`
- Serve locally: `python3 -m fda483.server`
- Test: `python3 -m unittest`
- Runtime status: `curl http://127.0.0.1:8080/api/status`

## Stack and layout

- Python standard-library HTTP server and crawler orchestration
- Application package in `src/fda483/`
- SQLite FTS5 in `src/fda483/database.py`
- PDF extraction/OCR in `src/fda483/indexer.py`
- Search contract in `src/fda483/search.py`
- Browser UI in `src/fda483/static/`
- Generated dependencies and index data in ignored `.deps/` and `data/`

## Invariants

- Fetch only FDA URLs and retain official download links; do not persist PDFs.
- Search uses case-insensitive token-prefix matching with `AND` between all
  query terms. Do not add approximate spelling unless explicitly requested.
- Preserve redaction markers and never infer redacted content.
- Increment `EXTRACTION_VERSION` when extraction completeness rules change.
- Keep malformed PDF and OCR failures isolated to one document.

## Repository and deployment routing

- GitHub `origin/main` contains the core application and local workflow.
- Bitbucket `genfox-fbi/dev` has unrelated OpenDevStack history and is the
  authoritative BI EDP integration branch. Preserve its Jenkins, Helm, and
  Docker scaffold; never force-push GitHub history over it.
- Port application changes to Bitbucket as normal commits based on its `dev`
  head, then verify the remote SHA.
- Keep EDP deployment configuration and operational guidance in Bitbucket.
  This repository should only point to that authority instead of duplicating
  a second deployment specification.
- The former Tencent Cloud Lighthouse plan is retired.

## Current state

- The local historical backfill completed on 2026-08-28 with all 2,956 unique
  downloadable FDA PDFs stored. The ignored SQLite database is not a Git
  artifact.
- Do not infer runtime availability from persisted sync state. Verify the
  process list and `/api/status`; the local Web and indexer processes may be
  stopped.
- BI EDP deployment is not yet live-verified. The current Bitbucket scaffold
  still needs the Python/OCR image, persistent volume, Web command, probes, and
  scheduled incremental index job.
