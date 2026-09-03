# FDA OpenRecords Search

**English** | [简体中文](README.zh-CN.md)

**Make FDA public records buried in PDFs and scans actually searchable.**

FDA OpenRecords Search is a local full-text search engine for public records in
the [FDA Office of Inspections and Investigations (OII) Electronic Reading
Room](https://www.fda.gov/about-fda/office-inspections-and-investigations/oii-foia-electronic-reading-room).

It discovers downloadable PDFs from official FDA pages, stores their metadata
and extracted text in SQLite FTS5, and provides a lightweight browser interface.
When a page does not contain enough native text, the indexer automatically
falls back to local ONNX OCR. Every search result retains a link to the original
FDA document.

> [!IMPORTANT]
> This is an independent open-source project. It is not an official FDA product
> and is not affiliated with or endorsed by the FDA. Website changes, PDF
> extraction, and OCR may produce incomplete results. Always verify important
> information against the original FDA document linked from each result.

## The problem this project solves

The FDA OII Electronic Reading Room publishes inspection, compliance, and
public-disclosure records primarily as individual PDF files. Its official
listing is useful when you already know the relevant metadata, but it is not
designed to answer cross-document questions such as:

- Which records mention a specific manufacturing, quality, or sterility issue?
- Where has a company, FEI, or establishment type appeared over time?
- Which record types, states, or countries contain the same term?
- Does the body of a scanned PDF contain a term of interest?

File names and list filters cannot search inside PDFs. Opening documents one by
one is slow, and scanned files often have no searchable text layer at all.
Browsing record types separately also makes research and cross-checking harder.

FDA OpenRecords Search turns those public files into a reproducible local
full-text index:

1. Discover records on official FDA pages and retain their source links.
2. Extract text from every PDF and run OCR on pages with insufficient text.
3. Store normalized metadata and document text in SQLite FTS5.
4. Search across record types with filters, contextual snippets, and sync
   status.

The project reduces the time needed to **find relevant source records**. It
does not replace regulatory or compliance judgment, characterize violations,
infer redacted content, or present OCR output as an authoritative conclusion.
Every result links back to the original FDA PDF for verification.

## Features

- Index downloadable public PDFs across record types in the FDA OII Electronic
  Reading Room
- Search PDF text, company names, FEIs, states, countries, and establishment
  types
- Filter by record type, location, and record year
- Run fast local full-text searches with SQLite FTS5
- Apply local ONNX OCR when native page text is insufficient
- Resume interrupted indexing and run concurrent or scheduled refreshes
- Report discovery, extraction, and OCR status
- Isolate malformed PDF and OCR failures to a single document
- Preserve official FDA source links without retaining downloaded PDFs

## How it works

```text
FDA Electronic Reading Room
          |
          v
 Discover records and official PDF links
          |
          v
 PDF text extraction -> per-page OCR when needed
          |
          v
      SQLite + FTS5
          |
          v
 Local HTTP API + browser interface
```

All network requests are restricted to `fda.gov`. The default database is
`data/fda_search.sqlite3`, and the `data/` directory is ignored by Git.

## Requirements

- Python 3.11 or later
- Linux, WSL, or another Unix-like environment that provides `fcntl`
- A SQLite build with FTS5 support, included in most Python distributions
- Sufficient disk space and network time to process the full public collection

## Quick start

### 1. Clone the repository

```bash
git clone https://github.com/meetwk0916/FDA.git
cd FDA
```

### 2. Install dependencies

Using a virtual environment is recommended:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
export PYTHONPATH=src
```

If your environment cannot create a virtual environment, install dependencies
into the ignored `.deps/` directory:

```bash
python3 -m pip install --target .deps -r requirements.txt
export PYTHONPATH=.deps:src
```

Keep the corresponding virtual environment or `PYTHONPATH` active for the
commands below.

### 3. Build a small test index

```bash
python3 -m fda_search.indexer --limit 10
```

### 4. Start the search service

```bash
python3 -m fda_search.server
```

Open <http://127.0.0.1:8080>.

## Building and maintaining the index

Index all downloadable records:

```bash
python3 -m fda_search.indexer --workers 2
```

Run an incremental refresh every 12 hours:

```bash
python3 -m fda_search.indexer --workers 2 --interval 43200
```

Force re-extraction of existing records:

```bash
python3 -m fda_search.indexer --refresh --workers 2
```

Use a different database file:

```bash
python3 -m fda_search.indexer --database /path/to/index.sqlite3
python3 -m fda_search.server --database /path/to/index.sqlite3
```

Indexer behavior:

- Successfully processed documents at the current extraction version are
  skipped automatically.
- Records at an older extraction version are reprocessed after extraction
  rules change.
- A database-level lock permits only one indexer per database.
- On `SIGINT` or `SIGTERM`, the indexer stops submitting new documents and
  waits for active writes to finish.
- Metadata rows without a usable download URL are counted and skipped.
- Because FDA server-side pagination can drift, discovery uses stable sorting,
  repeated passes, and media ID deduplication.

### Indexer options

| Option | Description |
| --- | --- |
| `--database PATH` | SQLite database path; defaults to `data/fda_search.sqlite3` |
| `--limit N` | Process only the newest N records for quick validation |
| `--workers N` | Number of parallel PDF workers; defaults to `2` |
| `--refresh` | Reprocess records regardless of existing extraction results |
| `--interval SECONDS` | Repeat incremental indexing at the given interval |

## Search semantics

- Queries are split into case-insensitive Unicode word tokens, limited to the
  first eight tokens.
- Each token uses literal prefix matching, and all tokens are joined with
  `AND`.
- Query terms do not need to appear next to each other.
- There is no edit-distance matching, spell correction, synonym expansion, or
  approximate matching.
- Results use BM25 ranking, with higher weights for FEI and company name.

For example, `quality control` returns only records containing both `quality*`
and `control*`.

## HTTP API

The service listens on `127.0.0.1:8080` by default. Change the interface or port
from the command line:

```bash
python3 -m fda_search.server --host 0.0.0.0 --port 8080
```

### `GET /api/search`

| Parameter | Description |
| --- | --- |
| `q` | Full-text query |
| `state` | Exact state or location filter |
| `year` | Four-digit record year |
| `record_type` | Exact record type filter |
| `limit` | Number of results, from 1 to 100; defaults to 20 |
| `offset` | Pagination offset; defaults to 0 |

Example:

```bash
curl "http://127.0.0.1:8080/api/search?q=quality%20control&limit=10"
```

### `GET /api/status`

Returns source discovery counts, document extraction status, and current sync
progress:

```bash
curl http://127.0.0.1:8080/api/status
```

## Project structure

```text
src/fda_search/
├── database.py       # SQLite schema and FTS5 index
├── indexer.py        # FDA discovery, PDF extraction, OCR, and sync
├── search.py         # Query construction, filtering, ranking, and status
├── server.py         # Standard-library HTTP server and JSON API
└── static/           # Browser interface
tests/                # unittest test suite
```

The server side is intentionally lightweight. Crawling orchestration and HTTP
serving use the Python standard library, while SQLite handles both persistence
and full-text search without a separate search service.

## Testing

```bash
python3 -m unittest
```

Tests use temporary databases and mocked network or PDF inputs. They do not
perform a full FDA crawl.

## Data, privacy, and accuracy

- Metadata and PDFs come from official public FDA pages.
- Source PDFs are used as temporary processing inputs and are not retained.
- The local database contains text extracted from public files and must not be
  committed to Git.
- Pages with insufficient native text are rendered and passed through OCR.
- Documents that still lack sufficient text after OCR are marked
  `ocr_required` rather than reported as fully indexed.
- A malformed, encrypted, or unreadable PDF is recorded as an isolated error.
- Legally redacted content cannot be recovered. This project never infers or
  fills in redacted text.
- Users are responsible for following applicable FDA website terms and using a
  considerate crawl frequency.

## Contributing

Issues and pull requests are welcome. Before submitting a change:

1. Keep network access restricted to official FDA URLs and retain official
   source links.
2. Do not commit PDFs, SQLite indexes, OCR model caches, or other generated
   files.
3. Preserve the case-insensitive, token-prefix, all-term `AND` search semantics
   unless the change explicitly intends to alter that contract.
4. Increment `EXTRACTION_VERSION` when extraction completeness rules change.
5. Run `python3 -m unittest` and describe how new behavior was verified.

When changing documentation, keep [README.md](README.md) and
[README.zh-CN.md](README.zh-CN.md) aligned.

## License

This project is licensed under the [MIT License](LICENSE).
