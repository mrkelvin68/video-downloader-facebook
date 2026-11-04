import csv, tempfile, unittest
from pathlib import Path
from app.services.page_export_service import export_pages_csv, PAGE_CSV_FIELDS


class PageExportTests(unittest.TestCase):
    def test_export_unicode_dedupe_missing_id_long_url(self):
        long_url = "https://www.facebook.com/" + "a" * 600
        pages = [{"page_name": "Phim Hộ Linh Tráng Sĩ", "page_url": "https://f.com/1", "page_id": "1", "data_source": "S"},
                 {"page_name": "dup", "page_url": "https://f.com/1"},
                 {"page_name": "NoId", "page_url": long_url}, {"page_name": "nourl"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.csv"
            self.assertEqual(export_pages_csv(path, pages), 2)
            self.assertEqual(path.read_bytes()[:3], b"\xef\xbb\xbf")
            rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
        self.assertEqual(tuple(rows[0]), PAGE_CSV_FIELDS)
        self.assertEqual(rows[0]["page_name"], "Phim Hộ Linh Tráng Sĩ")
        self.assertEqual(rows[1]["page_id"], "")
        self.assertEqual(rows[1]["page_url"], long_url)
        self.assertEqual(rows[1]["stt"], "2")


if __name__ == "__main__":
    unittest.main()
