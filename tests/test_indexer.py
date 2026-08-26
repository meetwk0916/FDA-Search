import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import onnxruntime.capi.onnxruntime_pybind11_state as onnx_errors

from fda483.database import connect
from fda483.indexer import (
    Discovery,
    Record,
    acquire_index_lock,
    clean_cell,
    datatable_parameters,
    discover_api_config,
    extract_pdf,
    has_useful_text,
    index_records,
    list_records,
    parse_record,
    run_periodically,
    row_identity,
)


class IndexerTests(unittest.TestCase):
    def test_clean_cell_removes_markup_and_decodes_entities(self):
        self.assertEqual(clean_cell("<strong>A&amp;B</strong>\n"), "A&B")

    def test_parse_record_builds_original_download_url(self):
        row = [
            '<time datetime="2026-07-17">07/17/2026</time>',
            "Cascade Specialty Pharmacy LLC",
            "3015133983",
            '<a href="/media/193964/download">483</a>',
            "Washington",
            "",
            "Producer of Non Sterile Drug Products",
            "<time>07/31/2026</time>",
            "",
        ]
        record = parse_record(row)
        self.assertEqual(record.media_id, "193964")
        self.assertEqual(record.record_type, "483")
        self.assertEqual(
            record.download_url, "https://www.fda.gov/media/193964/download"
        )

    def test_discovers_drupal_api_configuration(self):
        settings = (
            '{"datatables":{"id":{"ajax":{"url":"/datatables/views/ajax",'
            '"data":{"view_name":"ora_foia_electronic_reading_room_solr"}}}}}'
        )
        page = (
            '<script type="application/json" data-drupal-selector='
            f'"drupal-settings-json">{settings}</script>'
        ).encode()
        config = discover_api_config(page)
        self.assertEqual(config["url"], "https://www.fda.gov/datatables/views/ajax")

    def test_rejects_page_number_only_as_useful_text(self):
        self.assertFalse(has_useful_text("FORM FDA 483 (09/08) PAGE 1 OF 5"))
        self.assertTrue(
            has_useful_text(
                "OBSERVATION 1 The responsibilities and procedures applicable "
                "to the quality control unit are not fully followed and "
                "documented by the inspected establishment."
            )
        )

    def test_datatable_request_has_stable_multi_column_order(self):
        params = datatable_parameters({"data": {"view_name": "example"}})
        self.assertEqual(params["order[0][column]"], 0)
        self.assertEqual(params["order[1][column]"], 1)
        self.assertEqual(params["order[2][column]"], 2)
        self.assertEqual(params["columns[8][orderable]"], "false")
        self.assertEqual(params["foia_record_type_name"], "")

    def test_row_identity_distinguishes_duplicate_media_records(self):
        first = ["01/01/2026", "Company A", "1", '<a href="/media/1/download">483</a>']
        second = ["01/02/2026", "Company A", "1", '<a href="/media/1/download">483</a>']
        self.assertNotEqual(row_identity(first), row_identity(second))

    def test_database_lock_rejects_a_second_indexer(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "index.sqlite3"
            with acquire_index_lock(database):
                with self.assertRaisesRegex(
                    RuntimeError, f"already running.*PID {os.getpid()}"
                ):
                    with acquire_index_lock(database):
                        pass

    def test_periodic_runner_repeats_after_the_interval_until_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            stop_event = threading.Event()
            cycles = []
            waits = []

            def cycle():
                cycles.append(len(cycles) + 1)
                if len(cycles) == 2:
                    stop_event.set()

            run_periodically(
                Path(directory) / "index.sqlite3",
                interval=43200,
                cycle=cycle,
                stop_event=stop_event,
                wait=lambda seconds: waits.append(seconds) or False,
            )

            self.assertEqual(cycles, [1, 2])
            self.assertEqual(waits, [43200])

    def test_periodic_runner_retries_known_cycle_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            stop_event = threading.Event()
            attempts = []

            def cycle():
                attempts.append(len(attempts) + 1)
                if len(attempts) == 1:
                    raise RuntimeError("temporary FDA failure")
                stop_event.set()

            run_periodically(
                Path(directory) / "index.sqlite3",
                interval=60,
                cycle=cycle,
                stop_event=stop_event,
                wait=lambda _seconds: False,
            )

            self.assertEqual(attempts, [1, 2])

    @patch("fda483.indexer.list_records")
    @patch("fda483.indexer.extract_pdf")
    def test_indexing_stops_submitting_documents_after_shutdown(
        self, extract_pdf, list_records
    ):
        with tempfile.TemporaryDirectory() as directory:
            stop_event = threading.Event()
            records = [
                Record(str(index), "", f"Company {index}", "", "", "", "", "", "")
                for index in range(3)
            ]
            list_records.return_value = Discovery(records, 3, 3, 0, 0, 0)

            def extract(record):
                stop_event.set()
                return {
                    "record": record,
                    "filename": "",
                    "page_count": 1,
                    "content": "indexed content",
                    "status": "indexed",
                    "error": "",
                }

            extract_pdf.side_effect = extract
            _, completed = index_records(
                Path(directory) / "index.sqlite3",
                workers=1,
                stop_event=stop_event,
            )

            self.assertEqual(completed, 1)
            self.assertEqual(extract_pdf.call_count, 1)
            connection = connect(Path(directory) / "index.sqlite3")
            sync_state = dict(
                connection.execute("SELECT key, value FROM sync_state").fetchall()
            )
            connection.close()
            self.assertEqual(sync_state["sync_status"], "idle")
            self.assertEqual(sync_state["sync_phase"], "idle")
            self.assertEqual(sync_state["sync_pending"], "3")
            self.assertEqual(sync_state["sync_processed"], "1")
            self.assertEqual(sync_state["source_enumerated"], "3")
            self.assertIn("sync_completed_at", sync_state)
            self.assertNotIn("sync_last_success_at", sync_state)

    @patch(
        "fda483.indexer.list_records",
        side_effect=RuntimeError("FDA discovery unavailable"),
    )
    def test_failed_one_shot_cycle_persists_failure_state(self, _list_records):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "index.sqlite3"
            connection = connect(database)
            connection.execute(
                "INSERT INTO sync_state(key, value) VALUES "
                "('sync_completed_at', 'old completion')"
            )
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(RuntimeError, "discovery unavailable"):
                index_records(database)

            connection = connect(database)
            sync_state = dict(
                connection.execute("SELECT key, value FROM sync_state").fetchall()
            )
            connection.close()
            self.assertEqual(sync_state["sync_status"], "failed")
            self.assertEqual(sync_state["sync_phase"], "failed")
            self.assertEqual(
                sync_state["sync_last_error"], "FDA discovery unavailable"
            )
            self.assertNotEqual(sync_state["sync_completed_at"], "old completion")

    @patch("fda483.indexer.fetch")
    def test_discovery_accepts_a_stable_gap_in_fda_pagination(self, fetch):
        settings = (
            '{"datatables":{"id":{"ajax":{"url":"/datatables/views/ajax",'
            '"data":{"view_name":"ora_foia_electronic_reading_room_solr"}}}}}'
        )
        page = (
            '<script type="application/json" data-drupal-selector='
            f'"drupal-settings-json">{settings}</script>'
        ).encode()
        row = [
            "07/17/2026",
            "Example Company",
            "1",
            '<a href="/media/1/download">Warning Letter</a>',
            "Washington",
            "United States",
            "Manufacturer",
            "07/31/2026",
            "",
        ]

        def response(url):
            if url.endswith("electronic-reading-room"):
                return page, {}
            start = int(parse_qs(urlparse(url).query)["start"][0])
            data = [row] if start == 0 else []
            return json.dumps({"recordsFiltered": 2, "data": data}).encode(), {}

        fetch.side_effect = response
        discovery = list_records(
            batch_size=100, max_passes=10, stable_passes_required=2
        )
        self.assertEqual(
            [record.record_type for record in discovery.records],
            ["Warning Letter"],
        )
        self.assertEqual(discovery.reported_rows, 2)
        self.assertEqual(discovery.enumerated_rows, 1)
        self.assertEqual(discovery.unavailable_rows, 0)
        self.assertEqual(discovery.duplicate_references, 0)
        self.assertEqual(discovery.pagination_gap, 1)
        self.assertLess(fetch.call_count, 10)

    @patch("fda483.indexer.extract_pages", return_value=([], []))
    @patch("fda483.indexer.fetch", return_value=(b"%PDF-1.4", {}))
    def test_zero_page_pdf_is_saved_as_error(self, _fetch, _extract):
        record = Record("1", "", "Test", "", "", "", "", "", "https://www.fda.gov/media/1/download")
        result = extract_pdf(record)
        self.assertEqual(result["status"], "error")
        self.assertIn("zero pages", result["error"])

    @patch(
        "fda483.indexer.extract_pages",
        side_effect=onnx_errors.InvalidArgument("invalid OCR input"),
    )
    @patch("fda483.indexer.fetch", return_value=(b"%PDF-1.4", {}))
    def test_onnx_error_is_isolated_to_one_document(self, _fetch, _extract):
        record = Record("1", "", "Test", "", "", "", "", "", "https://www.fda.gov/media/1/download")
        result = extract_pdf(record)
        self.assertEqual(result["status"], "error")
        self.assertIn("invalid OCR input", result["error"])


if __name__ == "__main__":
    unittest.main()
