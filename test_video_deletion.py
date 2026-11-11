import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from app.database.connection import Database
from app.services.download_queue_service import DownloadQueueService
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.ui.dialogs.video_delete_dialog import VideoDeleteDialog
from app.ui.main_window import MainWindow


class VideoDeletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "projects.sqlite3"
        self.database = Database(self.database_path)
        self.projects = ProjectService(self.database)
        self.videos = VideoService(self.database)
        self.project_path = self.root / "project-videos"
        self.project_id = self.projects.create(
            "Project Test", "test", str(self.project_path)
        )
        self.videos.import_csv(
            self.project_id,
            "stt,title,url\n"
            "1,Video 1,https://www.facebook.com/reel/1\n"
            "2,Video 2,https://www.facebook.com/reel/2\n"
            "3,Video 3,https://www.facebook.com/reel/3\n",
        )
        self.video_rows = self.database.list_videos(self.project_id)
        self.video_ids = [v["id"] for v in self.video_rows]

    def tearDown(self):
        self.database.close()
        self.temp.cleanup()

    def test_delete_videos_database_only_keeps_local_files(self):
        # Create mock local files
        video_dir = self.project_path / "test-1"
        video_dir.mkdir(parents=True, exist_ok=True)
        local_mp4 = video_dir / "test-1.mp4"
        local_mp4.write_bytes(b"video-1-data")
        thumb = video_dir / "thumbnail.jpg"
        thumb.write_bytes(b"thumb-data")

        with self.database.connection:
            self.database.connection.execute(
                "UPDATE videos SET local_video_path=?, thumbnail_path=? WHERE id=?",
                (str(local_mp4), str(thumb), self.video_ids[0]),
            )

        # Also add a queue job
        queue = DownloadQueueService(self.database)
        queue.enqueue(self.project_id, [self.video_ids[0]])

        # Delete database only
        deleted_count = self.videos.delete_videos(
            self.project_id, [self.video_ids[0]], delete_local_files=False
        )
        self.assertEqual(deleted_count, 1)

        # Database rows removed
        remaining = self.database.list_videos(self.project_id)
        self.assertEqual(len(remaining), 2)
        self.assertNotIn(self.video_ids[0], [v["id"] for v in remaining])

        # Related queue jobs removed
        self.assertEqual(self.database.connection.execute(
            "SELECT COUNT(*) FROM download_jobs WHERE video_id=?", (self.video_ids[0],)
        ).fetchone()[0], 0)

        # Local files on disk are PRESERVED
        self.assertTrue(local_mp4.is_file())
        self.assertTrue(thumb.is_file())
        self.assertTrue(video_dir.is_dir())

    def test_delete_videos_with_local_files_removes_both(self):
        video_dir = self.project_path / "test-2"
        video_dir.mkdir(parents=True, exist_ok=True)
        local_mp4 = video_dir / "test-2.mp4"
        local_mp4.write_bytes(b"video-2-data")
        thumb = video_dir / "thumbnail.jpg"
        thumb.write_bytes(b"thumb-data")

        with self.database.connection:
            self.database.connection.execute(
                "UPDATE videos SET local_video_path=?, thumbnail_path=? WHERE id=?",
                (str(local_mp4), str(thumb), self.video_ids[1]),
            )

        # Delete with local files
        deleted_count = self.videos.delete_videos(
            self.project_id, [self.video_ids[1]], delete_local_files=True
        )
        self.assertEqual(deleted_count, 1)

        # Database rows removed
        remaining = self.database.list_videos(self.project_id)
        self.assertEqual(len(remaining), 2)
        self.assertNotIn(self.video_ids[1], [v["id"] for v in remaining])

        # Local files and folder REMOVED
        self.assertFalse(local_mp4.exists())
        self.assertFalse(thumb.exists())
        self.assertFalse(video_dir.exists())

    def test_dialog_options_and_selection(self):
        # When some selected
        dialog = VideoDeleteDialog("Project Test", selected_count=2, total_count=3)
        self.assertTrue(dialog.scope_selected_radio.isChecked())
        self.assertFalse(dialog.is_delete_all())
        self.assertTrue(dialog.mode_db_only_radio.isChecked())
        self.assertFalse(dialog.is_delete_local_files())

        # Switch to delete all and delete local files
        dialog.scope_all_radio.setChecked(True)
        dialog.mode_db_and_local_radio.setChecked(True)
        self.assertTrue(dialog.is_delete_all())
        self.assertTrue(dialog.is_delete_local_files())

        # When 0 selected initially
        dialog_zero = VideoDeleteDialog("Project Test", selected_count=0, total_count=3)
        self.assertTrue(dialog_zero.scope_all_radio.isChecked())
        self.assertTrue(dialog_zero.is_delete_all())
        self.assertFalse(dialog_zero.scope_selected_radio.isEnabled())

    def test_main_window_delete_selected_videos(self):
        window = MainWindow(self.database, auto_resume=False, enable_tray=False)
        project = self.projects.list_projects()[0]
        window.show_detail(project)

        # Check video 1
        window.detail_page.video_table.video_model.setData(
            window.detail_page.video_table.video_model.index(0, 0),
            True,
            role=VideoDeleteDialog.is_delete_all.__class__,  # any edit role
        )
        window.detail_page.video_table.video_model._checked = {self.video_ids[0]}

        # Test canceling dialog
        with patch.object(VideoDeleteDialog, "exec", return_value=VideoDeleteDialog.DialogCode.Rejected):
            window.delete_selected_videos()
        self.assertEqual(len(self.database.list_videos(self.project_id)), 3)

        # Test accepting DB-only deletion
        with (
            patch.object(VideoDeleteDialog, "exec", return_value=VideoDeleteDialog.DialogCode.Accepted),
            patch.object(VideoDeleteDialog, "is_delete_all", return_value=False),
            patch.object(VideoDeleteDialog, "is_delete_local_files", return_value=False),
            patch.object(QMessageBox, "information"),
        ):
            window.delete_selected_videos()

        self.assertEqual(len(self.database.list_videos(self.project_id)), 2)
        window.close()


if __name__ == "__main__":
    unittest.main()
