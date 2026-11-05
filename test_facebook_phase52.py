import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.connection import Database
from app.services.facebook_publishing_service import (
    FacebookPublishingPending,
    FacebookPublishingService,
)
from app.services.project_service import ProjectService
from app.services.video_service import VideoService


class _PublishingClient:
    def __init__(self, result):
        self.result = result
        self.uploaded = []

    def upload_video(self, **kwargs):
        self.uploaded.append(kwargs)
        kwargs["progress_callback"](25)
        kwargs["progress_callback"](100)
        return {"upload_id": "remote-1"}

    def wait_for_processing(self, page_id, upload_id):
        return self.result


class FacebookPhase52Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite3")
        self.project_id = ProjectService(self.database).create(
            "Project", "p", str(Path(self.temp.name) / "videos")
        )
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
        self.database.close()
        self.temp.cleanup()

    def test_success_persists_remote_id_url_and_progress(self):
        job_id = self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")
        client = _PublishingClient({"id": "post-1", "permalink_url": "https://facebook.com/post-1"})
        self.service.client = client
        progress = []
        with patch.object(self.service, "_inspect_video", return_value={"streams": [{"codec_type": "video"}]}):
            result = self.service.publish(job_id, progress=progress.append)
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["published_post_id"], "post-1")
        self.assertEqual(result["published_url"], "https://facebook.com/post-1")
        self.assertEqual(progress, [25, 100, 100])

    def test_processing_pending_is_not_marked_published(self):
        job_id = self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")
        self.service.client = _PublishingClient({"status": {"video_status": "processing"}, "id": "remote-1"})
        with patch.object(self.service, "_inspect_video", return_value={"streams": [{"codec_type": "video"}]}):
            with self.assertRaises(FacebookPublishingPending):
                self.service.publish(job_id)
        status = self.service.list_jobs(self.project_id)[0]["status"]
        self.assertEqual(status, "PUBLISHING")

    def test_active_duplicate_is_rejected_before_second_upload(self):
        self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")
        with self.assertRaises(Exception):
            self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")


if __name__ == "__main__":
    unittest.main()
