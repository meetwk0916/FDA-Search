from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .database import connect

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(query: str) -> list[str]:
    return [token.casefold() for token in TOKEN_RE.findall(query)][:8]


def build_fts_query(query: str) -> str:
    return " AND ".join(f'"{token}"*' for token in tokenize(query))


def search_documents(
    database: str | Path,
    query: str,
    state: str = "",
    year: str = "",
    record_type: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, object]:
    connection = connect(database)
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    tokens = tokenize(query)
    filters = []
    parameters: list[object] = []
    if state:
        filters.append("d.state = ?")
        parameters.append(state)
    if year and re.fullmatch(r"\d{4}", year):
        filters.append("substr(d.record_date, 7, 4) = ?")
        parameters.append(year)
    if record_type:
        filters.append("d.record_type = ?")
        parameters.append(record_type)
    where_filters = (" AND " + " AND ".join(filters)) if filters else ""

    if tokens:
        fts_query = build_fts_query(query)
        count = connection.execute(
            f"""
            SELECT count(*) FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ? {where_filters}
            """,
            [fts_query, *parameters],
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT d.id, d.media_id, d.record_type, d.record_date, d.company,
                d.fei, d.state, d.country, d.establishment_type, d.publish_date,
                d.download_url, d.filename, d.page_count,
                snippet(documents_fts, 5, '<mark>', '</mark>', ' … ', 42)
                    AS snippet,
                bm25(documents_fts, 3.0, 4.0, 2.0, 1.0, 2.0, 1.0) AS rank
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ? {where_filters}
            ORDER BY rank, d.record_date DESC
            LIMIT ? OFFSET ?
            """,
            [fts_query, *parameters, limit, offset],
        ).fetchall()
    else:
        base_where = " WHERE " + " AND ".join(filters) if filters else ""
        count = connection.execute(
            f"SELECT count(*) FROM documents d{base_where}", parameters
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT d.id, d.media_id, d.record_type, d.record_date, d.company,
                d.fei, d.state, d.country, d.establishment_type, d.publish_date,
                d.download_url, d.filename, d.page_count, '' AS snippet,
                0 AS rank
            FROM documents d {base_where}
            ORDER BY substr(d.record_date, 7, 4) DESC,
                substr(d.record_date, 1, 2) DESC,
                substr(d.record_date, 4, 2) DESC
            LIMIT ? OFFSET ?
            """,
            [*parameters, limit, offset],
        ).fetchall()

    states = [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT state FROM documents WHERE state <> '' ORDER BY state"
        )
    ]
    years = [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT substr(record_date, 7, 4) AS year "
            "FROM documents WHERE record_date GLOB '??/??/????' "
            "ORDER BY year DESC"
        )
    ]
    record_types = [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT record_type FROM documents "
            "WHERE record_type <> '' ORDER BY record_type"
        )
    ]
    connection.close()
    return {
        "query": query,
        "total": count,
        "limit": limit,
        "offset": offset,
        "states": states,
        "years": years,
        "record_types": record_types,
        "results": [dict(row) for row in rows],
    }


def index_status(database: str | Path) -> dict[str, object]:
    connection = connect(database)
    row = connection.execute(
        """
        SELECT count(*) AS total,
            sum(extraction_status = 'indexed' AND extraction_version >= 2)
                AS indexed,
            sum(extraction_status = 'ocr_required') AS ocr_required,
            sum(extraction_status = 'error') AS errors,
            max(indexed_at) AS updated_at
        FROM documents
        """
    ).fetchone()
    sync_values = {
        state_row["key"]: state_row["value"]
        for state_row in connection.execute("SELECT key, value FROM sync_state")
    }
    connection.close()

    def integer(key: str, default: int = 0) -> int:
        return int(sync_values.get(key, default))

    status = {key: row[key] or 0 for key in row.keys()}
    status["source_total"] = integer("source_total", status["total"])
    status["source_unavailable"] = integer("source_unavailable")
    status["source_downloadable"] = integer(
        "source_downloadable", status["source_total"] - status["source_unavailable"]
    )
    status["source"] = {
        "reported_rows": status["source_total"],
        "enumerated_rows": integer(
            "source_enumerated",
            status["source_downloadable"] + status["source_unavailable"],
        ),
        "downloadable_documents": status["source_downloadable"],
        "unavailable_rows": status["source_unavailable"],
        "duplicate_references": integer("source_duplicates"),
        "pagination_gap": integer("source_pagination_gap"),
    }
    status["sync"] = {
        "state": sync_values.get("sync_status", "idle"),
        "phase": sync_values.get("sync_phase", "idle"),
        "started_at": sync_values.get("sync_started_at") or None,
        "completed_at": sync_values.get("sync_completed_at") or None,
        "last_success_at": sync_values.get("sync_last_success_at") or None,
        "last_error": sync_values.get("sync_last_error") or None,
        "pending_documents": integer("sync_pending"),
        "processed_documents": integer("sync_processed"),
    }
    status["documents"] = {
        "stored": status["total"],
        "indexed": status["indexed"],
        "ocr_required": status["ocr_required"],
        "errors": status["errors"],
        "updated_at": status["updated_at"],
    }
    return status
