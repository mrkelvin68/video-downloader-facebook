import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.database.connection import Database
from app.services.facebook_publishing_service import FacebookPublishingError, FacebookPublishingService
from app.services.project_service import ProjectService
from app.services.video_service import VideoService


class FacebookSchedulingDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite3")
        self.project_id = ProjectService(self.database).create("Project", "p", str(Path(self.temp.name) / "videos"))
        VideoService(self.database).import_csv(
            self.project_id, "stt,title,url\n1,Video title,https://www.facebook.com/watch/?v=1"
        )
        self.video_id = self.database.list_videos(self.project_id)[0]["id"]
        self.video_path = Path(self.temp.name) / "videos" / "video.mp4"
        self.video_path.write_bytes(b"video")
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE videos SET local_video_path=? WHERE id=?", (str(self.video_path), self.video_id)
            )
        self.service = FacebookPublishingService(self.database)
        self.inspect_patcher = patch.object(self.service, "_inspect_video", return_value={"streams": [{"codec_type": "video"}]})
        self.inspect_patcher.start()
        self.service.save_pages(self.project_id, [{"id": "page-1", "name": "Page 1"}], "page-1")
        self.service.save_draft(
            self.project_id, self.video_id, page_id="page-1", facebook_title="FB title",
            description="Caption", hashtags="#tag",
        )
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE facebook_drafts SET preparation_status='READY' WHERE project_id=? AND video_id=?",
                (self.project_id, self.video_id),
            )

    def tearDown(self):
        self.inspect_patcher.stop()
        self.database.close()
        self.temp.cleanup()

    def test_schedule_is_persisted_as_local_utc_job(self):
        scheduled_for = datetime.now(UTC) + timedelta(days=1)
        job_id = self.service.schedule_from_draft(
            self.project_id, self.video_id, "page-1", scheduled_for=scheduled_for
        )

        job = self.service.get_job(job_id)
        self.assertEqual(job["id"], job_id)
        self.assertEqual(job["publish_mode"], "SCHEDULED")
        self.assertEqual(job["schedule_provider"], "LOCAL")
        self.assertEqual(job["schedule_state"], "LOCAL_WAITING")
        self.assertEqual(job["status"], "SCHEDULED")
        self.assertEqual(job["scheduled_at"], scheduled_for.isoformat(timespec="seconds"))
        self.assertEqual(job["timezone"], "Asia/Ho_Chi_Minh")

    def test_reschedule_keeps_the_same_job_and_cancel_is_safe(self):
        job_id = self.service.schedule_from_draft(
            self.project_id, self.video_id, "page-1", scheduled_for=datetime.now(UTC) + timedelta(days=1)
        )
        updated = self.service.reschedule(
            job_id, scheduled_for=datetime.now(UTC) + timedelta(days=2)
        )
        self.assertEqual(updated["id"], job_id)
        self.assertEqual(updated["schedule_state"], "LOCAL_WAITING")
        self.service.cancel(job_id)
        self.assertEqual(self.service.get_job(job_id)["status"], "CANCELLED")
        self.assertEqual(self.service.get_job(job_id)["schedule_state"], "CANCELLED")

    def test_active_schedule_duplicate_and_invalid_time_are_rejected(self):
        self.service.schedule_from_draft(
            self.project_id, self.video_id, "page-1", scheduled_for=datetime.now(UTC) + timedelta(days=1)
        )
        with self.assertRaises(FacebookPublishingError):
            self.service.schedule_from_draft(
                self.project_id, self.video_id, "page-1", scheduled_for=datetime.now(UTC) + timedelta(days=2)
            )
        with self.assertRaisesRegex(ValueError, "tương lai"):
            self.service.schedule_from_draft(
                self.project_id, self.video_id, "page-1", scheduled_for=datetime.now(UTC) - timedelta(minutes=1)
            )

    def test_migration_keeps_existing_jobs_and_adds_schedule_fields(self):
        self.database.close()
        path = Path(self.temp.name) / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute(
            """CREATE TABLE facebook_publishing_jobs (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, video_id TEXT NOT NULL,
                page_id TEXT NOT NULL, local_file_path TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '', scheduled_at TEXT, timezone TEXT NOT NULL DEFAULT 'UTC',
                status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
                remote_upload_id TEXT, published_post_id TEXT, published_url TEXT, last_error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO facebook_publishing_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy-job", "project", "video", "page", "C:/video.mp4", "Title", "Caption",
             "2030-01-01T00:00:00+00:00", "UTC", "SCHEDULED", 0, 0, None, None, None, None,
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        connection.commit()
        connection.close()

        migrated = Database(path)
        row = migrated.connection.execute(
            "SELECT id,title,description,publish_mode,schedule_provider,schedule_state FROM facebook_publishing_jobs"
        ).fetchone()
        migrated.close()
        self.assertEqual(tuple(row), ("legacy-job", "Title", "Caption", "SCHEDULED", "LOCAL", "LOCAL_WAITING"))


if __name__ == "__main__":
    unittest.main()
