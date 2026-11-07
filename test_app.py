import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app import ProjectStore, parse_video_csv, validate_output_dir, validate_windows_name
from app.core.exceptions import ImportBlockedError, ImportConfirmationRequired


class ProjectManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ProjectStore(self.root / "projects.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def create(self, name="Dự án Tiếng Việt", prefix="ep1-6", folder="videos"):
        return self.store.create(name, prefix, str(self.root / folder))

    def test_create_edit_duplicate_and_unique_uuid(self):
        first_id = self.create()
        second_id = self.create("Dự án B", "fb", "videos-b")
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(uuid.UUID(first_id).version, 4)
        with self.assertRaisesRegex(ValueError, "Tên dự án đã tồn tại"):
            self.create("dự án tiếng việt", "other", "videos-c")
        self.store.update(first_id, "Đã sửa", "ep1-6", str(self.root / "videos"), "Ghi chú")
        row = next(item for item in self.store.list_projects() if item["uuid"] == first_id)
        self.assertEqual((row["name"], row["notes"]), ("Đã sửa", "Ghi chú"))

    def test_list_projects_is_sorted_by_created_at_descending(self):
        id_1 = self.create("A Older Project", "old", "videos-old")
        id_2 = self.create("Z Newer Project", "new", "videos-new")
        self.store.database.connection.execute(
            "UPDATE projects SET created_at='2026-01-01T00:00:00+00:00' WHERE uuid=?",
            (id_1,),
        )
        self.store.database.connection.execute(
            "UPDATE projects SET created_at='2026-01-02T00:00:00+00:00' WHERE uuid=?",
            (id_2,),
        )
        self.store.database.connection.commit()
        projects = self.store.list_projects()
        self.assertEqual([p["uuid"] for p in projects], [id_2, id_1])

    def test_output_folder_permissions_and_no_overwrite(self):
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "đã tồn tại"):
            self.store.create("Không ghi đè", "fb", str(occupied))
        with self.assertRaises(ValueError):
            validate_output_dir("relative", may_exist=False)
        with patch("app.core.validators.os.access", return_value=False):
            with self.assertRaisesRegex(ValueError, "quyền ghi"):
                validate_output_dir(str(self.root / "new"), may_exist=False)
        self.assertEqual((occupied / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_edit_cannot_reuse_an_unowned_existing_folder(self):
        self.create()
        occupied = self.root / "occupied"
        occupied.mkdir()
        with self.assertRaisesRegex(ValueError, "không thuộc dự án"):
            self.store.update(
                self.store.list_projects()[0]["uuid"],
                "Đổi tên",
                "ep1-6",
                str(occupied),
            )

    def test_legacy_video_schema_migrates_without_losing_rows(self):
        self.store.close()
        connection = sqlite3.connect(self.root / "legacy.sqlite3")
        connection.execute(
            """CREATE TABLE projects (
                uuid TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL,
                output_dir TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )
        project_id = str(uuid.uuid4())
        now = "2026-01-01T00:00:00+00:00"
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, "Legacy", "old", str(self.root / "legacy-output"), "", now, now),
        )
        connection.execute(
            """CREATE TABLE videos (
                project_uuid TEXT, sequence INTEGER, code TEXT, title TEXT, url TEXT,
                normalized_url TEXT, created_at TEXT)"""
        )
        connection.execute(
            "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                7,
                "old-7",
                "Video cũ",
                "https://www.facebook.com/watch/?v=7",
                "https://facebook.com/watch/?v=7",
                now,
            ),
        )
        connection.commit()
        connection.close()

        migrated = ProjectStore(self.root / "legacy.sqlite3")
        try:
            row = migrated.list_videos(project_id)[0]
            self.assertEqual((row["sequence_number"], row["video_code"], row["title"]), (7, "old-7", "Video cũ"))
            self.assertEqual(
                migrated.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='videos_legacy'"
                ).fetchone(),
                None,
            )
        finally:
            migrated.close()

    def test_existing_project_video_schema_adds_facebook_status(self):
        self.store.close()
        path = self.root / "pre-facebook-status.sqlite3"
        connection = sqlite3.connect(path)
        project_id = str(uuid.uuid4())
        now = "2026-01-01T00:00:00+00:00"
        connection.execute(
            """CREATE TABLE projects (
                uuid TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                prefix TEXT NOT NULL, output_dir TEXT NOT NULL UNIQUE,
                notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)"""
        )
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, "Before Facebook status", "old", str(self.root / "old"), "", now, now),
        )
        connection.execute(
            """CREATE TABLE videos (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                sequence_number INTEGER NOT NULL, video_code TEXT NOT NULL,
                title TEXT NOT NULL, source_url TEXT NOT NULL,
                normalized_url TEXT NOT NULL,
                download_status TEXT NOT NULL DEFAULT 'pending',
                local_video_path TEXT, thumbnail_path TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(uuid))"""
        )
        connection.execute(
            """INSERT INTO videos
               (id, project_id, sequence_number, video_code, title, source_url,
                normalized_url, created_at, updated_at)
               VALUES ('video-1', ?, 1, 'old-1', 'Keep title', 'https://facebook.com/v',
                       'https://facebook.com/v', ?, ?)""",
            (project_id, now, now),
        )
        connection.commit()
        connection.close()

        migrated = ProjectStore(path)
        try:
            row = migrated.list_videos(project_id)[0]
            self.assertEqual(row["title"], "Keep title")
            self.assertEqual(row["facebook_uploaded"], 0)
        finally:
            migrated.close()

    def test_legacy_migration_failure_keeps_original_table_and_data(self):
        self.store.close()
        path = self.root / "broken-legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE projects (uuid TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE videos (broken_column TEXT)")
        connection.execute("INSERT INTO videos VALUES ('keep')")
        connection.commit()
        connection.close()
        with self.assertRaises(sqlite3.DatabaseError):
            ProjectStore(path)
        connection = sqlite3.connect(path)
        self.assertEqual(
            connection.execute("SELECT broken_column FROM videos").fetchone()[0],
            "keep",
        )
        connection.close()

    def test_failed_legacy_copy_rolls_back_table_rename(self):
        self.store.close()
        path = self.root / "orphan-legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute(
            """CREATE TABLE projects (
                uuid TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL,
                output_dir TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )
        connection.execute(
            """CREATE TABLE videos (
                project_uuid TEXT, sequence INTEGER, code TEXT, title TEXT, url TEXT,
                normalized_url TEXT, created_at TEXT)"""
        )
        connection.execute(
            "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "missing-project",
                1,
                "legacy-1",
                "Giữ nguyên",
                "https://www.facebook.com/watch/?v=1",
                "https://facebook.com/watch/?v=1",
                "2026-01-01",
            ),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(sqlite3.IntegrityError):
            ProjectStore(path)
        connection = sqlite3.connect(path)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(videos)")
        }
        self.assertIn("project_uuid", columns)
        self.assertEqual(
            connection.execute("SELECT title FROM videos").fetchone()[0],
            "Giữ nguyên",
        )
        self.assertIsNone(
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='videos_legacy'"
            ).fetchone()
        )
        connection.close()

    def test_name_validation_and_no_delete_api(self):
        for value in ("CON", "aux.txt", "a/b", "a.", "a ", "a:b", "x" * 121):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_windows_name(value, "Tên")
        self.assertEqual(validate_windows_name("Hợp lệ", "Tên"), "Hợp lệ")
        self.assertFalse(hasattr(self.store, "delete"))


class DatabaseTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ProjectStore(self.root / "projects.sqlite3")
        self.project_id = self.store.create(
            "Dự án", "ep1-6", str(self.root / "videos")
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    @staticmethod
    def csv_rows(rows):
        return "stt,ten_video,url\n" + "\n".join(
            f"{sequence},{title},https://www.facebook.com/watch/?v={sequence}"
            for sequence, title in rows
        )

    def test_insert_failure_rolls_back_replacement(self):
        self.store.import_csv(self.project_id, self.csv_rows([(1, "CSV A")]))
        self.store.connection.execute(
            """CREATE TRIGGER fail_second_insert BEFORE INSERT ON videos
               WHEN NEW.sequence_number=2
               BEGIN SELECT RAISE(ABORT, 'forced failure'); END"""
        )
        self.store.connection.commit()
        with self.assertRaises(ValueError):
            self.store.import_csv(
                self.project_id,
                self.csv_rows([(2, "CSV B"), (3, "CSV B-2")]),
                replace_confirmed=True,
            )
        rows = self.store.list_videos(self.project_id)
        self.assertEqual([(row["title"], row["video_code"]) for row in rows], [("CSV A", "ep1-6-1")])

    def test_replacement_requires_confirmation_and_preserves_saved_data(self):
        self.store.import_csv(self.project_id, self.csv_rows([(1, "CSV A")]))
        with self.assertRaises(ImportConfirmationRequired):
            self.store.import_csv(self.project_id, self.csv_rows([(2, "CSV B")]))
        self.assertEqual(self.store.list_videos(self.project_id)[0]["title"], "CSV A")

    def test_confirmed_replacement_updates_only_this_project(self):
        other_id = self.store.create(
            "Dự án khác", "other", str(self.root / "other-videos")
        )
        self.store.import_csv(self.project_id, self.csv_rows([(1, "CSV A")]))
        self.store.import_csv(
            self.project_id,
            self.csv_rows([(2, "CSV B")]),
            replace_confirmed=True,
        )
        self.assertEqual(
            [(row["title"], row["video_code"]) for row in self.store.list_videos(self.project_id)],
            [("CSV B", "ep1-6-2")],
        )
        self.assertEqual(self.store.list_videos(other_id), [])

    def test_replacement_refuses_downloaded_or_linked_videos(self):
        self.store.import_csv(self.project_id, self.csv_rows([(1, "CSV A")]))
        self.store.connection.execute(
            "UPDATE videos SET download_status='downloading'"
        )
        self.store.connection.commit()
        with self.assertRaises(ImportBlockedError):
            self.store.import_csv(
                self.project_id,
                self.csv_rows([(2, "CSV B")]),
                replace_confirmed=True,
            )
        self.assertEqual(self.store.list_videos(self.project_id)[0]["title"], "CSV A")

    def test_import_one_thousand_rows_and_export_exact_project_data(self):
        project_b = self.store.create(
            "Dự án B", "other", str(self.root / "other-videos")
        )
        twenty_rows = [(sequence, f"Video {sequence}") for sequence in range(1, 21)]
        self.assertEqual(
            self.store.import_csv(self.project_id, self.csv_rows(twenty_rows)),
            20,
        )
        rows = [(sequence, f"Video {sequence}") for sequence in range(1000, 0, -1)]
        imported = self.store.import_csv(
            self.project_id,
            self.csv_rows(rows),
            replace_confirmed=True,
        )
        self.assertEqual(imported, 1000)
        self.assertEqual(len(self.store.list_videos(project_b)), 0)
        exported = self.store.export_csv(self.project_id)
        self.assertTrue(exported.startswith("\ufefftitle,url\r\n"))
        parsed, errors = parse_video_csv(exported)
        self.assertEqual(errors, [])
        self.assertEqual(parsed[0]["ten_video"], "Video 1")
        self.assertIn("Video 1,https://www.facebook.com/watch/?v=1", exported)
        self.assertLess(exported.index("Video 1,"), exported.index("Video 1000,"))


class VideoTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_thousand_rows_use_one_table_model(self):
        from PySide6.QtCore import Qt
        from app.ui.widgets.video_table import VideoTable
        from PySide6.QtWidgets import QWidget

        table = VideoTable()
        empty_widget_count = len(table.findChildren(QWidget))
        table.set_videos(
            [
                {
                    "id": str(index),
                    "sequence_number": index,
                    "video_code": f"ep1-6-{index}",
                    "title": f"Video {index}",
                    "source_url": f"https://www.facebook.com/watch/?v={index}",
                }
                for index in range(1, 1001)
            ]
        )
        self.assertEqual(table.model().rowCount(), 1000)
        self.assertEqual(len(table.findChildren(QWidget)), empty_widget_count)
        first_checkbox = table.model().index(0, 0)
        self.assertTrue(
            table.model().setData(
                first_checkbox,
                Qt.CheckState.Checked.value,
                Qt.ItemDataRole.CheckStateRole,
            )
        )
        self.assertEqual(
            table.model().data(first_checkbox, Qt.ItemDataRole.CheckStateRole),
            Qt.CheckState.Checked,
        )
        table.set_text_filter("title", "Video 25")
        self.assertEqual(table.model().rowCount(), 11)
        table.set_sort("video_code", True)
        self.assertEqual(table.model().data(table.model().index(0, 2)), "ep1-6-259")
        table.resize(1366, 768)
        table.resize(1920, 1080)
        self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
