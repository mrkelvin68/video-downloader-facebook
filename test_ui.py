import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.connection import Database
from app.services.project_service import ProjectService
from app.ui.main_window import MainWindow


class ProjectCsvUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "projects.sqlite3"
        self.database = Database(self.database_path)
        self.project_id = ProjectService(self.database).create(
            "Dự án UI", "ui", str(self.root / "videos")
        )
        self.csv_a = self.root / "a.csv"
        self.csv_a.write_text(
            "stt,ten_video,url\n"
            "3,CSV A-3,https://www.facebook.com/watch/?v=3\n"
            "1,CSV A-1,https://www.facebook.com/watch/?v=1\n",
            encoding="utf-8-sig",
        )
        self.csv_b = self.root / "b.csv"
        self.csv_b.write_text(
            "stt,ten_video,url\n"
            "2,CSV B,https://www.facebook.com/watch/?v=2\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.database.close()
        self.temp.cleanup()

    def open_project(self, window):
        project = next(
            item
            for item in ProjectService(self.database).list_projects()
            if item.uuid == self.project_id
        )
        window.show_detail(project)

    def test_preview_cancel_confirmation_restart_and_export(self):
        from PySide6.QtWidgets import QMessageBox

        window = MainWindow(self.database)
        self.open_project(window)
        with patch("app.ui.main_window.CsvPreviewDialog.exec", return_value=0):
            window.load_csv(self.csv_a)
        self.assertEqual(self.database.list_videos(self.project_id), [])
        self.assertEqual(window.detail_page.video_table.model().rowCount(), 0)
        with patch("app.ui.main_window.QMessageBox.information"):
            window.confirm_import()
        saved = self.database.list_videos(self.project_id)
        self.assertEqual(
            [(row["sequence_number"], row["video_code"]) for row in saved],
            [(1, "ui-1"), (3, "ui-3")],
        )
        window.close()
        self.database.close()

        self.database = Database(self.database_path)
        restarted = MainWindow(self.database)
        self.open_project(restarted)
        self.assertEqual(restarted.detail_page.video_table.model().rowCount(), 2)
        with patch("app.ui.main_window.CsvPreviewDialog.exec", return_value=0):
            restarted.load_csv(self.csv_b)
        self.assertEqual(restarted.detail_page.video_table.model().rowCount(), 2)
        with patch(
            "app.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            restarted.confirm_import()
        self.assertEqual(
            [row["title"] for row in self.database.list_videos(self.project_id)],
            ["CSV A-1", "CSV A-3"],
        )
        export_path = self.root / "export.csv"
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(export_path), "CSV (*.csv)"),
        ):
            restarted.export_csv()
        content = export_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("\ufeffstt,ten_video,url"))
        self.assertLess(content.index("CSV A-1"), content.index("CSV A-3"))
        restarted.close()

    def test_invalid_csv_preview_cannot_be_confirmed_or_mutate_database(self):
        window = MainWindow(self.database)
        self.open_project(window)
        invalid = self.root / "invalid.csv"
        invalid.write_text(
            "stt,ten_video,url\n1,Bad,https://example.com/not-facebook\n",
            encoding="utf-8",
        )
        with patch("app.ui.main_window.CsvPreviewDialog.exec", return_value=0):
            window.load_csv(invalid)
        self.assertFalse(window.detail_page.confirm.isEnabled())
        with patch("app.ui.main_window.QMessageBox.information"):
            window.confirm_import()
        self.assertEqual(self.database.list_videos(self.project_id), [])
        window.close()

    def test_unreadable_file_clears_previous_unconfirmed_preview(self):
        window = MainWindow(self.database)
        self.open_project(window)
        with patch("app.ui.main_window.CsvPreviewDialog.exec", return_value=0):
            window.load_csv(self.csv_a)
        unreadable = self.root / "missing.csv"
        with patch("app.ui.main_window.QMessageBox.warning"):
            window.load_csv(unreadable)
        self.assertIsNone(window.pending_csv_text)
        self.assertFalse(window.detail_page.confirm.isEnabled())
        with patch("app.ui.main_window.QMessageBox.information"):
            window.confirm_import()
        self.assertEqual(self.database.list_videos(self.project_id), [])
        window.close()

    def test_create_project_from_project_dialog_and_resize_main_window(self):
        window = MainWindow(self.database)
        output_dir = self.root / "created-from-ui"

        def accept_project_dialog(dialog):
            dialog.name.setText("Dự án từ UI")
            dialog.prefix.setText("ui-created")
            dialog.output_dir.setText(str(output_dir))
            dialog.validate_and_accept()
            return dialog.result()

        with patch(
            "app.ui.main_window.ProjectDialog.exec",
            new=accept_project_dialog,
        ):
            window.add_project()
        created = next(
            project
            for project in ProjectService(self.database).list_projects()
            if project.name == "Dự án từ UI"
        )
        self.assertTrue(output_dir.is_dir())
        self.assertEqual(created.prefix, "ui-created")
        self.assertEqual(window.pages.currentIndex(), 1)
        window.show()
        for width, height in ((1366, 768), (1920, 1080)):
            window.resize(width, height)
            self.application.processEvents()
            self.assertEqual((window.width(), window.height()), (width, height))
        screenshot = window.grab()
        self.assertFalse(screenshot.isNull())
        window.close()


if __name__ == "__main__":
    unittest.main()
