import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.connection import Database
from app.services.download_queue_service import DownloadQueueService
from app.services.project_service import ProjectService
from app.services.video_service import VideoService


class ProjectDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "projects.sqlite3"
        self.database = Database(self.database_path)
        self.projects = ProjectService(self.database)
        self.project_path = self.root / "project-video-files"
        self.project_id = self.projects.create(
            "Project", "project", str(self.project_path)
        )
        self.other_path = self.root / "other-video-files"
        self.other_id = self.projects.create(
            "Other", "other", str(self.other_path)
        )
        VideoService(self.database).import_csv(
            self.project_id,
            "stt,title,url\n1,Video,https://www.facebook.com/reel/1\n",
        )
        VideoService(self.database).import_csv(
            self.other_id,
            "stt,title,url\n1,Other,https://www.facebook.com/reel/2\n",
        )
        self.video_id = self.database.list_videos(self.project_id)[0]["id"]

    def tearDown(self):
        self.database.close()
        self.temp.cleanup()

    def _add_matrix_data(self):
        connection = self.database.connection
        now = "2026-11-04T00:00:00+00:00"
        with connection:
            connection.execute(
                """INSERT INTO matrices
                   (matrix_id, project_id, name, start_date, day_count,
                    repeat_mode, created_at, updated_at)
                   VALUES ('matrix', ?, 'Matrix', '2026-11-04', 1,
                           'no_repeat', ?, ?)""",
                (self.project_id, now, now),
            )
            connection.execute(
                """INSERT INTO matrix_pages (page_id, matrix_id, name, position)
                   VALUES ('page', 'matrix', 'Page', 0)"""
            )
            connection.execute(
                """INSERT INTO matrix_slots
                   (slot_id, matrix_id, scheduled_time, position)
                   VALUES ('slot', 'matrix', '09:00', 0)"""
            )
            connection.execute(
                """INSERT INTO matrix_entries
                   (entry_id, matrix_id, project_id, page_id, video_id,
                    scheduled_date, scheduled_time, created_at, updated_at)
                   VALUES ('entry', 'matrix', ?, 'page', ?, '2026-11-04',
                           '09:00', ?, ?)""",
                (self.project_id, self.video_id, now, now),
            )
            connection.execute(
                """INSERT INTO matrix_undo_snapshots
                   (matrix_id, snapshot_json, updated_at)
                   VALUES ('matrix', '{}', ?)""",
                (now,),
            )

    def test_metadata_delete_preserves_files_and_cleans_related_rows_after_restart(self):
        marker = self.project_path / "keep.mp4"
        marker.write_bytes(b"keep")
        DownloadQueueService(self.database).enqueue(
            self.project_id, [self.video_id]
        )
        self._add_matrix_data()

        result = self.projects.delete(self.project_id)

        self.assertTrue(result.metadata_deleted)
        self.assertFalse(result.files_deleted)
        self.assertEqual(marker.read_bytes(), b"keep")
        self.assertIsNone(self.database.get_project(self.project_id))
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM videos WHERE project_id=?",
                (self.project_id,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM download_jobs WHERE project_id=?",
                (self.project_id,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM matrices WHERE project_id=?",
                (self.project_id,),
            ).fetchone()[0],
            0,
        )
        for table in (
            "matrix_pages",
            "matrix_slots",
            "matrix_entries",
            "matrix_undo_snapshots",
        ):
            self.assertEqual(
                self.database.connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE matrix_id='matrix'"
                ).fetchone()[0],
                0,
            )
        self.assertEqual(
            self.database.connection.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )
        self.assertIsNotNone(self.database.get_project(self.other_id))
        self.assertEqual(len(self.database.list_videos(self.other_id)), 1)

        self.database.close()
        self.database = Database(self.database_path)
        self.assertEqual(self.database.list_projects()[0]["uuid"], self.other_id)
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_physical_delete_removes_only_selected_project_directory(self):
        marker = self.project_path / "download" / "video.mp4"
        marker.parent.mkdir()
        marker.write_bytes(b"project")
        other_marker = self.other_path / "download" / "other.mp4"
        other_marker.parent.mkdir()
        other_marker.write_bytes(b"other")

        result = self.projects.delete(self.project_id, delete_files=True)

        self.assertTrue(result.metadata_deleted)
        self.assertTrue(result.files_deleted)
        self.assertFalse(self.project_path.exists())
        self.assertEqual(other_marker.read_bytes(), b"other")
        self.assertIsNotNone(self.database.get_project(self.other_id))
        self.assertEqual(self.database.list_videos(self.other_id)[0]["title"], "Other")

    def test_project_with_downloading_job_is_not_deleted(self):
        queue = DownloadQueueService(self.database)
        job_id = queue.enqueue(self.project_id, [self.video_id])[0]
        queue.claim(job_id)
        marker = self.project_path / "keep.mp4"
        marker.write_bytes(b"keep")

        with self.assertRaisesRegex(ValueError, "dừng"):
            self.projects.delete(self.project_id, delete_files=True)

        self.assertIsNotNone(self.database.get_project(self.project_id))
        self.assertEqual(queue.get_job(job_id)["status"], "downloading")
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_database_failure_rolls_back_metadata_and_restores_directory(self):
        marker = self.project_path / "keep.mp4"
        marker.write_bytes(b"keep")
        DownloadQueueService(self.database).enqueue(
            self.project_id, [self.video_id]
        )
        self.database.connection.execute(
            """CREATE TRIGGER reject_project_delete
               BEFORE DELETE ON projects
               BEGIN SELECT RAISE(ABORT, 'delete rejected'); END"""
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.projects.delete(self.project_id, delete_files=True)

        self.assertIsNotNone(self.database.get_project(self.project_id))
        self.assertEqual(len(self.database.list_videos(self.project_id)), 1)
        self.assertEqual(
            len(
                DownloadQueueService(self.database).list_jobs(
                    include_finished=False
                )
            ),
            1,
        )
        self.assertEqual(marker.read_bytes(), b"keep")
        self.assertFalse(
            any(self.root.glob(".project-video-files.delete-*"))
        )

    def test_file_cleanup_failure_reports_metadata_deleted_and_keeps_staged_files(self):
        marker = self.project_path / "keep.mp4"
        marker.write_bytes(b"keep")

        with patch(
            "app.services.project_service.shutil.rmtree",
            side_effect=PermissionError("access denied"),
        ):
            result = self.projects.delete(self.project_id, delete_files=True)

        self.assertTrue(result.metadata_deleted)
        self.assertFalse(result.files_deleted)
        self.assertEqual(result.cleanup_error, "access denied")
        self.assertIsNotNone(result.remaining_files_path)
        self.assertEqual(
            (Path(result.remaining_files_path) / "keep.mp4").read_bytes(),
            b"keep",
        )
        self.assertIsNone(self.database.get_project(self.project_id))

    def test_dangerous_root_and_overlapping_project_paths_are_rejected(self):
        self.database.connection.execute(
            "UPDATE projects SET output_dir=? WHERE uuid=?",
            (Path.home().anchor, self.project_id),
        )
        with self.assertRaisesRegex(ValueError, "thư mục gốc"):
            self.projects.delete(self.project_id, delete_files=True)
        self.assertIsNotNone(self.database.get_project(self.project_id))

        shared = self.root / "shared"
        shared.mkdir()
        nested = shared / "project"
        nested.mkdir()
        self.database.connection.execute(
            "UPDATE projects SET output_dir=? WHERE uuid=?",
            (str(shared), self.project_id),
        )
        self.database.connection.execute(
            "UPDATE projects SET output_dir=? WHERE uuid=?",
            (str(nested), self.other_id),
        )
        with self.assertRaisesRegex(ValueError, "phạm vi"):
            self.projects.delete(self.project_id, delete_files=True)
        self.assertTrue(nested.is_dir())

    def test_project_root_symlink_is_rejected_without_touching_target(self):
        target = self.root / "external"
        target.mkdir()
        marker = target / "outside.mp4"
        marker.write_bytes(b"outside")
        original = self.project_path.with_name("original-project")
        self.project_path.rename(original)
        try:
            self.project_path.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Directory symlink unavailable: {exc}")
        self.database.connection.execute(
            "UPDATE projects SET output_dir=? WHERE uuid=?",
            (str(self.project_path), self.project_id),
        )

        with self.assertRaisesRegex(ValueError, "symlink/junction"):
            self.projects.delete(self.project_id, delete_files=True)

        self.assertTrue(self.project_path.is_symlink())
        self.assertEqual(marker.read_bytes(), b"outside")
        self.assertIsNotNone(self.database.get_project(self.project_id))

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_junction_inside_project_is_rejected(self):
        target = self.root / "external"
        target.mkdir()
        marker = target / "outside.mp4"
        marker.write_bytes(b"outside")
        junction = self.project_path / "junction"
        result = subprocess.run(
            f'mklink /J "{junction}" "{target}"',
            shell=True,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            self.skipTest(f"Could not create junction: {result.stderr}")

        with self.assertRaisesRegex(ValueError, "symlink/junction"):
            self.projects.delete(self.project_id, delete_files=True)

        self.assertEqual(marker.read_bytes(), b"outside")
        self.assertTrue(junction.exists())


if __name__ == "__main__":
    unittest.main()
