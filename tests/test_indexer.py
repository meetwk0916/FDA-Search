import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import onnxruntime.capi.onnxruntime_pybind11_state as onnx_errors

from fda483.indexer import (
    Record,
    clean_cell,
    datatable_parameters,
    discover_api_config,
    extract_pdf,
    has_useful_text,
    list_records,
    parse_record,
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
        records, available, unavailable = list_records(
            batch_size=100, max_passes=10, stable_passes_required=2
        )
        self.assertEqual([record.record_type for record in records], ["Warning Letter"])
        self.assertEqual(available, 2)
        self.assertEqual(unavailable, 0)
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
