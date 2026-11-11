import tempfile
import unittest
from pathlib import Path

from app.core.exceptions import CsvValidationError, VideoImportValidationError
from app.database.connection import Database
from app.services.project_service import ProjectService
from app.services.video_service import VideoService


class ImportUrlUniquenessTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database = Database(root / "project.sqlite3")
        projects = ProjectService(self.database)
        self.project_id = projects.create("Project one", "phim-hay-review-09-11-2026", str(root / "one"))
        self.other_project_id = projects.create("Project two", "other", str(root / "two"))
        self.service = VideoService(self.database)

    def tearDown(self):
        self.database.close()
        self.temporary_directory.cleanup()

    def import_rows(self, project_id, rows, *, append=False):
        body = "\n".join(f"{stt},{title},{url}" for stt, title, url in rows)
        return self.service.import_csv(project_id, f"stt,title,url\n{body}\n", append=append)

    def test_saved_canonical_url_reports_row_url_and_saved_video(self):
        self.import_rows(self.project_id, [(1, "Saved", "https://youtu.be/Gs3JP2b6Q-g")])
        with self.assertRaises(VideoImportValidationError) as raised:
            self.import_rows(
                self.project_id,
                [(1, "New", "https://www.youtube.com/watch?v=Gs3JP2b6Q-g")],
                append=True,
            )
        self.assertIn("Dòng nhập: 1", str(raised.exception))
        self.assertIn("URL: https://www.youtube.com/watch?v=Gs3JP2b6Q-g", str(raised.exception))
        self.assertIn("Trùng với video ID: phim-hay-review-09-11-2026-1", str(raised.exception))
        self.assertIn("STT đã lưu: 1", str(raised.exception))
        self.import_rows(
            self.project_id,
            [(1, "Corrected", "https://www.youtube.com/watch?v=Gs3JP2b6Q-h")],
            append=True,
        )
        self.assertEqual(len(self.database.list_videos(self.project_id)), 2)
    def test_duplicate_urls_in_input_report_first_and_duplicate_rows(self):
        with self.assertRaises(CsvValidationError) as raised:
            self.import_rows(
                self.project_id,
                [
                    (1, "One", "https://www.instagram.com/reel/ABC123/?igsh=test"),
                    (2, "Two", "https://instagram.com/reel/ABC123/"),
                ],
                append=True,
            )
        self.assertIn("Dòng nhập: 2", str(raised.exception))
        self.assertIn("Trùng với dòng nhập: 1", str(raised.exception))

    def test_other_project_and_same_title_are_allowed(self):
        url = "https://www.facebook.com/watch/?v=647537299265662"
        self.import_rows(self.project_id, [(1, "Same title", url)])
        self.import_rows(self.other_project_id, [(1, "Same title", url)])
        self.import_rows(
            self.project_id,
            [(1, "Same title", "https://www.facebook.com/watch/?v=647537299265663")],
            append=True,
        )
        self.assertEqual(len(self.database.list_videos(self.project_id)), 2)
        self.assertEqual(len(self.database.list_videos(self.other_project_id)), 1)

    def test_duplicate_input_stt_uses_new_sequence_and_code(self):
        self.import_rows(self.project_id, [(1, "Saved", "https://www.facebook.com/watch/?v=1")])
        self.import_rows(
            self.project_id,
            [(1, "New", "https://www.facebook.com/watch/?v=2")],
            append=True,
        )
        saved = self.database.list_videos(self.project_id)
        self.assertEqual((saved[1]["sequence_number"], saved[1]["video_code"]), (2, "phim-hay-review-09-11-2026-2"))

    def test_many_saved_duplicates_do_not_partially_write(self):
        self.import_rows(
            self.project_id,
            [
                (1, "Saved one", "https://www.facebook.com/watch/?v=1"),
                (2, "Saved two", "https://www.facebook.com/watch/?v=2"),
            ],
        )
        with self.assertRaises(VideoImportValidationError) as raised:
            self.import_rows(
                self.project_id,
                [
                    (1, "Duplicate one", "https://www.facebook.com/watch/?v=1"),
                    (2, "Duplicate two", "https://www.facebook.com/watch/?v=2"),
                    (3, "Valid", "https://www.facebook.com/watch/?v=3"),
                ],
                append=True,
            )
        self.assertEqual(len(raised.exception.errors), 2)
        self.assertEqual(len(self.database.list_videos(self.project_id)), 2)
