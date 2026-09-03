from __future__ import annotations

import argparse
import fcntl
import html
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterator, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import cv2
import numpy as np
import pymupdf
import onnxruntime.capi.onnxruntime_pybind11_state as onnx_errors
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from rapidocr_onnxruntime import RapidOCR

from .database import DEFAULT_DATABASE, connect

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
KNOWN_CYCLE_ERRORS = (RuntimeError, HTTPError, URLError, json.JSONDecodeError)


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
    record_type: str


@dataclass(frozen=True)
class Discovery:
    records: list[Record]
    reported_rows: int
    enumerated_rows: int
    unavailable_rows: int
    duplicate_references: int
    pagination_gap: int


@contextmanager
def acquire_index_lock(database: str | Path) -> Iterator[TextIO]:
    database_path = Path(database).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = database_path.with_name(database_path.name + ".lock")
    lock_file = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            lock_file.seek(0)
            owner = lock_file.read().strip() or "unknown"
            raise RuntimeError(
                f"an indexer is already running for {database_path} (PID {owner})"
            ) from error
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        yield lock_file
    finally:
        lock_file.close()


def write_sync_state(
    connection: sqlite3.Connection, **values: object
) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO sync_state(key, value) VALUES (?, ?)",
        [(key, "" if value is None else str(value)) for key, value in values.items()],
    )


def persist_sync_state(database: str | Path, **values: object) -> None:
    connection = connect(database)
    write_sync_state(connection, **values)
    connection.commit()
    connection.close()


def run_periodically(
    database: str | Path,
    interval: int,
    cycle: Callable[[], object],
    stop_event: threading.Event,
    wait: Callable[[float], bool] | None = None,
) -> None:
    if interval < 1:
        raise ValueError("interval must be a positive integer")
    wait_for_stop = wait or stop_event.wait
    with acquire_index_lock(database):
        while not stop_event.is_set():
            persist_sync_state(
                database,
                sync_status="discovering",
                sync_phase="discovering",
                sync_started_at=datetime.now(UTC).isoformat(),
                sync_completed_at="",
                sync_last_error="",
                sync_pending=0,
                sync_processed=0,
            )
            if stop_event.is_set():
                persist_sync_state(
                    database,
                    sync_status="idle",
                    sync_phase="idle",
                )
                break
            try:
                cycle()
            except KNOWN_CYCLE_ERRORS as error:
                completed_at = datetime.now(UTC).isoformat()
                persist_sync_state(
                    database,
                    sync_status="failed",
                    sync_phase="failed",
                    sync_last_error=str(error),
                    sync_completed_at=completed_at,
                )
                print(f"Indexing cycle failed: {error}", file=sys.stderr, flush=True)
            else:
                state = "idle" if stop_event.is_set() else "sleeping"
                persist_sync_state(
                    database,
                    sync_status=state,
                    sync_phase=state,
                )
            if stop_event.is_set():
                break
            if wait_for_stop(interval):
                persist_sync_state(
                    database,
                    sync_status="idle",
                    sync_phase="idle",
                )
                break


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
) -> Discovery:
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
    unavailable_rows = len(unavailable)
    duplicate_references = max(
        0, len(seen_rows) - unavailable_rows - len(records)
    )
    return Discovery(
        records=selected,
        reported_rows=available,
        enumerated_rows=len(seen_rows),
        unavailable_rows=unavailable_rows,
        duplicate_references=duplicate_references,
        pagination_gap=max(0, available - len(seen_rows)),
    )


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
    stop_event: threading.Event | None = None,
) -> tuple[int, int]:
    stop_event = stop_event or threading.Event()
    connection = connect(database)
    write_sync_state(
        connection,
        sync_status="discovering",
        sync_phase="discovering",
        sync_started_at=datetime.now(UTC).isoformat(),
        sync_completed_at="",
        sync_last_error="",
        sync_pending=0,
        sync_processed=0,
    )
    connection.commit()
    try:
        if stop_event.is_set():
            write_sync_state(
                connection,
                sync_status="idle",
                sync_phase="idle",
                sync_completed_at=datetime.now(UTC).isoformat(),
            )
            connection.commit()
            return 0, 0
        discovery = list_records(limit)
        records = discovery.records
        unavailable = discovery.unavailable_rows
        write_sync_state(
            connection,
            source_total=discovery.reported_rows,
            source_enumerated=discovery.enumerated_rows,
            source_unavailable=unavailable,
            source_downloadable=len(records),
            source_duplicates=discovery.duplicate_references,
            source_pagination_gap=discovery.pagination_gap,
            sync_status="extracting",
            sync_phase="extracting",
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
        pending = (
            records
            if refresh
            else [record for record in records if record.media_id not in existing]
        )
        completed = 0
        write_sync_state(
            connection,
            sync_pending=len(pending),
            sync_processed=completed,
        )
        connection.commit()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending_records = iter(pending)
            futures = set()

            def submit_available() -> None:
                while len(futures) < workers and not stop_event.is_set():
                    try:
                        record = next(pending_records)
                    except StopIteration:
                        break
                    futures.add(executor.submit(extract_pdf, record))

            submit_available()
            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    save_result(connection, result)
                    completed += 1
                    write_sync_state(connection, sync_processed=completed)
                    connection.commit()
                    record = result["record"]
                    print(
                        f"[{completed}/{len(pending)}] {result['status']}: "
                        f"{record.company} ({record.media_id})",
                        flush=True,
                    )
                submit_available()
        completed_at = datetime.now(UTC).isoformat()
        final_state = {
            "sync_status": "idle",
            "sync_phase": "idle",
            "sync_completed_at": completed_at,
            "sync_last_error": "",
        }
        if completed == len(pending):
            final_state["sync_last_success_at"] = completed_at
        write_sync_state(
            connection,
            **final_state,
        )
        connection.commit()
        return len(records), completed
    except KNOWN_CYCLE_ERRORS as error:
        write_sync_state(
            connection,
            sync_status="failed",
            sync_phase="failed",
            sync_completed_at=datetime.now(UTC).isoformat(),
            sync_last_error=str(error),
        )
        connection.commit()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Index FDA reading-room PDF full text")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--limit", type=int, help="Index only the newest N records")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--interval",
        type=int,
        metavar="SECONDS",
        help="Repeat indexing after this many seconds",
    )
    args = parser.parse_args()
    if args.interval is not None and args.interval < 1:
        parser.error("--interval must be a positive integer")
    stop_event = threading.Event()

    def request_shutdown(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    def run_cycle() -> None:
        discovered, indexed = index_records(
            args.database,
            args.limit,
            args.workers,
            args.refresh,
            stop_event,
        )
        print(f"Discovered {discovered}; processed {indexed}.", flush=True)

    try:
        if args.interval is None:
            with acquire_index_lock(args.database):
                run_cycle()
        else:
            run_periodically(
                args.database,
                args.interval,
                run_cycle,
                stop_event,
            )
    except (*KNOWN_CYCLE_ERRORS, ValueError) as error:
        print(f"Indexing failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
