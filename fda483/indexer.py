from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import cv2
import numpy as np
import pymupdf
import onnxruntime.capi.onnxruntime_pybind11_state as onnx_errors
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from rapidocr_onnxruntime import RapidOCR

from .database import connect

FDA_ORIGIN = "https://www.fda.gov"
READING_ROOM_URL = (
    f"{FDA_ORIGIN}/about-fda/office-inspections-and-investigations/"
    "oii-foia-electronic-reading-room"
)
MEDIA_RE = re.compile(r'href="/media/(\d+)/download"')
TAG_RE = re.compile(r"<[^>]+>")
OCR_TEXT_THRESHOLD = 80
EXTRACTION_VERSION = 2
_ocr_local = threading.local()
ONNX_RUNTIME_ERRORS = (
    onnx_errors.DeviceReset,
    onnx_errors.EPFail,
    onnx_errors.EngineError,
    onnx_errors.Fail,
    onnx_errors.InvalidArgument,
    onnx_errors.InvalidGraph,
    onnx_errors.InvalidProtobuf,
    onnx_errors.ModelLoadCanceled,
    onnx_errors.NoModel,
    onnx_errors.NoSuchFile,
    onnx_errors.NotFound,
    onnx_errors.NotImplemented,
    onnx_errors.RuntimeException,
)


@dataclass(frozen=True)
class Record:
    media_id: str
    record_date: str
    company: str
    fei: str
    state: str
    country: str
    establishment_type: str
    publish_date: str
    download_url: str
    record_type: str = "483"


