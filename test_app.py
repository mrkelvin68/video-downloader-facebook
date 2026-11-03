import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app import ProjectStore, validate_windows_name, validate_output_dir


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


if __name__ == "__main__":
    unittest.main()
