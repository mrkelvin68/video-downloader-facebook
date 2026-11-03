import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app import ProjectStore, parse_video_csv, validate_windows_name, validate_output_dir


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ProjectStore(self.root / "projects.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def create(self, name="Dự án A", prefix="fb", folder="video-a", notes="Ghi chú"):
        return self.store.create(name, prefix, str(self.root / folder), notes)

    def test_schema_create_persist_and_safe_edit(self):
        project_uuid = self.create()
        self.assertEqual(uuid.UUID(project_uuid).version, 4)
        row = self.store.list_projects()[0]
        self.assertEqual(set(row.keys()), {"uuid", "name", "prefix", "output_dir", "notes", "created_at", "updated_at"})
        self.assertTrue(Path(row["output_dir"]).is_dir())
        self.store.update(project_uuid, "Dự án B", "fb-new", row["output_dir"], "Mới")
        updated = self.store.list_projects()[0]
        self.assertEqual((updated["name"], updated["prefix"], updated["notes"]), ("Dự án B", "fb-new", "Mới"))
        self.store.close()
        reopened = ProjectStore(self.root / "projects.sqlite3")
        self.assertEqual(reopened.list_projects()[0]["uuid"], project_uuid)
        reopened.close()

    def test_new_folder_must_not_overwrite(self):
        folder = self.root / "exists"
        folder.mkdir()
        with self.assertRaisesRegex(ValueError, "đã tồn tại"):
            self.store.create("Tên", "fb", str(folder))
        self.create()
        with self.assertRaisesRegex(ValueError, "đã tồn tại"):
            self.store.update(self.store.list_projects()[0]["uuid"], "Tên mới", "fb", str(folder))

    def test_duplicate_name_and_output_directory_are_rejected(self):
        self.create()
        with self.assertRaisesRegex(ValueError, "đã tồn tại"):
            self.create("Dự án A", folder="video-b")
        with self.assertRaisesRegex(ValueError, "đã tồn tại"):
            self.create("Dự án B", folder="video-a")

    def test_windows_name_validation(self):
        for value in ("CON", "aux.txt", "a/b", "a.", "a ", "a:b", "x" * 121):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_windows_name(value, "Tên")
        self.assertEqual(validate_windows_name("Hợp lệ", "Tên"), "Hợp lệ")

    def test_output_directory_must_be_absolute_and_writable(self):
        with self.assertRaises(ValueError):
            validate_output_dir("relative", may_exist=False)
        missing_parent = self.root / "missing" / "child"
        with self.assertRaises(ValueError):
            validate_output_dir(str(missing_parent), may_exist=False)
        with patch("app.os.access", return_value=False):
            with self.assertRaisesRegex(ValueError, "quyền ghi"):
                validate_output_dir(str(self.root / "child"), may_exist=False)

    def test_no_delete_api(self):
        self.assertFalse(hasattr(self.store, "delete"))

    def test_csv_validation_and_store_round_trip(self):
        csv_text = "\ufeffsequence;url;title\n1;\"https://www.facebook.com/watch/?v=1\";\"One; video\"\n"
        rows, errors = parse_video_csv(csv_text)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["sequence"], 1)
        project_uuid = self.create()
        self.assertEqual(self.store.import_csv(project_uuid, csv_text), 1)
        exported = self.store.export_csv(project_uuid)
        self.assertTrue(exported.startswith("\ufeff"))
        self.assertIn("fb-1", self.store.connection.execute("SELECT video_code FROM videos").fetchone()["video_code"])
        self.assertIn("One; video", exported)

    def test_csv_rejects_duplicate_normalized_urls_and_non_facebook(self):
        rows, errors = parse_video_csv("sequence,url,title\n1,https://www.facebook.com/a/,A\n2,https://www.facebook.com/a,B\n3,https://example.com/a,C\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 2)

    def test_ui_phase2_smoke(self):
        from PySide6.QtWidgets import QApplication
        from app import MainWindow
        app = QApplication.instance() or QApplication([])
        window = MainWindow(self.store)
        p_uuid = self.create()
        project = dict(self.store.list_projects()[0])
        window.show_detail(project)
        self.assertEqual(window.pages.currentIndex(), 1)
        self.assertFalse(window.confirm.isEnabled())

        # Test loading valid CSV without modal blocking
        csv_file = self.root / "valid.csv"
        csv_file.write_text("sequence,url,title\n1,https://www.facebook.com/watch/?v=1,Clip 1\n", encoding="utf-8")
        with patch.object(window, "show_csv_preview"):
            window.load_csv(csv_file)
        self.assertEqual(len(window.csv_rows), 1)
        self.assertEqual(window.csv_errors, [])
        self.assertTrue(window.confirm.isEnabled())

        # Test confirm import persists to DB
        with patch("app.QMessageBox.information"):
            window.confirm_import()
        videos = self.store.list_videos(p_uuid)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["video_code"], "fb-1")
        self.assertFalse(window.confirm.isEnabled())

        # Test loading invalid CSV disables confirm
        bad_file = self.root / "bad.csv"
        bad_file.write_text("sequence,url\n1,invalid-url\n", encoding="utf-8")
        with patch("app.QMessageBox.question", return_value=app.style()):  # confirm reimport
            from PySide6.QtWidgets import QMessageBox
            with patch("app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                with patch.object(window, "show_csv_preview"):
                    window.load_csv(bad_file)
        self.assertFalse(window.confirm.isEnabled())
        self.assertTrue(len(window.csv_errors) > 0)


if __name__ == "__main__":
    unittest.main()
