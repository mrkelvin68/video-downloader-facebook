import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

from app.services.viral_reels_export_service import (
    export_csv,
    export_xlsx,
    sync_google_sheet,
)


class FakeWorksheet:
    def __init__(self, records=None):
        self.records = list(records or [])
        self.appended = []
        self.updated = None

    def get_all_records(self):
        return [dict(row) for row in self.records]

    def append_rows(self, values):
        self.appended.extend(values)

    def update(self, range_name, values):
        self.updated = (range_name, values)


class ViralReelsExportTests(unittest.TestCase):
    def test_csv_preserves_appends_deduplicates_and_updates(self):
        with tempfile.TemporaryDirectory(dir=".") as folder:
            path = Path(folder) / "reels.csv"
            self.assertEqual(
                export_csv(path, [{"reel_id": "1", "reel_url": "u1", "views": 10}]), 1
            )
            self.assertEqual(
                export_csv(
                    path,
                    [
                        {"reel_id": "1", "reel_url": "u1", "views": 99},
                        {"reel_id": "2", "reel_url": "u2", "views": 20},
                    ],
                ),
                1,
            )
            self.assertEqual(
                export_csv(
                    path,
                    [{"reel_id": "1", "reel_url": "u1", "views": 30, "caption": "new"}],
                    update_existing=True,
                ),
                1,
            )
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["reel_id"] for row in rows], ["1", "2"])
            self.assertEqual(rows[0]["views"], "30")
            self.assertEqual(rows[0]["caption"], "new")
            self.assertEqual(rows[1]["views"], "20")

    def test_xlsx_or_clear_dependency_error(self):
        with tempfile.TemporaryDirectory(dir=".") as folder:
            path = Path(folder) / "reels.xlsx"
            if importlib.util.find_spec("openpyxl") is None:
                with self.assertRaisesRegex(RuntimeError, "openpyxl"):
                    export_xlsx(path, [{"reel_id": "1"}])
                return

            self.assertEqual(export_xlsx(path, [{"reel_id": "1", "views": 1}]), 1)
            self.assertEqual(
                export_xlsx(
                    path,
                    [{"reel_id": "1", "views": 2}, {"reel_id": "2", "views": 3}],
                    update_existing=True,
                ),
                2,
            )
            import openpyxl

            rows = list(openpyxl.load_workbook(path).active.values)
            self.assertEqual(rows[0], ("reel_id", "views"))
            self.assertEqual(rows[1:], [("1", 2), ("2", 3)])

    def test_sheet_appends_only_new_records(self):
        sheet = FakeWorksheet([{"reel_id": "1", "reel_url": "u1", "views": 10}])
        changed = sync_google_sheet(
            [
                {"reel_id": "1", "reel_url": "u1", "views": 99},
                {"reel_id": "2", "reel_url": "u2", "views": 20},
            ],
            worksheet=sheet,
        )
        self.assertEqual(changed, 1)
        self.assertEqual(sheet.appended, [["2", "u2", 20]])
        self.assertIsNone(sheet.updated)

    def test_sheet_updates_existing_metadata_and_accepts_client(self):
        sheet = FakeWorksheet([{"reel_id": "1", "reel_url": "u1", "views": 10}])

        class Book:
            sheet1 = sheet

        class Client:
            def open(self, name):
                self.name = name
                return Book()

        changed = sync_google_sheet(
            [{"reel_id": "1", "reel_url": "u1", "views": 99}],
            client=Client(),
            spreadsheet="viral reels",
            update_existing=True,
        )
        self.assertEqual(changed, 1)
        self.assertEqual(
            sheet.updated,
            ("A1", [["reel_id", "reel_url", "views"], ["1", "u1", 99]]),
        )


if __name__ == "__main__":
    unittest.main()
