import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.services.google_sheets_service import google_sheet_csv_url, parse_google_sheet_csv
from app.database.connection import Database
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.ui.pages.project_detail_page import ProjectDetailPage
from app.ui.dialogs.manual_video_import_dialog import ManualVideoImportDialog


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

    def test_google_sheet_stt_column_is_ignored_and_auto_numbered(self):
        _, rows, errors, _ = parse_google_sheet_csv(
            "stt,title,url\n999,First,https://www.facebook.com/reel/1\ninvalid,Second,https://www.facebook.com/reel/2\n"
        )
        self.assertFalse(errors)
        self.assertEqual([row["stt"] for row in rows], [1, 2])

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

    def test_manual_import_button_emits_request(self):
        page = ProjectDetailPage()
        selected = []
        page.manualImportRequested.connect(lambda: selected.append(True))
        QTest.mouseClick(page.manual_import_button, Qt.MouseButton.LeftButton)
        self.assertEqual(selected, [True])
        page.deleteLater()

    def test_manual_dialog_saves_title_url_and_auto_numbers_rows(self):
        dialog = ManualVideoImportDialog()
        dialog.table.item(0, 0).setText("First")
        dialog.table.item(0, 1).setText("https://www.facebook.com/reel/1")
        dialog.add_row()
        dialog.table.item(1, 0).setText("Second")
        dialog.table.item(1, 1).setText("https://www.facebook.com/reel/2")
        dialog.save_rows()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        self.assertEqual([row["stt"] for row in dialog.rows], [1, 2])
        self.assertIn("stt,title,url", dialog.csv_text)
        dialog.deleteLater()

    def test_manual_dialog_keeps_rows_when_one_row_is_invalid(self):
        dialog = ManualVideoImportDialog()
        dialog.table.item(0, 0).setText("Good row")
        dialog.table.item(0, 1).setText("https://www.facebook.com/reel/1")
        dialog.add_row()
        dialog.table.item(1, 0).setText("Bad row")
        dialog.table.item(1, 1).setText("https://example.com/not-facebook")
        with patch("app.ui.dialogs.manual_video_import_dialog.QMessageBox.warning"):
            dialog.save_rows()
        self.assertEqual(dialog.result(), dialog.DialogCode.Rejected)
        self.assertEqual(dialog.table.item(0, 0).text(), "Good row")
        self.assertEqual(dialog.table.item(1, 0).text(), "Bad row")
        restored = ManualVideoImportDialog(rows=[{"ten_video": "Good row", "url": "https://www.facebook.com/reel/1"}])
        self.assertEqual(restored.table.rowCount(), 1)
        self.assertEqual(restored.table.item(0, 0).text(), "Good row")
        dialog.deleteLater()
        restored.deleteLater()


if __name__ == "__main__":
    unittest.main()
