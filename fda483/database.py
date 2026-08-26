from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    media_id TEXT NOT NULL UNIQUE,
    record_type TEXT NOT NULL,
    record_date TEXT NOT NULL,
    company TEXT NOT NULL,
    fei TEXT NOT NULL,
    state TEXT NOT NULL,
    country TEXT NOT NULL,
    establishment_type TEXT NOT NULL,
    publish_date TEXT NOT NULL,
    download_url TEXT NOT NULL,
    filename TEXT NOT NULL DEFAULT '',
    page_count INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL DEFAULT '',
    extraction_status TEXT NOT NULL,
    extraction_version INTEGER NOT NULL DEFAULT 1,
    error TEXT NOT NULL DEFAULT '',
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_record_date ON documents(record_date DESC);
CREATE INDEX IF NOT EXISTS documents_fei ON documents(fei);
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    company, fei, state, country, establishment_type, content,
    content='documents', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, company, fei, state, country,
        establishment_type, content)
    VALUES (new.id, new.company, new.fei, new.state, new.country,
        new.establishment_type, new.content);
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, company, fei, state,
        country, establishment_type, content)
    VALUES ('delete', old.id, old.company, old.fei, old.state, old.country,
        old.establishment_type, old.content);
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, company, fei, state,
        country, establishment_type, content)
    VALUES ('delete', old.id, old.company, old.fei, old.state, old.country,
        old.establishment_type, old.content);
    INSERT INTO documents_fts(rowid, company, fei, state, country,
        establishment_type, content)
    VALUES (new.id, new.company, new.fei, new.state, new.country,
        new.establishment_type, new.content);
END;
"""


def connect(path: str | Path) -> sqlite3.Connection:
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(documents)")
    }
    if "extraction_version" not in columns:
        connection.execute(
            "ALTER TABLE documents ADD COLUMN extraction_version "
            "INTEGER NOT NULL DEFAULT 1"
        )
    if "record_type" not in columns:
        connection.execute(
            "ALTER TABLE documents ADD COLUMN record_type "
            "TEXT NOT NULL DEFAULT '483'"
        )
    return connection
