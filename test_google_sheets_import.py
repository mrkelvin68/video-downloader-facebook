import unittest
import tempfile
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.services.google_sheets_service import google_sheet_csv_url, parse_google_sheet_csv
from app.database.connection import Database
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.ui.pages.project_detail_page import ProjectDetailPage


class GoogleSheetsImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_public_url_and_named_tab_are_converted_to_csv_endpoints(self):
        url = "https://docs.google.com/spreadsheets/d/abc_123/edit?gid=42"
        self.assertIn("export?format=csv&gid=42", google_sheet_csv_url(url))
        self.assertIn("gviz/tq?tqx=out:csv&sheet=Videos", google_sheet_csv_url(url, "Videos"))

    def test_optional_columns_defaults_and_duplicate_preview_stats(self):
        text = "title,url\nVideo 1,https://www.facebook.com/reel/abc\n,https://www.facebook.com/reel/abc\n"
        csv_text, rows, errors, stats = parse_google_sheet_csv(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stt"], 1)
        self.assertEqual(rows[0]["ten_video"], "Video 1")
        self.assertEqual(stats, {"total": 2, "valid": 1, "invalid": 1, "duplicates": 1})
        self.assertIn("URL trùng", errors[0])
        self.assertEqual(csv_text, "")

    def test_missing_url_column_is_reported(self):
        _, rows, errors, stats = parse_google_sheet_csv("title\nVideo 1\n")
        self.assertEqual(rows, [])
        self.assertIn("url", errors[0])
        self.assertEqual(stats["valid"], 0)

    def test_valid_sheet_rows_use_existing_video_import_and_sqlite(self):
        csv_text, rows, errors, _ = parse_google_sheet_csv(
            "stt,title,url\n2,Second,https://www.facebook.com/reel/2\n1,First,https://www.facebook.com/reel/1\n"
        )
        self.assertFalse(errors)
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            project_id = ProjectService(database).create("Test", "t", str(Path(directory) / "videos"))
            imported = VideoService(database).import_csv(project_id, csv_text)
            self.assertEqual(imported, len(rows))
            self.assertEqual([row["sequence_number"] for row in database.list_videos(project_id)], [1, 2])
            database.close()

    def test_drop_zone_click_emits_csv_selection_without_dialog_logic(self):
        page = ProjectDetailPage()
        selected = []
        page.csvChosen.connect(lambda: selected.append(True))
        page.drop_zone.show()
        QTest.mouseClick(page.drop_zone, Qt.MouseButton.LeftButton)
        self.assertEqual(selected, [True])
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
