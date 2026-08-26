import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fda483.database import connect
from fda483.search import search_documents
from fda483.search import index_status


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "test.sqlite3"
        connection = connect(self.database)
        connection.execute(
            """
            INSERT INTO documents (
                media_id, record_type, record_date, company, fei, state, country,
                establishment_type, publish_date, download_url, filename,
                page_count, content, extraction_status, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "193964",
                "483",
                "07/17/2026",
                "Cascade Specialty Pharmacy LLC",
                "3015133983",
                "Washington",
                "United States",
                "Producer of Non Sterile Drug Products",
                "07/31/2026",
                "https://www.fda.gov/media/193964/download",
                "cascade.pdf",
                6,
                "The responsibilities and procedures applicable to the "
                "quality control unit are not fully followed.",
                "indexed",
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.directory.cleanup()

    def test_searches_pdf_full_text(self):
        result = search_documents(self.database, "control unit")
        self.assertEqual(result["total"], 1)
        self.assertIn("<mark>", result["results"][0]["snippet"])

    def test_does_not_expand_a_typo(self):
        result = search_documents(self.database, "responsibilites")
        self.assertEqual(result["total"], 0)

    def test_does_not_expand_a_first_character_typo(self):
        result = search_documents(self.database, "xontrol")
        self.assertEqual(result["total"], 0)

    def test_matches_exact_prefix_and_requires_every_term(self):
        self.assertEqual(search_documents(self.database, "responsib control")["total"], 1)
        self.assertEqual(search_documents(self.database, "control missing")["total"], 0)

    def test_filters_by_state_and_year(self):
        result = search_documents(
            self.database, "", state="Washington", year="2026"
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(
            result["results"][0]["download_url"],
            "https://www.fda.gov/media/193964/download",
        )

    def test_filters_by_record_type_and_lists_filter_options(self):
        result = search_documents(self.database, "", record_type="483")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["record_types"], ["483"])
        self.assertEqual(result["years"], ["2026"])

        self.assertEqual(
            search_documents(self.database, "", record_type="Warning Letter")[
                "total"
            ],
            0,
        )

    def test_status_uses_available_source_total(self):
        connection = connect(self.database)
        connection.execute(
            "INSERT INTO sync_state(key, value) VALUES ('source_total', '2154')"
        )
        connection.execute(
            "INSERT INTO sync_state(key, value) VALUES ('source_unavailable', '33')"
        )
        connection.execute(
            "INSERT INTO sync_state(key, value) VALUES ('source_downloadable', '2120')"
        )
        connection.commit()
        connection.close()
        status = index_status(self.database)
        self.assertEqual(status["source_total"], 2154)
        self.assertEqual(status["source_downloadable"], 2120)


if __name__ == "__main__":
    unittest.main()