def fetch(url: str, timeout: int = 90) -> tuple[bytes, dict[str, str]]:
    if not url.startswith(FDA_ORIGIN + "/"):
        raise ValueError("refusing to fetch a non-FDA URL")
    with tempfile.NamedTemporaryFile() as header_file:
        result = subprocess.run(
            [
                "curl",
                "--location",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                str(timeout),
                "--dump-header",
                header_file.name,
                url,
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"FDA download failed ({result.returncode}): {message}")
        header_file.seek(0)
        blocks = re.split(rb"\r?\n\r?\n", header_file.read().strip())
        final_headers = blocks[-1].decode("iso-8859-1", errors="replace")
        headers = {}
        for line in final_headers.splitlines()[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower()] = value.strip()
        return result.stdout, headers


def clean_cell(value: str) -> str:
    return html.unescape(TAG_RE.sub("", value)).strip()


def row_identity(row: list[str]) -> tuple[str, ...]:
    values = [clean_cell(value) for value in row[:8]]
    media_match = MEDIA_RE.search(row[3])
    values[3] = media_match.group(1) if media_match else row[3].strip()
    return tuple(values)


def discover_api_config(page: bytes) -> dict[str, object]:
    text = page.decode("utf-8")
    match = re.search(
        r'<script type="application/json"[^>]*data-drupal-selector='
        r'"drupal-settings-json"[^>]*>(.*?)</script>',
        text,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("FDA page did not expose Drupal data-table settings")
    settings = json.loads(html.unescape(match.group(1)))
    tables = settings.get("datatables", {})
    for table in tables.values():
        ajax = table.get("ajax", {})
        data = ajax.get("data", {})
        if data.get("view_name") == "ora_foia_electronic_reading_room_solr":
            return {"url": FDA_ORIGIN + ajax["url"], "data": data}
    raise RuntimeError("FDA reading-room data-table configuration was not found")


def parse_record(row: list[str]) -> Record:
    media_match = MEDIA_RE.search(row[3])
    if not media_match:
        raise ValueError("Record has no FDA media download link")
    media_id = media_match.group(1)
    return Record(
        media_id=media_id,
        record_date=clean_cell(row[0]),
        company=clean_cell(row[1]),
        fei=clean_cell(row[2]),
        state=clean_cell(row[4]),
        country=clean_cell(row[5]),
        establishment_type=clean_cell(row[6]),
        publish_date=clean_cell(row[7]),
        download_url=f"{FDA_ORIGIN}/media/{media_id}/download",
        record_type=clean_cell(row[3]),
    )


def has_useful_text(text: str) -> bool:
    return len(re.findall(r"[A-Za-z0-9]", text)) >= OCR_TEXT_THRESHOLD


def ocr_page(document: pymupdf.Document, page_number: int) -> str:
    engine = getattr(_ocr_local, "engine", None)
    if engine is None:
        engine = RapidOCR()
        _ocr_local.engine = engine
    page = document.load_page(page_number)
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )
    image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    result, _ = engine(image)
    return "\n".join(item[1] for item in result) if result else ""


def extract_pages(payload: bytes) -> tuple[list[str], list[int]]:
    try:
        reader = PdfReader(BytesIO(payload))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except PyPdfError:
        with pymupdf.open(stream=payload, filetype="pdf") as document:
            pages = [page.get_text("text").strip() for page in document]

    ocr_pages = [index for index, text in enumerate(pages) if not has_useful_text(text)]
    if ocr_pages:
        with pymupdf.open(stream=payload, filetype="pdf") as document:
            for index in ocr_pages:
                ocr_text = ocr_page(document, index).strip()
                if has_useful_text(ocr_text):
                    pages[index] = ocr_text
    incomplete = [
        index + 1 for index, text in enumerate(pages) if not has_useful_text(text)
    ]
    return pages, incomplete


def datatable_parameters(config: dict[str, object]) -> dict[str, object]:
    parameters = {
        **config["data"],
        "foia_record_type_name": "",
        "search[value]": "",
        "search[regex]": "false",
    }
    for index in range(9):
        parameters.update(
            {
                f"columns[{index}][data]": index,
                f"columns[{index}][name]": "",
                f"columns[{index}][searchable]": "true",
                f"columns[{index}][orderable]": "false" if index == 8 else "true",
                f"columns[{index}][search][value]": "",
                f"columns[{index}][search][regex]": "false",
            }
        )
    for index, (column, direction) in enumerate(
        ((0, "desc"), (1, "asc"), (2, "asc"))
    ):
        parameters[f"order[{index}][column]"] = column
        parameters[f"order[{index}][dir]"] = direction
    return parameters


def list_records(
    limit: int | None = None,
    batch_size: int = 100,
    max_passes: int = 20,
    stable_passes_required: int = 3,
) -> tuple[list[Record], int, int]:
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive integer")
    page, _ = fetch(READING_ROOM_URL)
    config = discover_api_config(page)
    base_parameters = datatable_parameters(config)
    records: dict[str, Record] = {}
    unavailable: set[tuple[str, str, str]] = set()
    seen_rows: set[tuple[str, ...]] = set()
    available = 0
    passes = 1 if limit is not None else max_passes
    passes_completed = 0
    stable_passes = 0
    previous_seen = 0

    for pass_number in range(passes):
        passes_completed = pass_number + 1
        start = 0
        while True:
            length = min(batch_size, limit - len(records)) if limit else batch_size
            params = {
                **base_parameters,
                "draw": pass_number * 100 + start // batch_size + 1,
                "start": start,
                "length": length,
            }
            payload, _ = fetch(f"{config['url']}?{urlencode(params)}")
            response = json.loads(payload)
            available = max(available, int(response["recordsFiltered"]))
            rows = response.get("data", [])
            if not rows:
                break
            for row in rows:
                seen_rows.add(row_identity(row))
                try:
                    record = parse_record(row)
                    records.setdefault(record.media_id, record)
                except ValueError:
                    unavailable.add(
                        (clean_cell(row[0]), clean_cell(row[1]), clean_cell(row[2]))
                    )
                if limit is not None and len(records) >= limit:
                    break
            if limit is not None and len(records) >= limit:
                break
            start += len(rows)
            if start >= int(response["recordsFiltered"]):
                break

        if limit is not None:
            break
        print(
            f"Discovery pass {pass_number + 1}: {len(records)} unique PDF links "
            f"across {len(seen_rows)} of {available} metadata rows.",
            flush=True,
        )
        if len(seen_rows) >= available:
            break
        if len(seen_rows) == previous_seen:
            stable_passes += 1
        else:
            stable_passes = 0
        previous_seen = len(seen_rows)
        if stable_passes >= stable_passes_required:
            break

    if (
        limit is None
        and len(seen_rows) < available
        and stable_passes < stable_passes_required
    ):
        raise RuntimeError(
            "FDA pagination remained unstable after "
            f"{passes_completed} passes: discovered "
            f"{len(seen_rows)} of {available} rows"
        )
    selected = list(records.values())
    if limit is not None:
        selected = selected[:limit]
    return selected, available, len(unavailable)


def extract_pdf(record: Record) -> dict[str, object]:
    try:
        payload, headers = fetch(record.download_url)
        if not payload.startswith(b"%PDF"):
            raise ValueError("download did not return a PDF")
        pages, incomplete = extract_pages(payload)
        if not pages:
            raise ValueError("PDF parser returned zero pages")
        content = "\n\n".join(
            f"--- Page {number} ---\n{text}"
            for number, text in enumerate(pages, start=1)
            if text
        )
        disposition = headers.get("content-disposition", "")
        filename_match = re.search(r'filename="?([^";]+)', disposition)
        return {
            "record": record,
            "filename": filename_match.group(1) if filename_match else "",
            "page_count": len(pages),
            "content": content,
            "status": "indexed" if not incomplete else "ocr_required",
            "error": (
                ""
                if not incomplete
                else "OCR returned insufficient text on pages: "
                + ", ".join(map(str, incomplete))
            ),
        }
    except (
        RuntimeError,
        ValueError,
        OSError,
        EOFError,
        KeyError,
        IndexError,
        TypeError,
        NotImplementedError,
        PyPdfError,
        pymupdf.FileDataError,
        cv2.error,
        *ONNX_RUNTIME_ERRORS,
    ) as error:
        return {
            "record": record,
            "filename": "",
            "page_count": 0,
            "content": "",
            "status": "error",
            "error": str(error),
        }


def save_result(connection: sqlite3.Connection, result: dict[str, object]) -> None:
    record = result["record"]
    connection.execute(
        """
        INSERT INTO documents (
            media_id, record_type, record_date, company, fei, state, country,
            establishment_type, publish_date, download_url, filename,
            page_count, content, extraction_status, extraction_version,
            error, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(media_id) DO UPDATE SET
            record_type=excluded.record_type, record_date=excluded.record_date,
            company=excluded.company,
            fei=excluded.fei, state=excluded.state, country=excluded.country,
            establishment_type=excluded.establishment_type,
            publish_date=excluded.publish_date,
            download_url=excluded.download_url, filename=excluded.filename,
            page_count=excluded.page_count, content=excluded.content,
            extraction_status=excluded.extraction_status,
            extraction_version=excluded.extraction_version,
            error=excluded.error,
            indexed_at=excluded.indexed_at
        """,
        (
            record.media_id,
            record.record_type,
            record.record_date,
            record.company,
            record.fei,
            record.state,
            record.country,
            record.establishment_type,
            record.publish_date,
            record.download_url,
            result["filename"],
            result["page_count"],
            result["content"],
            result["status"],
            EXTRACTION_VERSION,
            result["error"],
            datetime.now(UTC).isoformat(),
        ),
    )


def index_records(
    database: str | Path,
    limit: int | None = None,
    workers: int = 2,
    refresh: bool = False,
) -> tuple[int, int]:
    records, available, unavailable = list_records(limit)
    connection = connect(database)
    connection.execute(
        "INSERT OR REPLACE INTO sync_state(key, value) VALUES ('source_total', ?)",
        (str(available),),
    )
    connection.execute(
        "INSERT OR REPLACE INTO sync_state(key, value) VALUES "
        "('source_unavailable', ?)",
        (str(unavailable),),
    )
    connection.execute(
        "INSERT OR REPLACE INTO sync_state(key, value) VALUES "
        "('source_downloadable', ?)",
        (str(len(records)),),
    )
    connection.execute(
        "INSERT OR REPLACE INTO sync_state(key, value) VALUES ('sync_started_at', ?)",
        (datetime.now(UTC).isoformat(),),
    )
    connection.commit()
    if unavailable:
        print(
            f"FDA lists {unavailable} records without a PDF download link; skipped.",
            flush=True,
        )
    existing = {
        row["media_id"]
        for row in connection.execute(
            "SELECT media_id FROM documents "
            "WHERE extraction_status = 'indexed' AND extraction_version >= ?",
            (EXTRACTION_VERSION,),
        )
    }
    pending = records if refresh else [r for r in records if r.media_id not in existing]
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(extract_pdf, record): record for record in pending}
        for future in as_completed(futures):
            result = future.result()
            save_result(connection, result)
            connection.commit()
            completed += 1
            record = result["record"]
            print(
                f"[{completed}/{len(pending)}] {result['status']}: "
                f"{record.company} ({record.media_id})",
                flush=True,
            )
    connection.close()
    return len(records), completed


def main() -> None:
    parser = argparse.ArgumentParser(description="Index FDA reading-room PDF full text")
    parser.add_argument("--database", default="data/fda483.sqlite3")
    parser.add_argument("--limit", type=int, help="Index only the newest N records")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        discovered, indexed = index_records(
            args.database, args.limit, args.workers, args.refresh
        )
        print(f"Discovered {discovered}; processed {indexed}.")
    except (RuntimeError, HTTPError, URLError, json.JSONDecodeError) as error:
        print(f"Indexing failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
